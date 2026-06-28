"""Validation and result assembly for the form editor. Pure, stdlib-only.

Raw-value convention: each field's value is a string, except a checkbox whose
value is ``"true"``/``"false"``. ``validate_all`` returns ``{key: error}``;
``build_result`` converts to native JSON values in field order.
"""

from __future__ import annotations

from typing import Mapping

from dunders.forms.schema import FormSpec
from dunders.forms.types import get_type

# Widget kinds whose empty value becomes "" (vs None) in the result.
_TEXT_LIKE = frozenset({"input", "textarea", "combo", "ecombo"})


def validate_all(spec: FormSpec, raw: Mapping[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for f in spec.fields:
        value = raw.get(f.key, "")
        t = get_type(f.type)
        if t.widget == "checkbox":
            continue
        if not value.strip():
            if f.required:
                errors[f.key] = "Required"
            continue
        msg = t.validate(value, f)
        if msg:
            errors[f.key] = msg
    return errors


def build_result(spec: FormSpec, raw: Mapping[str, str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for f in spec.fields:
        value = raw.get(f.key, "")
        t = get_type(f.type)
        if t.widget == "checkbox":
            out[f.key] = t.convert(value, f)
            continue
        if not value.strip():
            out[f.key] = "" if t.widget in _TEXT_LIKE else None
            continue
        out[f.key] = t.convert(value, f)
    return out
