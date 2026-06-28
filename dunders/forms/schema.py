"""Form schema parsing: a JSON object of {key: type-string | field-object} into
a typed :class:`FormSpec`. Pure, stdlib-only, no Textual.

Schema format (variant A):
- ``$``-prefixed keys are meta: ``$title``, ``$description``. They never become
  fields and never appear in the result.
- A field value is either a bare type-string (``"str"``) or an object with
  ``type`` plus optional ``label`` / ``default`` / ``required`` / ``options``.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field


EXAMPLE_FORM_JSON: str = """{
  "$title": "Example form",
  "$description": "Edit this schema, then save and reopen it to render the form.",
  "name": {"type": "str", "label": "Your name", "required": true},
  "age": {"type": "int", "label": "Age", "default": 30},
  "ratio": {"type": "real", "label": "Ratio", "default": 1.5},
  "birthday": {"type": "date", "label": "Birthday"},
  "country": {"type": "combo", "label": "Country", "options": ["USA", "Germany", "Japan"], "default": "USA"},
  "city": {"type": "ecombo", "label": "City", "options": ["Berlin", "Tokyo"]},
  "subscribe": {"type": "bool", "label": "Subscribe?", "default": true},
  "clip": {"type": "clipboard", "label": "From clipboard"},
  "notes": {"type": "text", "label": "Notes"}
}"""

VALID_TYPES = frozenset(
    {
        "str",
        "int",
        "real",
        "date",
        "combo",
        "ecombo",
        "clipboard",
        "selected_text",
        "bool",
        "text",
    }
)


class SchemaError(ValueError):
    """Raised when a form schema is malformed."""


@dataclass(frozen=True)
class FieldSpec:
    key: str
    type: str
    label: str
    options: tuple[str, ...] = ()
    default: str | None = None
    required: bool = False


@dataclass(frozen=True)
class FormSpec:
    title: str
    description: str
    fields: tuple[FieldSpec, ...] = _dc_field(default_factory=tuple)


def looks_form(name: object) -> bool:
    """True if ``name`` has a ``.form.json`` extension (name-only, cheap)."""
    return str(name).lower().endswith(".form.json")


def _coerce_default(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_field(key: str, raw: object) -> FieldSpec:
    if isinstance(raw, str):
        type_name, obj = raw, {}
    elif isinstance(raw, dict):
        type_name = raw.get("type")
        obj = raw
    else:
        raise SchemaError(f"field {key!r}: value must be a type string or object")
    if not isinstance(type_name, str) or type_name not in VALID_TYPES:
        raise SchemaError(f"field {key!r}: unknown type {type_name!r}")
    options = obj.get("options", [])
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise SchemaError(f"field {key!r}: options must be a list of strings")
    return FieldSpec(
        key=key,
        type=type_name,
        label=str(obj.get("label", key)),
        options=tuple(options),
        default=_coerce_default(obj.get("default")),
        required=bool(obj.get("required", False)),
    )


def parse_schema(data: object) -> FormSpec:
    """Parse a schema dict into a :class:`FormSpec`. Raises :class:`SchemaError`."""
    if not isinstance(data, dict):
        raise SchemaError("schema must be a JSON object")
    fields = [
        _parse_field(key, raw)
        for key, raw in data.items()
        if not key.startswith("$")
    ]
    return FormSpec(
        title=str(data.get("$title", "")),
        description=str(data.get("$description", "")),
        fields=tuple(fields),
    )
