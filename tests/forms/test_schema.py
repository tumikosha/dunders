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
