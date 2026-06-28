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
    assert T.get_type("int").widget == "int"
    assert T.get_type("real").widget == "real"
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


def test_combo_without_options_accepts_any():
    tc = T.get_type("combo")
    assert tc.validate("anything", FieldSpec(key="x", type="combo", label="x")) is None


def test_date_passthrough_when_dateparser_absent(monkeypatch):
    monkeypatch.setattr(T, "_dateparser", None)
    td = T.get_type("date")
    f = _f(type="date")
    assert td.validate("whenever", f) is None
    assert td.convert("whenever", f) == "whenever"


def test_date_iso_when_dateparser_present():
    pytest.importorskip("dateparser")
    td = T.get_type("date")
    f = FieldSpec(key="d", type="date", label="d")
    assert td.validate("2026-06-27", f) is None
    assert td.convert("2026-06-27", f) == "2026-06-27"


def test_real_rejects_non_finite():
    tr = T.get_type("real")
    f = _f(type="real")
    # non-finite values must be rejected
    assert tr.validate("nan", f) is not None
    assert tr.validate("inf", f) is not None
    assert tr.validate("-inf", f) is not None
    # finite values must still be accepted
    assert tr.validate("3.14", f) is None
    assert tr.validate("-1.5", f) is None
    assert tr.validate("0", f) is None


def test_int_rejects_underscores_and_spaces():
    ti = T.get_type("int")
    f = _f(type="int")
    # digit-group underscores must be rejected
    assert ti.validate("1_000", f) is not None
    # surrounding whitespace must be rejected
    assert ti.validate(" 42 ", f) is not None
    # plain integers must still be accepted
    assert ti.validate("42", f) is None
    assert ti.validate("-7", f) is None
