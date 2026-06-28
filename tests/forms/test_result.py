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
