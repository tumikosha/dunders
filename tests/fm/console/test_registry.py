from __future__ import annotations

from dataclasses import dataclass, field

from dunders.fm.console.registry import ConsoleRegistry


@dataclass
class _MockTarget:
    id: str
    busy: bool = False
    appended: list[bytes] = field(default_factory=list)
    closed: bool = False

    def append(self, b: bytes) -> None:
        self.appended.append(b)

    def mark_done(self, rc: int) -> None:
        self.busy = False


def _factory_factory():
    created: list[_MockTarget] = []

    def factory(target_id: str) -> _MockTarget:
        t = _MockTarget(id=target_id)
        created.append(t)
        return t

    return factory, created


def test_default_target_lazy():
    factory, created = _factory_factory()
    r = ConsoleRegistry(factory=factory)
    t = r.get_or_create(None)
    assert t.id == "console-default"
    assert created == [t]
    assert r.get_or_create(None) is t


def test_named_target_lazy():
    factory, created = _factory_factory()
    r = ConsoleRegistry(factory=factory)
    t1 = r.get_or_create("build")
    assert t1.id == "console-build"
    t2 = r.get_or_create("build")
    assert t1 is t2


def test_anonymous_targets_increment():
    factory, _ = _factory_factory()
    r = ConsoleRegistry(factory=factory)
    a1 = r.get_or_create(None, anonymous=True)
    a2 = r.get_or_create(None, anonymous=True)
    assert a1.id == "console-anon-1"
    assert a2.id == "console-anon-2"


def test_busy_query():
    factory, _ = _factory_factory()
    r = ConsoleRegistry(factory=factory)
    t = r.get_or_create("build")
    assert not r.is_busy("build")
    t.busy = True
    assert r.is_busy("build")


def test_remove_drops_record():
    factory, _ = _factory_factory()
    r = ConsoleRegistry(factory=factory)
    r.get_or_create("foo")
    r.remove("console-foo")
    assert not r.is_busy("foo")
    factory2, created = _factory_factory()
    r2 = ConsoleRegistry(factory=factory2)
    r2.get_or_create("foo")
    assert created and created[0].id == "console-foo"
