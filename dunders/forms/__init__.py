"""Form editor core: stdlib-only, app-agnostic. Never imports fm/windowing.

Public surface mirrors how dunders.ai re-exports through a single package.
"""

from __future__ import annotations

from dunders.forms.context import read_clipboard
from dunders.forms.result import build_result, validate_all
from dunders.forms.schema import (
    EXAMPLE_FORM_JSON,
    FieldSpec,
    FormSpec,
    SchemaError,
    VALID_TYPES,
    looks_form,
    parse_schema,
)
from dunders.forms.types import FIELD_TYPES, TypeSpec, get_type

__all__ = [
    "EXAMPLE_FORM_JSON",
    "FieldSpec",
    "FormSpec",
    "SchemaError",
    "VALID_TYPES",
    "looks_form",
    "parse_schema",
    "FIELD_TYPES",
    "TypeSpec",
    "get_type",
    "read_clipboard",
    "build_result",
    "validate_all",
]
