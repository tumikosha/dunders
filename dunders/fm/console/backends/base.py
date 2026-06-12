"""Backend / Handle protocols shared by all command-execution backends."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, runtime_checkable


OnChunk = Callable[[bytes], None]
OnExit = Callable[[int], None]


@runtime_checkable
class Handle(Protocol):
    """One running command. Lifecycle ends when on_exit is called."""

    @property
    def running(self) -> bool: ...
    def cancel(self) -> None: ...        # SIGINT-equivalent
    def kill(self) -> None: ...          # SIGKILL-equivalent
    def write_stdin(self, data: bytes) -> None: ...  # forward bytes to child stdin
    def close_stdin(self) -> None: ...   # send EOF


@runtime_checkable
class Backend(Protocol):
    """Spawns commands and produces Handles."""

    name: str

    def spawn(
        self,
        cmd: str,
        cwd: Path,
        on_chunk: OnChunk,
        on_exit: OnExit,
    ) -> Handle: ...

    def shutdown(self) -> None: ...
