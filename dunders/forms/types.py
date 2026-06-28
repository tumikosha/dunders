"""Field-type registry: each type maps to a widget kind, a validator (returns an
error message or None) and a converter (string → JSON value). Pure, stdlib-only.

``dateparser`` is optional (extra ``dunders[forms]``). When absent, ``date``
validates anything and passes the raw string through unchanged.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from dunders.forms.schema import FieldSpec

try:  # optional extra: dunders[forms]
    import dateparser as _dateparser
except Exception:  # pragma: no cover - import guard
    _dateparser = None


@dataclass(frozen=True)
class TypeSpec:
    widget: str
    validate: Callable[[str, FieldSpec], "str | None"]
    convert: Callable[[str, FieldSpec], object]


def _ok(_value: str, _field: FieldSpec) -> None:
    return None


def _as_str(value: str, _field: FieldSpec) -> object:
    return value


_INT_RE = re.compile(r"^[+-]?\d+$")


def _int_validate(value: str, _field: FieldSpec) -> "str | None":
    if not _INT_RE.fullmatch(value):
        return "Must be an integer"
    try:
        int(value)
    except ValueError:
        return "Must be an integer"
    return None


def _int_convert(value: str, _field: FieldSpec) -> object:
    return int(value)


def _real_validate(value: str, _field: FieldSpec) -> "str | None":
    try:
        parsed = float(value)
    except ValueError:
        return "Must be a number"
    if not math.isfinite(parsed):
        return "Must be a finite number"
    return None


def _real_convert(value: str, _field: FieldSpec) -> object:
    return float(value)


def _bool_convert(value: str, _field: FieldSpec) -> object:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _combo_validate(value: str, field: FieldSpec) -> "str | None":
    if not field.options:
        return None
    if value not in field.options:
        return "Choose a value from the list"
    return None


def _parse_date(value: str) -> "datetime | None":
    return _dateparser.parse(value) if _dateparser is not None else None


def _date_validate(value: str, _field: FieldSpec) -> "str | None":
    if _dateparser is None:
        return None
    if _parse_date(value) is None:
        return "Unrecognized date"
    return None


def _date_convert(value: str, _field: FieldSpec) -> object:
    dt = _parse_date(value)
    if dt is None:
        return value
    if (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0):
        return dt.date().isoformat()
    return dt.isoformat()


FIELD_TYPES: dict[str, TypeSpec] = {
    "str": TypeSpec("input", _ok, _as_str),
    "clipboard": TypeSpec("input", _ok, _as_str),
    "selected_text": TypeSpec("textarea", _ok, _as_str),
    "text": TypeSpec("textarea", _ok, _as_str),
    "int": TypeSpec("int", _int_validate, _int_convert),
    "real": TypeSpec("real", _real_validate, _real_convert),
    "date": TypeSpec("date", _date_validate, _date_convert),
    "combo": TypeSpec("combo", _combo_validate, _as_str),
    "ecombo": TypeSpec("ecombo", _ok, _as_str),
    "bool": TypeSpec("checkbox", _ok, _bool_convert),
}


def get_type(name: str) -> TypeSpec:
    return FIELD_TYPES[name]
