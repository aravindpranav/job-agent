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
    return el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
  };
  const selectorFor = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    return '';
  };
  controls.forEach((el) => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) return;
    if (el.offsetParent === null && type !== 'file') return;  // skip hidden
    const options = el.tagName.toLowerCase() === 'select'
      ? Array.from(el.options).map(o => o.text.trim()).filter(t => t && !/^select/i.test(t))
      : [];
    out.push({
      tag: el.tagName.toLowerCase(),
      type,
      name: el.getAttribute('name') || el.id || '',
      label: labelFor(el),
      required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true',
      options,
      selector: selectorFor(el),
    });
  });
  return out;
}
"""


def read_form(page) -> tuple[FormField, ...]:
    """Enumerate and classify every fillable control on the current page."""
    raw = page.evaluate(_SCAN_JS)
    fields: list[FormField] = []
    for r in raw:
        if not r["selector"]:
            continue  # unaddressable control — skip rather than guess a selector
        options = tuple(r["options"])
        field_type = classify_control(r["tag"], r["type"], r["label"], r["name"], bool(options))
        fields.append(FormField(
            selector=r["selector"],
            field_type=field_type,
            label=r["label"],
            name=r["name"],
            required=bool(r["required"]),
            options=options,
        ))
    return tuple(fields)
