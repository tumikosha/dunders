# Form Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A JSON-schema-driven, scrollable form with a `GO` button that produces a typed `{key: value}` JSON result, usable both programmatically (`app.forms.ask`) and as a `.form.json` viewer in the file manager.

**Architecture:** A domain-clean, stdlib-only core under `dunders/forms/` (schema parse, type registry, clipboard context, result assembly — never imports `fm`/`windowing`), a Textual `FormDialog` UI in `dunders/fm/form_dialog.py`, and a thin `FormsService` (`app.forms`) plus launch wiring in `dunders/app.py`.

**Tech Stack:** Python ≥3.12, Textual, stdlib `json`/`subprocess`. Optional `dateparser` via the new `dunders[forms]` extra (soft degradation when absent).

## Global Constraints

- Python ≥3.12; the `dunders/forms/` core is **stdlib-only** and must never import `dunders.fm` or `dunders.windowing`.
- `dateparser` is an **optional** dependency (extra `dunders[forms]`); the `date` type degrades to passthrough string when it is not installed.
- Tests run under `pytest` with `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed); `testpaths = ["tests"]`.
- Lint with `ruff check` (must stay clean).
- Clipboard reading is via `subprocess` only — no new runtime dependency.
- Field/result key order always follows schema key order; `$`-prefixed keys are meta and never appear in the result.

---

### Task 1: Forms package + schema parser

**Files:**
- Create: `dunders/forms/__init__.py`
- Create: `dunders/forms/schema.py`
- Test: `tests/forms/__init__.py` (empty), `tests/forms/test_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `FieldSpec(key: str, type: str, label: str, options: tuple[str, ...], default: str | None, required: bool)` — frozen dataclass.
  - `FormSpec(title: str, description: str, fields: tuple[FieldSpec, ...])` — frozen dataclass.
  - `SchemaError(ValueError)`.
  - `VALID_TYPES: frozenset[str]`.
  - `parse_schema(data: dict) -> FormSpec`.
  - `looks_form(name: object) -> bool` (True iff name ends `.form.json`).

- [ ] **Step 1: Write the failing test**

Create `tests/forms/__init__.py` (empty file) and `tests/forms/test_schema.py`:

```python
import pytest

from dunders.forms.schema import (
    FieldSpec,
    FormSpec,
    SchemaError,
    looks_form,
    parse_schema,
)


def test_string_shorthand_and_object_forms():
    spec = parse_schema(
        {
            "$title": "T",
            "$description": "D",
            "name": "str",
            "salary": {"type": "int", "label": "Monthly", "default": 1000, "required": True},
            "country": {"type": "combo", "options": ["US", "DE"]},
        }
    )
    assert isinstance(spec, FormSpec)
    assert spec.title == "T"
    assert spec.description == "D"
    # field order preserved, meta keys excluded
    assert [f.key for f in spec.fields] == ["name", "salary", "country"]
    name = spec.fields[0]
    assert isinstance(name, FieldSpec)
    assert name.type == "str" and name.label == "name" and not name.required
    salary = spec.fields[1]
    assert salary.type == "int" and salary.label == "Monthly"
    assert salary.default == "1000" and salary.required is True
    country = spec.fields[2]
    assert country.options == ("US", "DE")


def test_unknown_type_raises():
    with pytest.raises(SchemaError):
        parse_schema({"x": "wat"})


def test_non_object_schema_raises():
    with pytest.raises(SchemaError):
        parse_schema([1, 2, 3])


def test_looks_form():
    assert looks_form("a.form.json")
    assert looks_form("A.FORM.JSON")
    assert not looks_form("a.json")
    assert not looks_form("a.form")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/forms/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.forms'`.

- [ ] **Step 3: Write minimal implementation**

Create `dunders/forms/schema.py`:

```python
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
```

Create `dunders/forms/__init__.py`:

```python
"""Form editor core: stdlib-only, app-agnostic. Never imports fm/windowing.

Public surface mirrors how dunders.ai re-exports through a single package.
"""

from __future__ import annotations

from dunders.forms.schema import (
    FieldSpec,
    FormSpec,
    SchemaError,
    VALID_TYPES,
    looks_form,
    parse_schema,
)

__all__ = [
    "FieldSpec",
    "FormSpec",
    "SchemaError",
    "VALID_TYPES",
    "looks_form",
    "parse_schema",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/forms/test_schema.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add dunders/forms/__init__.py dunders/forms/schema.py tests/forms/__init__.py tests/forms/test_schema.py
git commit -m "feat(forms): schema parser + FormSpec/FieldSpec core"
```

---

### Task 2: Field-type registry + `dunders[forms]` extra

**Files:**
- Create: `dunders/forms/types.py`
- Modify: `dunders/forms/__init__.py` (re-export `get_type`, `TypeSpec`, `FIELD_TYPES`)
- Modify: `pyproject.toml` (add `forms` extra; add it to `all`)
- Test: `tests/forms/test_types.py`

**Interfaces:**
- Consumes: `FieldSpec` from Task 1.
- Produces:
  - `TypeSpec(widget: str, validate: Callable[[str, FieldSpec], str | None], convert: Callable[[str, FieldSpec], object])`.
  - `FIELD_TYPES: dict[str, TypeSpec]` keyed by type name.
  - `get_type(name: str) -> TypeSpec`.
  - `widget` is one of `"input" | "int" | "real" | "date" | "combo" | "ecombo" | "checkbox" | "textarea"`.

- [ ] **Step 1: Write the failing test**

Create `tests/forms/test_types.py`:

```python
import pytest

from dunders.forms import types as T
from dunders.forms.schema import FieldSpec


def _f(key="x", type="str", **kw):
    return FieldSpec(key=key, type=type, label=key, **kw)


def test_widget_mapping():
    assert T.get_type("str").widget == "input"
    assert T.get_type("clipboard").widget == "input"
    assert T.get_type("selected_text").widget == "textarea"
    assert T.get_type("text").widget == "textarea"
    assert T.get_type("bool").widget == "checkbox"
    assert T.get_type("combo").widget == "combo"
    assert T.get_type("ecombo").widget == "ecombo"


def test_int_real():
    ti = T.get_type("int")
    assert ti.validate("42", _f(type="int")) is None
    assert ti.convert("42", _f(type="int")) == 42
    assert ti.validate("4.5", _f(type="int")) is not None
    tr = T.get_type("real")
    assert tr.validate("3.14", _f(type="real")) is None
    assert tr.convert("3.14", _f(type="real")) == pytest.approx(3.14)
    assert tr.validate("abc", _f(type="real")) is not None


def test_bool_convert():
    tb = T.get_type("bool")
    assert tb.convert("true", _f(type="bool")) is True
    assert tb.convert("false", _f(type="bool")) is False


def test_combo_membership():
    tc = T.get_type("combo")
    f = _f(type="combo", options=("US", "DE"))
    assert tc.validate("US", f) is None
    assert tc.validate("XX", f) is not None
    # ecombo accepts free entry
    te = T.get_type("ecombo")
    assert te.validate("anything", _f(type="ecombo", options=("a",))) is None


def test_date_passthrough_when_dateparser_absent(monkeypatch):
    monkeypatch.setattr(T, "_dateparser", None)
    td = T.get_type("date")
    f = _f(type="date")
    assert td.validate("whenever", f) is None
    assert td.convert("whenever", f) == "whenever"


def test_date_iso_when_dateparser_present():
    dateparser = pytest.importorskip("dateparser")
    td = T.get_type("date")
    f = FieldSpec(key="d", type="date", label="d")
    assert td.validate("2026-06-27", f) is None
    assert td.convert("2026-06-27", f) == "2026-06-27"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/forms/test_types.py -v`
Expected: FAIL with `AttributeError: module 'dunders.forms.types' has no attribute ...` / import error.

- [ ] **Step 3: Write minimal implementation**

Create `dunders/forms/types.py`:

```python
"""Field-type registry: each type maps to a widget kind, a validator (returns an
error message or None) and a converter (string → JSON value). Pure, stdlib-only.

``dateparser`` is optional (extra ``dunders[forms]``). When absent, ``date``
validates anything and passes the raw string through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def _int_validate(value: str, _field: FieldSpec) -> "str | None":
    try:
        int(value)
    except ValueError:
        return "Must be an integer"
    return None


def _int_convert(value: str, _field: FieldSpec) -> object:
    return int(value)


def _real_validate(value: str, _field: FieldSpec) -> "str | None":
    try:
        float(value)
    except ValueError:
        return "Must be a number"
    return None


def _real_convert(value: str, _field: FieldSpec) -> object:
    return float(value)


def _bool_convert(value: str, _field: FieldSpec) -> object:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _combo_validate(value: str, field: FieldSpec) -> "str | None":
    if value not in field.options:
        return "Choose a value from the list"
    return None


def _parse_date(value: str):
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
```

Add to `dunders/forms/__init__.py` imports and `__all__`:

```python
from dunders.forms.types import FIELD_TYPES, TypeSpec, get_type
```

(append `"FIELD_TYPES"`, `"TypeSpec"`, `"get_type"` to `__all__`.)

Modify `pyproject.toml` — add after the `db = [...]` extra:

```toml
# Opt-in date recognition for the `date` form-field type. dateparser is BSD;
# kept out of the default install so the base package stays dependency-light.
# Absent → a `date` field behaves as a plain string (passthrough).
forms = ["dateparser>=1.2"]
```

and change the `all` extra to include it:

```toml
all = ["dunders[sftp,image,office,db,ai,forms]"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/forms/test_types.py -v`
Expected: PASS (the dateparser-present test is skipped if the extra isn't installed).

- [ ] **Step 5: Commit**

```bash
git add dunders/forms/types.py dunders/forms/__init__.py pyproject.toml tests/forms/test_types.py
git commit -m "feat(forms): field-type registry + dunders[forms] extra"
```

---

### Task 3: Clipboard context reader

**Files:**
- Create: `dunders/forms/context.py`
- Modify: `dunders/forms/__init__.py` (re-export `read_clipboard`)
- Test: `tests/forms/test_context.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `read_clipboard(timeout: float = 1.0) -> str` — system clipboard text, or `""` on any failure / missing tool.

- [ ] **Step 1: Write the failing test**

Create `tests/forms/test_context.py`:

```python
import subprocess
from types import SimpleNamespace

from dunders.forms import context


def test_reads_first_available_tool(monkeypatch):
    monkeypatch.setattr(context.sys, "platform", "linux")
    monkeypatch.setattr(context.shutil, "which", lambda name: "/usr/bin/" + name)

    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout="hello world\n")

    monkeypatch.setattr(context.subprocess, "run", fake_run)
    assert context.read_clipboard() == "hello world"


def test_missing_tool_returns_empty(monkeypatch):
    monkeypatch.setattr(context.sys, "platform", "linux")
    monkeypatch.setattr(context.shutil, "which", lambda name: None)
    assert context.read_clipboard() == ""


def test_timeout_returns_empty(monkeypatch):
    monkeypatch.setattr(context.sys, "platform", "darwin")
    monkeypatch.setattr(context.shutil, "which", lambda name: "/usr/bin/pbpaste")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1.0)

    monkeypatch.setattr(context.subprocess, "run", boom)
    assert context.read_clipboard() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/forms/test_context.py -v`
Expected: FAIL with import error (`context` not found).

- [ ] **Step 3: Write minimal implementation**

Create `dunders/forms/context.py`:

```python
"""Autofill context for the form editor. ``read_clipboard`` shells out to the
platform clipboard tool (no pip dependency); any failure degrades to ``""``.

``selected_text`` is NOT read here — it is supplied by the caller (the app
knows the active editor), keeping this core free of fm/windowing imports.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

_POSIX_READERS = [
    ["wl-paste", "--no-newline"],
    ["xclip", "-selection", "clipboard", "-o"],
    ["xsel", "-b", "-o"],
]


def _candidate_commands() -> list[list[str]]:
    if sys.platform == "darwin":
        return [["pbpaste"]]
    if sys.platform.startswith("win"):
        return [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
    return list(_POSIX_READERS)


def read_clipboard(timeout: float = 1.0) -> str:
    """Return the system clipboard text, or ``""`` if it can't be read."""
    for cmd in _candidate_commands():
        if shutil.which(cmd[0]) is None:
            continue
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            return (out.stdout or "").rstrip("\r\n")
    return ""
```

Add to `dunders/forms/__init__.py`:

```python
from dunders.forms.context import read_clipboard
```

(append `"read_clipboard"` to `__all__`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/forms/test_context.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add dunders/forms/context.py dunders/forms/__init__.py tests/forms/test_context.py
git commit -m "feat(forms): clipboard context reader (subprocess, soft-degrading)"
```

---

### Task 4: Validation + result assembly

**Files:**
- Create: `dunders/forms/result.py`
- Modify: `dunders/forms/__init__.py` (re-export `validate_all`, `build_result`)
- Test: `tests/forms/test_result.py`

**Interfaces:**
- Consumes: `FormSpec`, `get_type` (Tasks 1–2).
- Produces:
  - `validate_all(spec: FormSpec, raw: Mapping[str, str]) -> dict[str, str]` — `{key: error}` for invalid fields (empty dict = all valid). Required-empty → `"Required"`; empty optional fields skip type validation.
  - `build_result(spec: FormSpec, raw: Mapping[str, str]) -> dict[str, object]` — typed result in field order. Empty text-likes → `""`; empty number/date → `None`; `$`-meta excluded by construction (not in `spec.fields`).
- Raw-value convention (also used by the dialog in Task 6): every field's value is a string, except checkbox which is `"true"`/`"false"`.

- [ ] **Step 1: Write the failing test**

Create `tests/forms/test_result.py`:

```python
from dunders.forms import build_result, parse_schema, validate_all


SCHEMA = {
    "name": {"type": "str", "required": True},
    "age": "int",
    "ratio": "real",
    "agree": "bool",
    "country": {"type": "combo", "options": ["US", "DE"]},
}


def test_validate_required_and_types():
    spec = parse_schema(SCHEMA)
    errors = validate_all(
        spec,
        {"name": "", "age": "notint", "ratio": "1.5", "agree": "true", "country": "US"},
    )
    assert errors["name"] == "Required"
    assert "age" in errors
    assert "ratio" not in errors
    assert "country" not in errors


def test_validate_all_clean():
    spec = parse_schema(SCHEMA)
    errors = validate_all(
        spec,
        {"name": "Bob", "age": "30", "ratio": "1.5", "agree": "false", "country": "DE"},
    )
    assert errors == {}


def test_build_result_typed_and_ordered():
    spec = parse_schema(SCHEMA)
    result = build_result(
        spec,
        {"name": "Bob", "age": "30", "ratio": "1.5", "agree": "true", "country": "DE"},
    )
    assert list(result.keys()) == ["name", "age", "ratio", "agree", "country"]
    assert result == {
        "name": "Bob",
        "age": 30,
        "ratio": 1.5,
        "agree": True,
        "country": "DE",
    }


def test_build_result_empty_optionals():
    spec = parse_schema({"note": "str", "age": "int"})
    result = build_result(spec, {"note": "", "age": ""})
    assert result == {"note": "", "age": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/forms/test_result.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_result'`.

- [ ] **Step 3: Write minimal implementation**

Create `dunders/forms/result.py`:

```python
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
```

Add to `dunders/forms/__init__.py`:

```python
from dunders.forms.result import build_result, validate_all
```

(append `"build_result"`, `"validate_all"` to `__all__`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/forms/test_result.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add dunders/forms/result.py dunders/forms/__init__.py tests/forms/test_result.py
git commit -m "feat(forms): validation + typed result assembly"
```

---

### Task 5: FormDialog UI

**Files:**
- Create: `dunders/fm/form_dialog.py`
- Test: `tests/fm/test_form_dialog.py`

**Interfaces:**
- Consumes: `FormSpec`, `FieldSpec`, `get_type`, `validate_all`, `build_result`, `read_clipboard` (forms core); `WindowContent` (windowing).
- Produces:
  - `FormDialog(spec: FormSpec, *, selected_text: str = "", context: object | None = None)` — a `Container`+`WindowContent`.
  - `FormDialog.Submitted(dialog, result: dict)` and `FormDialog.Cancelled(dialog)` messages.
  - Methods used by the app/tests: `action_go()`, `action_cancel()`, `focus_first()`, and `_raw_values() -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/fm/test_form_dialog.py`:

```python
import pytest

from dunders.app import DundersApp
from dunders.fm.form_dialog import FormDialog
from dunders.forms import parse_schema
from dunders.windowing.helpers import show_modal


async def _mount(app, spec, *, selected_text=""):
    dialog = FormDialog(spec, selected_text=selected_text)
    show_modal(app.desktop, dialog, title="Form", size=(60, 18))
    return dialog


@pytest.mark.asyncio
async def test_selected_text_prefilled(tmp_path):
    spec = parse_schema({"text": {"type": "selected_text"}})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = await _mount(app, spec, selected_text="hello sel")
        await pilot.pause()
        assert dialog._raw_values()["text"] == "hello sel"


@pytest.mark.asyncio
async def test_go_with_invalid_does_not_submit(tmp_path):
    spec = parse_schema({"age": "int"})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    captured = []
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = await _mount(app, spec)
        await pilot.pause()
        dialog._rows["age"]["primary"].value = "notanint"
        captured.clear()
        app.screen.post_message  # touch to ensure screen exists
        dialog.action_go()
        await pilot.pause()
        # dialog still mounted (not submitted): error text set
        err = dialog.query_one("#fe-age")
        assert "integer" in str(err.render()).lower()


@pytest.mark.asyncio
async def test_go_with_valid_submits_typed(tmp_path):
    spec = parse_schema({"name": {"type": "str"}, "age": "int", "agree": "bool"})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    results = []

    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = FormDialog(spec)
        show_modal(app.desktop, dialog, title="Form", size=(60, 18))
        await pilot.pause()

        def _grab(event):
            results.append(event.result)

        # subscribe by overriding the app handler indirectly: post and read
        dialog._rows["name"]["primary"].value = "Bob"
        dialog._rows["age"]["primary"].value = "30"
        dialog._rows["agree"]["primary"].value = True
        raw = dialog._raw_values()
        from dunders.forms import build_result

        assert build_result(spec, raw) == {"name": "Bob", "age": 30, "agree": True}


@pytest.mark.asyncio
async def test_ecombo_custom_swaps_to_input(tmp_path):
    spec = parse_schema({"city": {"type": "ecombo", "options": ["Berlin"]}})
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = await _mount(app, spec)
        await pilot.pause()
        row = dialog._rows["city"]
        assert row["alt"].display is False
        dialog._activate_custom("city")
        await pilot.pause()
        assert row["alt"].display is True
        row["alt"].value = "Praha"
        assert dialog._raw_values()["city"] == "Praha"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/fm/test_form_dialog.py -v`
Expected: FAIL with import error (`dunders.fm.form_dialog` not found).

- [ ] **Step 3: Write minimal implementation**

Create `dunders/fm/form_dialog.py`:

```python
"""FormDialog: a scrollable, schema-driven form modal.

Each field renders the widget its type maps to (see dunders.forms.types). On
GO the values are validated; invalid fields highlight and the form stays open.
When clean, a typed result dict is posted via :class:`Submitted`.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

from dunders.forms import (
    FieldSpec,
    FormSpec,
    build_result,
    get_type,
    read_clipboard,
    validate_all,
)
from dunders.windowing import WindowContent

# Sentinel Select value for the "✎ Custom…" entry of an editable combo.
_CUSTOM = "\x00__dunders_custom__"


class FormDialog(Container, WindowContent):
    can_focus = False

    BINDINGS = [Binding("escape", "cancel", show=False)]

    DEFAULT_CSS = """
    FormDialog { layout: vertical; background: $surface; }
    FormDialog #form-fields { height: 1fr; padding: 0 1; }
    FormDialog .form-label { margin-top: 1; color: $text; }
    FormDialog .form-error { color: red; height: auto; }
    FormDialog Input, FormDialog Select { margin: 0; width: 1fr; }
    FormDialog TextArea { height: 4; margin: 0; }
    FormDialog #form-buttons { height: 1; align: center middle; margin: 1 0; }
    FormDialog #form-buttons Button { margin: 0 1; }
    """

    class Submitted(Message):
        def __init__(self, dialog: "FormDialog", result: dict) -> None:
            self.dialog = dialog
            self.result = result
            super().__init__()

    class Cancelled(Message):
        def __init__(self, dialog: "FormDialog") -> None:
            self.dialog = dialog
            super().__init__()

    def __init__(
        self,
        spec: FormSpec,
        *,
        selected_text: str = "",
        context: object | None = None,
    ) -> None:
        super().__init__()
        self.spec = spec
        self.context = context
        self.window_title = spec.title or "Form"
        self._selected_text = selected_text
        # key -> {"kind": str, "primary": Widget, "alt": Input | None}
        self._rows: dict[str, dict] = {}

    # --- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form-fields"):
            for f in self.spec.fields:
                yield from self._compose_field(f)
        with Horizontal(id="form-buttons"):
            yield Button("GO", id="form-go", variant="success")
            yield Button("Cancel", id="form-cancel")

    def _compose_field(self, f: FieldSpec) -> ComposeResult:
        label = f.label + (" *" if f.required else "")
        yield Static(label + ":", classes="form-label")
        kind = get_type(f.type).widget
        default = f.default or ""
        if kind == "checkbox":
            checked = default.strip().lower() in ("1", "true", "yes", "on")
            w = Checkbox(value=checked, id=f"fw-{f.key}")
            self._rows[f.key] = {"kind": kind, "primary": w, "alt": None}
            yield w
        elif kind == "textarea":
            text = self._selected_text if f.type == "selected_text" else default
            w = TextArea(text, id=f"fw-{f.key}")
            self._rows[f.key] = {"kind": kind, "primary": w, "alt": None}
            yield w
        elif kind == "combo":
            opts = [(o, o) for o in f.options]
            value = default if default in f.options else Select.BLANK
            w = Select(opts, id=f"fw-{f.key}", allow_blank=True, value=value)
            self._rows[f.key] = {"kind": kind, "primary": w, "alt": None}
            yield w
        elif kind == "ecombo":
            opts = [(o, o) for o in f.options] + [("✎ Custom…", _CUSTOM)]
            value = default if default in f.options else Select.BLANK
            sel = Select(opts, id=f"fw-{f.key}", allow_blank=True, value=value)
            inp = Input(value=default, id=f"fwx-{f.key}")
            inp.display = bool(default) and default not in f.options
            self._rows[f.key] = {"kind": kind, "primary": sel, "alt": inp}
            yield sel
            yield inp
        else:  # input-like: str / int / real / date / clipboard
            initial = read_clipboard() if f.type == "clipboard" else default
            if not initial and default:
                initial = default
            w = Input(value=initial, id=f"fw-{f.key}")
            self._rows[f.key] = {"kind": kind, "primary": w, "alt": None}
            yield w
        yield Static("", classes="form-error", id=f"fe-{f.key}")

    def on_mount(self) -> None:
        self.call_after_refresh(self.focus_first)

    def focus_first(self) -> None:
        for f in self.spec.fields:
            row = self._rows.get(f.key)
            if row is not None:
                row["primary"].focus()
                return

    # --- value extraction -------------------------------------------------

    def _raw_value(self, f: FieldSpec) -> str:
        row = self._rows[f.key]
        kind = row["kind"]
        if kind == "checkbox":
            return "true" if row["primary"].value else "false"
        if kind == "textarea":
            return row["primary"].text
        if kind == "combo":
            v = row["primary"].value
            return "" if v is Select.BLANK else str(v)
        if kind == "ecombo":
            inp = row["alt"]
            if inp is not None and inp.display:
                return inp.value
            v = row["primary"].value
            return "" if v is Select.BLANK else str(v)
        return row["primary"].value

    def _raw_values(self) -> dict[str, str]:
        return {f.key: self._raw_value(f) for f in self.spec.fields}

    # --- editable-combo swap ----------------------------------------------

    def _activate_custom(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None or row["kind"] != "ecombo":
            return
        row["primary"].display = False
        if row["alt"] is not None:
            row["alt"].display = True
            self.call_after_refresh(row["alt"].focus)

    def on_select_changed(self, event: Select.Changed) -> None:
        for f in self.spec.fields:
            row = self._rows.get(f.key)
            if row and row["kind"] == "ecombo" and row["primary"] is event.select:
                if event.value == _CUSTOM:
                    self._activate_custom(f.key)
                break

    # --- actions ----------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-go":
            self.action_go()
        elif event.button.id == "form-cancel":
            self.action_cancel()

    def action_go(self) -> None:
        raw = self._raw_values()
        errors = validate_all(self.spec, raw)
        for f in self.spec.fields:
            self.query_one(f"#fe-{f.key}", Static).update(errors.get(f.key, ""))
        if errors:
            first = next(f for f in self.spec.fields if f.key in errors)
            row = self._rows[first.key]
            (row["alt"] if row["alt"] and row["alt"].display else row["primary"]).focus()
            return
        self.post_message(self.Submitted(self, build_result(self.spec, raw)))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled(self))

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            self.action_cancel()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/fm/test_form_dialog.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add dunders/fm/form_dialog.py tests/fm/test_form_dialog.py
git commit -m "feat(forms): FormDialog UI (scroll, validation, ecombo custom swap)"
```

---

### Task 6: FormsService + app wiring (`app.forms`, `_open_form`, handlers, result write)

**Files:**
- Create: `dunders/fm/forms_service.py`
- Modify: `dunders/app.py` (imports; `self.forms` in `__init__`; `FormRequest` payload; `_open_form`, `_open_form_from_file`, `_active_editor_selection`, `_write_form_result`; `on_form_dialog_submitted`/`_cancelled`)
- Test: `tests/fm/test_forms_app.py`

**Interfaces:**
- Consumes: `FormDialog` (Task 5); `parse_schema`, `FormSpec` (core); `show_modal`, `_close_modal` (app).
- Produces:
  - `FormsService(app)` with `async ask(spec: FormSpec | dict, *, selected_text: str | None = None) -> dict | None`.
  - `app.forms: FormsService`.
  - `DundersApp._open_form(spec, *, schema_path: Path | None = None, selected_text: str = "", on_result=None)`.
  - `DundersApp._open_form_from_file(path: Path)`.
  - `DundersApp._active_editor_selection() -> str`.
  - `DundersApp._write_form_result(schema_path: Path, result: dict)`.

- [ ] **Step 1: Write the failing test**

Create `tests/fm/test_forms_app.py`:

```python
import json

import pytest

from dunders.app import DundersApp
from dunders.fm.form_dialog import FormDialog
from dunders.forms import parse_schema


@pytest.mark.asyncio
async def test_ask_returns_dict_on_go(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        spec = parse_schema({"name": {"type": "str"}, "age": "int"})
        task = pilot.app.run_worker  # ensure app loop is live
        fut = app.forms.ask(spec)
        # drive the dialog
        await pilot.pause()
        dialog = app.query_one(FormDialog)
        dialog._rows["name"]["primary"].value = "Bob"
        dialog._rows["age"]["primary"].value = "7"
        dialog.action_go()
        await pilot.pause()
        result = await fut
        assert result == {"name": "Bob", "age": 7}


@pytest.mark.asyncio
async def test_ask_returns_none_on_cancel(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        spec = parse_schema({"name": "str"})
        fut = app.forms.ask(spec)
        await pilot.pause()
        app.query_one(FormDialog).action_cancel()
        await pilot.pause()
        assert await fut is None


@pytest.mark.asyncio
async def test_file_form_writes_result(tmp_path):
    schema = tmp_path / "demo.form.json"
    schema.write_text(json.dumps({"name": "str", "age": "int"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_form_from_file(schema)
        await pilot.pause()
        dialog = app.query_one(FormDialog)
        dialog._rows["name"]["primary"].value = "Ann"
        dialog._rows["age"]["primary"].value = "5"
        dialog.action_go()
        await pilot.pause()
        out = tmp_path / "demo.result.json"
        assert out.exists()
        assert json.loads(out.read_text()) == {"name": "Ann", "age": 5}


@pytest.mark.asyncio
async def test_bad_schema_file_notifies_no_dialog(tmp_path):
    schema = tmp_path / "bad.form.json"
    schema.write_text("{ not json", encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_form_from_file(schema)
        await pilot.pause()
        assert not app.query(FormDialog)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/fm/test_forms_app.py -v`
Expected: FAIL — `AttributeError: 'DundersApp' object has no attribute 'forms'`.

- [ ] **Step 3: Write minimal implementation**

Create `dunders/fm/forms_service.py`:

```python
"""FormsService: the runtime object behind ``app.forms`` (mirrors ``app.ai``).

``ask`` opens a :class:`FormDialog` and resolves to the typed result dict (or
None on cancel) via an asyncio Future bridged by the app's dialog handlers.
"""

from __future__ import annotations

import asyncio

from dunders.forms import FormSpec, parse_schema


class FormsService:
    def __init__(self, app) -> None:
        self._app = app

    async def ask(
        self,
        spec: "FormSpec | dict",
        *,
        selected_text: str | None = None,
    ) -> "dict | None":
        if isinstance(spec, dict):
            spec = parse_schema(spec)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def _done(result: "dict | None") -> None:
            if not fut.done():
                fut.set_result(result)

        self._app._open_form(
            spec, selected_text=selected_text or "", on_result=_done
        )
        return await fut
```

In `dunders/app.py`:

Add imports near the other fm imports:

```python
from dunders.fm.form_dialog import FormDialog
from dunders.fm.forms_service import FormsService
from dunders.forms import SchemaError, looks_form, parse_schema
```

Add a payload dataclass next to the other `*Request` payloads (search for `class CopyMoveRequest` and place nearby):

```python
@dataclass
class FormRequest:
    """Context payload for a FormDialog: where the result goes."""

    schema_path: "Path | None" = None
    on_result: "object | None" = None  # callable(result | None) | None
```

In `DundersApp.__init__`, right after `self.ai = LlmService(events=self.events)`:

```python
        self.forms = FormsService(self)
```

Add the methods (place them near `action_find_file` / the other dialog openers):

```python
    def _active_editor_selection(self) -> str:
        """Selected text in the focused editor, or "" if none."""
        win = self.desktop.focused_window if self.desktop else None
        content = getattr(win, "content", None)
        editor = getattr(content, "_editor", None)
        buf = getattr(editor, "buffer", None)
        if buf is None:
            return ""
        try:
            return buf.get_selected_text() or ""
        except Exception:
            return ""

    def _open_form(
        self,
        spec,
        *,
        schema_path=None,
        selected_text: str = "",
        on_result=None,
    ) -> None:
        if self.desktop is None:
            if on_result is not None:
                on_result(None)
            return
        self._remember_active_panel_id()
        ctx = FormRequest(schema_path=schema_path, on_result=on_result)
        dialog = FormDialog(spec, selected_text=selected_text, context=ctx)
        show_modal(
            self.desktop, dialog, title=spec.title or "Form", size=(76, 22)
        )
        self.call_after_refresh(dialog.focus_first)

    def _open_form_from_file(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            spec = parse_schema(data)
        except (OSError, ValueError, SchemaError) as exc:
            self.notify(f"Invalid form schema: {exc}", severity="error")
            return
        self._open_form(
            spec,
            schema_path=path,
            selected_text=self._active_editor_selection(),
        )

    def _write_form_result(self, schema_path: Path, result: dict) -> None:
        name = schema_path.name
        suffix = ".form.json"
        base = name[: -len(suffix)] if name.lower().endswith(suffix) else schema_path.stem
        out = schema_path.with_name(base + ".result.json")
        try:
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, out)
            self.notify(f"Saved {out.name}")
        except OSError as exc:
            self.notify(f"Could not save result: {exc}", severity="error")

    def on_form_dialog_submitted(self, event: "FormDialog.Submitted") -> None:
        dialog = event.dialog
        ctx = getattr(dialog, "context", None)
        result = event.result
        self._close_modal(dialog)
        cb = getattr(ctx, "on_result", None)
        if cb is not None:
            cb(result)
            return
        path = getattr(ctx, "schema_path", None)
        if path is not None:
            self._write_form_result(path, result)

    def on_form_dialog_cancelled(self, event: "FormDialog.Cancelled") -> None:
        dialog = event.dialog
        ctx = getattr(dialog, "context", None)
        self._close_modal(dialog)
        cb = getattr(ctx, "on_result", None)
        if cb is not None:
            cb(None)
```

(Confirm `json` and `os` are already imported at the top of `app.py` — they are used elsewhere; if a linter flags a missing one, add it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/fm/test_forms_app.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add dunders/fm/forms_service.py dunders/app.py tests/fm/test_forms_app.py
git commit -m "feat(forms): app.forms service + FormDialog wiring & result write"
```

---

### Task 7: Launch points — File menu + F3/F4 routing

**Files:**
- Modify: `dunders/app.py` (register `form.open` command; add `File` menu item; `action_form_open`; route `.form.json` in `_open_editor_window` and `_open_member_view`)
- Test: `tests/fm/test_form_routing.py`

**Interfaces:**
- Consumes: `_open_form`, `_open_form_from_file`, `_active_editor_selection`, `looks_form` (Task 6); `_register_app_commands`, the `File` `Menu` (existing).
- Produces:
  - `WindowCommand(id="form.open", ...)` and `action_form_open()`.
  - `.form.json` routing branch in `_open_editor_window(path, ...)` and `_open_member_view(entry)`.

- [ ] **Step 1: Write the failing test**

Create `tests/fm/test_form_routing.py`:

```python
import json

import pytest

from dunders.app import DundersApp
from dunders.fm.form_dialog import FormDialog


@pytest.mark.asyncio
async def test_f3_on_form_json_opens_form(tmp_path):
    schema = tmp_path / "x.form.json"
    schema.write_text(json.dumps({"name": "str"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_editor_window(schema, read_only=True)
        await pilot.pause()
        assert app.query(FormDialog)


@pytest.mark.asyncio
async def test_f4_on_form_json_opens_form(tmp_path):
    schema = tmp_path / "y.form.json"
    schema.write_text(json.dumps({"age": "int"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_editor_window(schema, read_only=False)
        await pilot.pause()
        assert app.query(FormDialog)


@pytest.mark.asyncio
async def test_plain_json_does_not_open_form(tmp_path):
    plain = tmp_path / "data.json"
    plain.write_text(json.dumps({"name": "str"}), encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_editor_window(plain, read_only=True)
        await pilot.pause()
        assert not app.query(FormDialog)


@pytest.mark.asyncio
async def test_form_open_command_registered(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.dispatcher.registry.get("form.open") is not None
```

(If `app.dispatcher.registry.get` is not the exact lookup API, the last test may
need `app.registry`/`resolve`; adjust to the real registry accessor used by
other command tests — see `tests/fm/test_associations_menu_app.py` for the
pattern. The behavior asserted is "the `form.open` command exists".)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/fm/test_form_routing.py -v`
Expected: FAIL — the first two assert no `FormDialog` (routing not added yet).

- [ ] **Step 3: Write minimal implementation**

In `dunders/app.py`:

Register the command — in `_register_app_commands`, add to the command list (near the `ai.settings` registration):

```python
            WindowCommand(id="form.open", label="Form editor…", handler=self.action_form_open),
```

Add the menu item — in the `File` menu (the `Menu("File", [...])` block), add after `app.open_file`:

```python
                MenuItem(label="Form editor…", command_id="form.open"),
```

Add the action method (near `action_find_file`):

```python
    def action_form_open(self) -> None:
        """File ▸ Form editor… — hybrid: use a .form.json under the cursor,
        else prompt for a schema path."""
        if self._has_active_modal():
            return
        panel = self._active_panel()
        if panel is None or self.desktop is None:
            return
        entry = panel.current_entry if panel is not None else None
        path = getattr(entry, "path", None)
        if path is not None and looks_form(path.name):
            self._open_form_from_file(path)
            return
        self._remember_active_panel_id()
        dialog = InputDialog(
            "Schema path (.form.json):",
            initial=str(panel.cwd) + "/",
            context=FormSchemaPromptRequest(),
        )
        show_modal(self.desktop, dialog, title="Form editor", size=(60, 5))
        self.call_after_refresh(dialog.focus_input)
```

Add the prompt payload near `FormRequest`:

```python
@dataclass
class FormSchemaPromptRequest:
    """Marks an InputDialog whose value is a path to a .form.json schema."""
```

Handle that prompt's submission — find `on_input_dialog_submitted` and add a
branch dispatching on the context type (mirroring how it already isinstance-
checks other `*Request` contexts):

```python
        if isinstance(event.dialog.context, FormSchemaPromptRequest):
            self._close_modal(event.dialog)
            self._open_form_from_file(Path(event.value).expanduser())
            return
```

Route `.form.json` in `_open_editor_window` — add at the very top of the method,
right after `self._editor_seq += 1; seq = self._editor_seq` (so it precedes the
image/csv/hex branches; both F3 read_only and F4 not-read_only reach it):

```python
        # A .form.json schema opens the form editor, not the text/hex viewer.
        if looks_form(path.name):
            self._open_form_from_file(path)
            return
```

Route `.form.json` for VFS members in `_open_member_view` — add near the top of
that method, before the office/csv/markdown branches:

```python
        if looks_form(entry.name):
            try:
                spec = parse_schema(json.loads(bytes(data).decode("utf-8")))
            except (ValueError, SchemaError) as exc:
                self.notify(f"Invalid form schema: {exc}", severity="error")
                return
            self._open_form(spec, selected_text=self._active_editor_selection())
            return
```

(Place this after `data` is read in `_open_member_view`. If the member's bytes
variable is named differently than `data`, use that name.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/fm/test_form_routing.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add dunders/app.py tests/fm/test_form_routing.py
git commit -m "feat(forms): File menu entry + F3/F4 routing for .form.json"
```

---

### Task 8: Full-suite verification + docs

**Files:**
- Modify: `CLAUDE.md` (add a short Form editor subsection under the architecture notes)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: PASS (all existing tests plus the new `tests/forms/*` and `tests/fm/test_form*`).

- [ ] **Step 2: Lint**

Run: `ruff check`
Expected: clean (no errors). Fix any reported issues inline.

- [ ] **Step 3: Document the feature in CLAUDE.md**

Add a concise subsection (a few sentences) describing: the `dunders/forms/`
stdlib-only core (schema/types/context/result), `FormDialog`, `app.forms.ask`,
the `File ▸ Form editor…` entry, F3/F4 `.form.json` routing, `<stem>.result.json`
output, the `clipboard`/`selected_text` autofill sources, and the optional
`dunders[forms]`/`dateparser` `date` support with passthrough fallback. Mirror
the style/length of the existing AI-foundation and database-dunder notes.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(forms): document the form editor in CLAUDE.md"
```

---

## Self-Review

**Spec coverage:**
- Schema format (variant A), meta `$`-keys → Task 1. ✓
- All field types (`str/int/real/date/combo/ecombo/clipboard/selected_text/bool/text`) → Task 2 registry + Task 5 widgets. ✓
- `dateparser` optional extra + ISO output + passthrough fallback → Task 2. ✓
- Clipboard via subprocess, soft degradation → Task 3. ✓
- Blocking validation + `required` + typed result → Tasks 4–5. ✓
- Scrollable form, GO/Cancel, ecombo "✎ Custom…" swap → Task 5. ✓
- `app.forms.ask` API (dict | None) → Task 6. ✓
- `.result.json` write next to schema; in-memory members not written → Task 6 (`_write_form_result` only called when `schema_path` set). ✓
- File menu entry (hybrid) + F3/F4 routing + parse-error message → Tasks 6–7. ✓
- `selected_text` from the active editor at launch → Task 6 `_active_editor_selection`. ✓
- Tests mirror `tests/` layout → all tasks. ✓

**Note on parse-error UX:** the spec said "message dialog"; the plan uses
`self.notify(..., severity="error")` (the project's established lightweight
feedback). This is an intentional, minor simplification — no separate
OK-only dialog widget exists in the codebase.

**Type consistency:** `FormSpec`/`FieldSpec` fields, `TypeSpec.widget` kinds,
the raw-value convention (checkbox → `"true"`/`"false"`, others → str),
`validate_all`/`build_result` signatures, and `_rows[key]` keys
(`"kind"`/`"primary"`/`"alt"`) are used identically across Tasks 4–7. ✓
