"""Read an application form into a list of typed :class:`FormField`.

``classify_control`` is a pure function (tag/type/label → :class:`FieldType`) and
is unit-tested directly. ``read_form`` is the only Playwright-touching part: it
runs one JS scan of the page's form controls and classifies each. Keeping the
extraction in a single ``page.evaluate`` call keeps the browser round-trips down
and the Python side pure.
"""

from __future__ import annotations

from job_agent.apply.fields import FieldType, FormField

_EEO_WORDS = ("gender", "sex", "race", "ethnic", "veteran", "disab", "hispanic", "latino")


def _is_eeo(label: str, name: str) -> bool:
    hay = f"{label} {name}".lower()
    return any(w in hay for w in _EEO_WORDS)


def classify_control(tag: str, input_type: str, label: str = "", name: str = "",
                     has_options: bool = False) -> FieldType:
    """Pure: map a raw control descriptor to a :class:`FieldType`."""
    tag = tag.lower()
    input_type = (input_type or "").lower()
    if tag == "textarea":
        return FieldType.TEXTAREA
    if tag == "select":
        return FieldType.EEO if _is_eeo(label, name) else FieldType.SELECT
    if tag in ("combobox", "listbox"):
        # ARIA widget (React select) — selected by clicking its option, and its
        # option list may be empty at scan time (popup renders on open).
        return FieldType.COMBOBOX
    if tag == "buttonpair":
        # Yes/No button pair (Ashby toggle) — answered by clicking one button.
        # Deliberately never EEO: an EEO-worded pair falls through the filler's
        # text matchers unmatched and pauses, rather than risk a mis-click.
        return FieldType.TOGGLE
    if input_type == "password":
        return FieldType.CREDENTIAL
    if input_type == "file":
        return FieldType.FILE
    if input_type == "checkbox":
        return FieldType.CHECKBOX
    if input_type == "radio":
        return FieldType.RADIO
    if input_type in ("", "text", "email", "tel", "url", "number", "search", "date"):
        return FieldType.EEO if _is_eeo(label, name) else FieldType.TEXT
    return FieldType.UNKNOWN


# One-shot DOM scan. Returns a plain array of control descriptors we classify in
# Python. Label resolution: <label for=id>, wrapping <label>, aria-label, then
# placeholder. Skips hidden/submit/button controls.
_SCAN_JS = r"""
() => {
  const out = [];
  const controls = document.querySelectorAll('input, select, textarea');
  const labelFor = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return l.innerText.trim();
    }
    const wrap = el.closest('label');
    if (wrap) return wrap.innerText.trim();
    const lb = el.getAttribute('aria-labelledby');           // ARIA widgets
    if (lb) {
      const t = lb.split(/\s+/)
        .map((i) => (document.getElementById(i) || {}).innerText || '')
        .join(' ').trim();
      if (t) return t;
    }
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    // widget libraries (Ashby) often put the label elsewhere in the field
    // container — walk a few ancestors looking for one. Placeholder text
    // ("Start typing...") is the LAST resort: it names the widget, not the field.
    let anc = el.parentElement;
    for (let i = 0; i < 4 && anc; i++) {
      const l = anc.querySelector('label');
      if (l && l.innerText.trim()) return l.innerText.trim();
      anc = anc.parentElement;
    }
    return el.getAttribute('placeholder') || '';
  };
  const roleSelector = (el, role) => {
    // Playwright's :nth-match — addresses widgets with no id/name at all
    const all = Array.from(document.querySelectorAll(`[role="${role}"]`));
    return `:nth-match([role="${role}"], ${all.indexOf(el) + 1})`;
  };
  const selectorFor = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'combobox' || role === 'listbox') return roleSelector(el, role);
    return '';
  };
  controls.forEach((el) => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) return;
    if (el.offsetParent === null && type !== 'file') return;  // skip hidden
    const options = el.tagName.toLowerCase() === 'select'
      ? Array.from(el.options).map(o => o.text.trim()).filter(t => t && !/^select/i.test(t))
      : [];
    const fieldset = el.closest('fieldset');
    const legend = fieldset ? fieldset.querySelector('legend') : null;
    const role = (el.getAttribute('role') || '').toLowerCase();
    out.push({
      // an <input role="combobox"> (Ashby) is a click-select widget, not a text box
      tag: role === 'combobox' ? 'combobox' : el.tagName.toLowerCase(),
      type,
      name: el.getAttribute('name') || el.id || '',
      label: labelFor(el),
      groupLabel: legend ? legend.innerText.trim() : '',
      required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true',
      maxlength: el.getAttribute('maxlength'),
      options,
      selector: selectorFor(el),
    });
  });
  // ARIA widgets (React selects): role=combobox / role=listbox on non-native
  // elements. Their [role=option] children may live inside the widget or in a
  // popup referenced by aria-controls/aria-owns — and may be absent entirely
  // until the widget is opened (options then resolve at click time).
  document.querySelectorAll('[role="combobox"], [role="listbox"]').forEach((el) => {
    if (['input', 'select', 'textarea'].includes(el.tagName.toLowerCase())) return; // native pass
    if (el.offsetParent === null) return;
    let opts = Array.from(el.querySelectorAll('[role="option"]'))
      .map((o) => o.innerText.trim()).filter(Boolean);
    const owns = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
    if (!opts.length && owns) {
      const pop = document.getElementById(owns);
      if (pop) opts = Array.from(pop.querySelectorAll('[role="option"]'))
        .map((o) => o.innerText.trim()).filter(Boolean);
    }
    out.push({
      tag: el.getAttribute('role').toLowerCase(),
      type: '',
      name: el.id || '',
      label: labelFor(el),
      groupLabel: '',
      required: el.getAttribute('aria-required') === 'true',
      maxlength: null,
      options: opts,
      selector: el.id ? '#' + CSS.escape(el.id)
                      : roleSelector(el, el.getAttribute('role').toLowerCase()),
    });
  });
  // Yes/No BUTTON pairs (Ashby toggles): the question renders as two sibling
  // <button>s reading "Yes" / "No" — no input element at all, so the passes
  // above never see it. A container whose button children are exactly one
  // "Yes" and one "No" is one question; its label resolves like any field's.
  // Selector durability matters here: Ashby re-renders the whole form after a
  // resume upload, recreating these nodes. Anchor to Ashby's own
  // data-field-path (its stable field key, verified to survive the re-render)
  // or an id; a stamped attribute is the LAST resort — a re-render wipes it.
  let toggleIdx = 0;
  const pairSeen = new Set();
  document.querySelectorAll('button').forEach((btn) => {
    const parent = btn.parentElement;
    if (!parent || pairSeen.has(parent)) return;
    if (btn.offsetParent === null) return;
    const btns = Array.from(parent.children).filter((c) => c.tagName === 'BUTTON');
    const texts = btns.map((b) => b.innerText.trim());
    const lower = texts.map((t) => t.toLowerCase());
    if (btns.length !== 2 || !lower.includes('yes') || !lower.includes('no')) return;
    pairSeen.add(parent);
    const entry = parent.closest('[data-field-path]');
    let selector;
    if (entry) {
      selector = `[data-field-path="${entry.getAttribute('data-field-path')}"]`;
    } else if (parent.id) {
      selector = '#' + CSS.escape(parent.id);
    } else {
      toggleIdx += 1;
      parent.setAttribute('data-ja-toggle', String(toggleIdx));
      selector = `[data-ja-toggle="${toggleIdx}"]`;
    }
    out.push({
      tag: 'buttonpair',
      type: '',
      name: parent.id || '',
      label: labelFor(parent),
      groupLabel: '',
      required: parent.getAttribute('aria-required') === 'true',
      maxlength: null,
      options: texts,
      selector,
    });
  });
  return out;
}
"""


def _single_field(r: dict) -> FormField:
    options = tuple(r["options"])
    field_type = classify_control(r["tag"], r["type"], r["label"], r["name"], bool(options))
    return FormField(
        selector=r["selector"],
        field_type=field_type,
        label=r["label"],
        name=r["name"],
        required=bool(r["required"]),
        options=options,
        max_length=int(r["maxlength"]) if r.get("maxlength") else None,
    )


def _grouped_field(name: str, members: list[dict]) -> FormField:
    """Collapse same-name radio/checkbox controls into ONE field.

    A 30-country radio picker is one question with 30 options — not 30 required
    fields. Each member's own label becomes an option; the group label is the
    fieldset legend (falling back to the shared name); required if any member is.
    """
    label = next((m["groupLabel"] for m in members if m.get("groupLabel")), "") or name
    options = tuple(m["label"] for m in members if m["label"])
    field_type = classify_control(members[0]["tag"], members[0]["type"], label, name,
                                  bool(options))
    return FormField(
        selector=f'input[name="{name}"]',
        field_type=field_type,
        label=label,
        name=name,
        required=any(m["required"] for m in members),
        options=options,
    )


def read_form(page) -> tuple[FormField, ...]:
    """Enumerate and classify every fillable control on the current page.

    Same-name radio/checkbox controls are grouped into a single field with one
    option per control (see :func:`_grouped_field`); everything else maps 1:1.
    """
    raw = page.evaluate(_SCAN_JS)
    fields: list[FormField] = []
    groups: dict[str, list[dict]] = {}
    order: list[tuple[str, str | dict]] = []   # ("single", r) | ("group", name)
    for r in raw:
        if not r["selector"]:
            continue  # unaddressable control — skip rather than guess a selector
        if r["type"] in ("radio", "checkbox") and r["name"]:
            if r["name"] not in groups:
                order.append(("group", r["name"]))
            groups.setdefault(r["name"], []).append(r)
        else:
            order.append(("single", r))
    for kind, item in order:
        fields.append(_grouped_field(item, groups[item]) if kind == "group"
                      else _single_field(item))
    return tuple(fields)
