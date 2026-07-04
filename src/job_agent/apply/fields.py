"""Immutable value types shared across the assisted-apply pipeline.

A run is a sequence of pure transformations over these frozen dataclasses:

    read_form(page)      -> tuple[FormField, ...]     (form_reader)
    build_fill_plan(...)  -> FillPlan                  (filler)
    request_approval(...) -> Decision                  (review)
    run_submit(...)       -> SubmitResult              (submit)

Keeping the data immutable means a value can never be mutated on its way to a
real employer, and every stage is a testable pure function over plain data —
no live browser required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FieldType(str, Enum):
    """What kind of control a form field is — drives how it's filled."""

    TEXT = "text"          # single-line text / email / tel / number / url
    TEXTAREA = "textarea"  # free-text (often custom questions)
    SELECT = "select"      # native dropdown with fixed options
    COMBOBOX = "combobox"  # ARIA widget (role=combobox/listbox) — filled by CLICK
    CHECKBOX = "checkbox"
    RADIO = "radio"
    FILE = "file"          # resume / document upload
    EEO = "eeo"            # self-identification (gender/race/veteran/disability)
    CREDENTIAL = "credential"  # password / account creation — NEVER auto-filled
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FormField:
    """One enumerated control on the application form."""

    selector: str                       # CSS selector to locate it on the page
    field_type: FieldType
    label: str = ""                     # human-visible label / question text
    name: str = ""                      # the control's name/id attribute
    required: bool = False
    options: tuple[str, ...] = ()       # for SELECT / RADIO
    max_length: int | None = None       # the control's maxlength, if declared

    def describe(self) -> str:
        req = " (required)" if self.required else ""
        return f"{self.label or self.name or self.selector}{req}"


@dataclass(frozen=True)
class PlannedFill:
    """A field we will fill, with the value and where it came from."""

    field: FormField
    value: str
    source: str          # e.g. "answer_bank.salary_expectation", "resume", "career_facts.email"


@dataclass(frozen=True)
class Unfilled:
    """A field we deliberately left empty, with the reason (never guessed)."""

    field: FormField
    reason: str


@dataclass(frozen=True)
class FillPlan:
    """The complete, immutable plan of what will (and won't) be submitted."""

    planned: tuple[PlannedFill, ...] = ()
    unfilled: tuple[Unfilled, ...] = ()

    def missing_required(self) -> tuple[Unfilled, ...]:
        """Unfilled fields that the form marks required — these block approval."""
        return tuple(u for u in self.unfilled if u.field.required)

    def with_value(self, selector: str, value: str, source: str = "manual (edit)") -> "FillPlan":
        """Return a new plan with ``selector`` filled to ``value``.

        Moves the field from ``unfilled`` to ``planned`` (or updates an existing
        planned value). Immutable — the original plan is untouched.
        """
        target = next(
            (u.field for u in self.unfilled if u.field.selector == selector),
            next((p.field for p in self.planned if p.field.selector == selector), None),
        )
        if target is None:
            raise KeyError(f"No field with selector {selector!r} in this plan.")
        planned = tuple(p for p in self.planned if p.field.selector != selector)
        unfilled = tuple(u for u in self.unfilled if u.field.selector != selector)
        return FillPlan(
            planned=planned + (PlannedFill(field=target, value=value, source=source),),
            unfilled=unfilled,
        )
