"""Maps logical names ("build") to long-lived console window targets.

Decoupled from Textual: parameterized by a factory callable so unit tests
can pass a mock target. The DundersApp wires the real ConsoleContent factory
in `app.py`.
"""

from __future__ import annotations

from typing import Callable, Generic, Protocol, TypeVar


class _TargetLike(Protocol):
    id: str
    busy: bool


T = TypeVar("T", bound=_TargetLike)


class ConsoleRegistry(Generic[T]):
    def __init__(self, factory: Callable[[str], T]) -> None:
        self._factory = factory
        self._items: dict[str, T] = {}
        self._anon_counter = 0

    def get_or_create(self, name: str | None, *, anonymous: bool = False) -> T:
        if anonymous:
            self._anon_counter += 1
            target_id = f"console-anon-{self._anon_counter}"
        elif name is None:
            target_id = "console-default"
        else:
            target_id = f"console-{name}"
        if target_id in self._items:
            return self._items[target_id]
        target = self._factory(target_id)
        self._items[target_id] = target
        return target

    def is_busy(self, name: str) -> bool:
        target_id = "console-default" if name in (None, "default") else f"console-{name}"
        t = self._items.get(target_id)
        return bool(t and t.busy)

    def get(self, target_id: str) -> T | None:
        return self._items.get(target_id)

    def remove(self, target_id: str) -> None:
        self._items.pop(target_id, None)

    def all(self) -> list[T]:
        return list(self._items.values())
