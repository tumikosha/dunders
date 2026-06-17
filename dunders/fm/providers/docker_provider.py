"""DockerProvider — browse Docker containers and their filesystems via the CLI,
locally or on a remote host over SSH.

Addressing — ``VfsPath(scheme="docker", root=<endpoint>, parts=...)``:
  * ``root=""``               → local Docker daemon
  * ``root="ssh://u@h:port"`` → remote daemon, reached over SSH
  * ``parts=()``             → the container index of that endpoint
  * ``parts=("web",)``       → container ``web`` (its ``/``)
  * ``parts=("web","etc")``  → ``/etc`` inside container ``web``

Transport: locally we shell out to ``docker``; for a remote endpoint we run the
remote ``docker`` CLI over our OWN SSH connection, which we keep multiplexed via
an auto-managed ControlMaster (one connection per endpoint, reused by every
call — ~15 ms/call vs ~200 ms for a fresh SSH each time). No SFTP/paramiko.

Caveats (v1): browsing a container's filesystem requires it to be RUNNING
(`docker exec`); stopped containers appear in the index but error on entry.
Remote auth is key/agent only (``BatchMode=yes``); the remote needs the
``docker`` CLI on the SSH user's PATH with socket access. Listing parses
`ls -la --full-time` (best-effort for exotic names/locales). open_read /
open_write buffer the whole file in memory. Host keys use ``accept-new``.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import time
from typing import BinaryIO

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProviderAction, ProviderColumn
from dunders.fm.actions import OpError, OpResult, ProgressCallback
from dunders.fm.file_entry import FileEntry

__all__ = ["DockerProvider", "docker_available"]

_DOCKER = "docker"
_REMOTE_SCHEMES = ("ssh://", "tcp://", "unix://")


def _parse_spec(spec: str) -> tuple[str, tuple[str, ...]]:
    """``[docker:]<spec>`` → ``(endpoint, parts)``.

    Local: ``""``/``web``/``web/var/log`` → ``("", ())`` / ``("", ("web",))`` /
    ``("", ("web","var","log"))``. Remote: ``ssh://[user@]host[:port][/name[/path]]``
    → ``("ssh://host", ("name", "path", …))``.
    """
    s = spec.strip()
    if s.startswith("docker:"):
        s = s[len("docker:"):]
    for sch in _REMOTE_SCHEMES:
        if s.startswith(sch):
            netloc, _, path = s[len(sch):].partition("/")
            parts = tuple(p for p in path.split("/") if p)
            return sch + netloc, parts
    if s.startswith("//"):
        s = s[2:]
    s = s.strip("/")
    if not s:
        return "", ()
    return "", tuple(p for p in s.split("/") if p)


def _parse_endpoint(endpoint: str) -> tuple[str, str | None]:
    """``ssh://[user@]host[:port]`` → (ssh-target ``[user@]host``, port|None)."""
    s = endpoint
    for sch in _REMOTE_SCHEMES:
        if s.startswith(sch):
            s = s[len(sch):]
            break
    s = s.split("/", 1)[0]  # defensive: endpoints carry no path
    prefix = ""
    if "@" in s:
        user, _, s = s.rpartition("@")
        prefix = user + "@"
    if ":" in s:
        host, _, port = s.rpartition(":")
        if port.isdigit():
            return prefix + host, port
    return prefix + s, None


_available: bool | None = None


def docker_available() -> bool:
    """Whether the local `docker` CLI is present and its daemon answers
    `version`. Cached after the first call (registry build time). Remote
    endpoints still need this — the scheme is gated on a usable local CLI."""
    global _available
    if _available is None:
        if shutil.which(_DOCKER) is None:
            _available = False
        else:
            try:
                proc = subprocess.run(
                    [_DOCKER, "version"], capture_output=True, timeout=10
                )
                _available = proc.returncode == 0
            except Exception:
                _available = False
    return _available


class _DockerWriter(io.BytesIO):
    """Buffers bytes; on close packs them as a one-member tar and `docker cp`s
    the member into the container's parent directory."""

    def __init__(self, provider: "DockerProvider", loc: VfsPath) -> None:
        super().__init__()
        self._provider = provider
        self._loc = loc
        self._flushed = False

    def close(self) -> None:
        if not self._flushed and not self.closed:
            self._flushed = True
            data = self.getvalue()
            loc = self._loc
            container = loc.parts[0]
            member = loc.parts[-1]
            parent = "/" + "/".join(loc.parts[1:-1])
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                info = tarfile.TarInfo(name=member)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            self._provider._run(
                ["cp", "-", f"{container}:{parent}"],
                endpoint=loc.root, input=buf.getvalue(),
            )
        super().close()


class DockerProvider:
    scheme = "docker"
    display_name = "Docker"
    capabilities = frozenset({"read", "write", "stream", "slow"})
    # Greyed hint in the "_" → Docker open dialog.
    open_placeholder = "empty = local containers · name[/path] · ssh://user@host[:port][/name]"
    # Opening with an empty spec is valid here — it lands on the local index.
    accepts_empty_open = True

    def __init__(self) -> None:
        # Endpoints whose SSH ControlMaster has been (attempted to be) started.
        self._masters: set[str] = set()

    # -- transport ---------------------------------------------------------

    @staticmethod
    def _master_sock(endpoint: str) -> str:
        d = os.path.expanduser("~/.cache/dunders/ssh")
        os.makedirs(d, exist_ok=True)
        h = hashlib.sha1(endpoint.encode()).hexdigest()[:12]
        return os.path.join(d, f"cm-{h}.sock")

    def _ensure_master(self, endpoint: str) -> None:
        """Start one multiplexing SSH master per endpoint (idempotent). On
        failure we still proceed — ControlMaster=auto opens a direct connection."""
        if endpoint in self._masters:
            return
        self._masters.add(endpoint)
        target, port = _parse_endpoint(endpoint)
        args = [
            "ssh", "-fN", "-M", "-S", self._master_sock(endpoint),
            "-o", "ControlMaster=auto", "-o", "ControlPersist=600",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
        ]
        if port:
            args += ["-p", port]
        args.append(target)
        try:
            subprocess.run(args, capture_output=True, timeout=20)
        except Exception:
            pass

    def _ssh_prefix(self, endpoint: str) -> list[str]:
        target, port = _parse_endpoint(endpoint)
        opts = [
            "ssh",
            "-o", f"ControlPath={self._master_sock(endpoint)}",
            "-o", "ControlMaster=auto", "-o", "ControlPersist=600",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
        ]
        if port:
            opts += ["-p", port]
        return opts + [target]

    # ``input`` mirrors subprocess.run's own kwarg name on purpose (the writer
    # pipes a tar via `_run([...], input=...)`).
    def _run(self, args: list[str], *, endpoint: str = "", input: bytes | None = None) -> bytes:
        """Run ``docker <args>`` — locally, or on ``endpoint`` over multiplexed
        SSH. Return stdout bytes; raise OSError on failure/timeout."""
        if not endpoint:
            cmd = [_DOCKER, *args]
        else:
            self._ensure_master(endpoint)
            remote = "docker " + " ".join(shlex.quote(a) for a in args)
            cmd = [*self._ssh_prefix(endpoint), remote]
        try:
            proc = subprocess.run(cmd, input=input, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise OSError(f"docker {args[0] if args else ''} timed out") from exc
        if proc.returncode != 0:
            msg = proc.stderr.decode(errors="replace").strip() or "docker error"
            raise OSError(msg)
        return proc.stdout

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _remote(loc: VfsPath) -> str:
        """Path inside the container (``parts[0]`` is the container name)."""
        return "/" + "/".join(loc.parts[1:])

    def _is_running(self, name: str, endpoint: str = "") -> bool:
        out = self._run(["inspect", "-f", "{{.State.Running}}", name], endpoint=endpoint)
        return out.decode(errors="replace").strip() == "true"

    # -- scan --------------------------------------------------------------

    def scan(
        self,
        loc: VfsPath,
        *,
        show_hidden: bool = False,
        include_parent: bool = True,
    ) -> list[FileEntry]:
        if not loc.parts:
            return self._scan_index(loc.root)
        return self._scan_fs(loc, show_hidden=show_hidden,
                             include_parent=include_parent)

    _GLYPHS = {
        "running": ("▶", "success"),
        "paused": ("⏸", "warning"),
        "restarting": ("↻", "warning"),
    }

    def _scan_index(self, endpoint: str = "") -> list[FileEntry]:
        out = self._run(["ps", "-a", "--format", "{{json .}}"], endpoint=endpoint)
        entries: list[FileEntry] = []
        for line in out.decode(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            name = (rec.get("Names") or rec.get("ID") or "").split(",")[0]
            if not name:
                continue
            state = (rec.get("State") or "").lower()
            glyph, role = self._GLYPHS.get(state, ("■", "muted"))
            extra = {"docker.state": state, "glyph": glyph, "glyph_role": role}
            # Compose membership is captured here (cheap — already in `docker ps`
            # output) so applies_to never shells out during rendering.
            if "com.docker.compose.project=" in (rec.get("Labels") or ""):
                extra["docker.compose"] = "1"
            entries.append(FileEntry(
                loc=VfsPath(scheme="docker", root=endpoint, parts=(name,)),
                name=name, size=0, mtime=0.0, is_dir=True,
                extra=extra,
            ))
        return entries

    def _parent_entry(self, loc: VfsPath) -> FileEntry:
        # parts-based parent: ("web","etc")→("web",)→() (the endpoint index).
        return FileEntry(loc=loc.parent, name="..", size=0, mtime=0.0, is_dir=True)

    def _scan_fs(self, loc: VfsPath, *, show_hidden: bool, include_parent: bool) -> list[FileEntry]:
        container = loc.parts[0]
        if not self._is_running(container, loc.root):
            raise OSError(
                f"container {container!r} is not running — start it to browse"
            )
        args = ["exec", container, "ls", "-la", "--full-time"]
        if show_hidden:
            args.append("-A")
        args.append(self._remote(loc) or "/")
        out = self._run(args, endpoint=loc.root)
        entries: list[FileEntry] = []
        if include_parent:
            entries.append(self._parent_entry(loc))
        for line in out.decode(errors="replace").splitlines():
            entry = self._parse_ls_line(line, loc, show_hidden)
            if entry is not None:
                entries.append(entry)
        return entries

    def _parse_ls_line(self, line: str, loc: VfsPath, show_hidden: bool) -> FileEntry | None:
        # "drwxr-xr-x 2 root root 4096 2026-06-14 10:00:00.000000000 +0000 name"
        # 9 fixed columns (perms links owner group size date time tz) then name.
        parts = line.split(None, 8)
        if len(parts) < 9 or parts[0] in ("total", ""):
            return None
        perms = parts[0]
        try:
            size = int(parts[4])
        except ValueError:
            return None
        name = parts[8]
        is_symlink = perms.startswith("l")
        if is_symlink and " -> " in name:
            name = name.split(" -> ", 1)[0]
        if name in (".", ".."):
            return None
        if not show_hidden and name.startswith("."):
            return None
        mtime = self._parse_mtime(parts[5], parts[6])
        is_dir = perms.startswith("d")
        return FileEntry(
            loc=loc.child(name),
            name=name,
            size=size,
            mtime=mtime,
            is_dir=is_dir,
            is_symlink=is_symlink,
            is_executable="x" in perms[1:10],
        )

    @staticmethod
    def _parse_mtime(date_s: str, time_s: str) -> float:
        try:
            stamp = f"{date_s} {time_s.split('.')[0]}"
            return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            return 0.0

    # -- file ops ----------------------------------------------------------

    def is_dir(self, loc: VfsPath) -> bool:
        if len(loc.parts) <= 1:  # the index, or a container root
            return True
        try:
            self._run(["exec", loc.parts[0], "test", "-d", self._remote(loc)],
                      endpoint=loc.root)
            return True
        except OSError:
            return False

    def open_read(self, loc: VfsPath) -> BinaryIO:
        data = self._run(["exec", loc.parts[0], "cat", "--", self._remote(loc)],
                         endpoint=loc.root)
        return io.BytesIO(data)

    def mkdir(self, parent: VfsPath, name: str) -> OpResult:
        remote = "/" + "/".join((*parent.parts[1:], name))
        try:
            self._run(["exec", parent.parts[0], "mkdir", "-p", remote],
                      endpoint=parent.root)
        except OSError as exc:
            return OpResult(errors=[OpError(loc=parent.child(name), reason=str(exc))])
        return OpResult()

    def delete(
        self,
        targets: list[VfsPath],
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event=None,
    ) -> OpResult:
        result = OpResult()
        for i, t in enumerate(targets, 1):
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                break
            try:
                self._run(["exec", t.parts[0], "rm", "-rf", "--", self._remote(t)],
                          endpoint=t.root)
            except OSError as exc:
                result.errors.append(OpError(loc=t, reason=str(exc)))
            if on_progress is not None:
                on_progress(i, len(targets))
        return result

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        if not overwrite:
            try:
                self._run(["exec", loc.parts[0], "test", "-e", self._remote(loc)],
                          endpoint=loc.root)
                raise FileExistsError(f"{self._remote(loc)} already exists")
            except OSError as exc:
                if isinstance(exc, FileExistsError):
                    raise
                # `test -e` exit!=0 → file does not exist → OK to write
        return _DockerWriter(self, loc)

    def needs_password(self, spec: str) -> bool:  # key/agent SSH auth only
        return False

    def resolve_target(
        self, spec: str, *, base: VfsPath, password: str | None = None
    ) -> VfsPath | None:
        endpoint, parts = _parse_spec(spec)
        if not parts:
            return VfsPath(scheme="docker", root=endpoint, parts=())
        if not self._is_running(parts[0], endpoint):
            raise OSError(
                f"container {parts[0]!r} not found or not running "
                f"(start it, or check the name)"
            )
        return VfsPath(scheme="docker", root=endpoint, parts=parts)

    # -- actions -----------------------------------------------------------

    @staticmethod
    def _state(entry) -> str:
        try:
            return entry.extra.get("docker.state", "")
        except AttributeError:
            return ""

    def _compose_labels(self, name: str, endpoint: str = "") -> dict | None:
        out = self._run(["inspect", "-f", "{{json .Config.Labels}}", name],
                        endpoint=endpoint)
        labels = json.loads(out.decode(errors="replace") or "{}") or {}
        if "com.docker.compose.project" in labels:
            return labels
        return None

    def _has_compose(self, entry) -> bool:
        # Cheap: read the flag captured at scan time. MUST stay subprocess-free —
        # this runs in applies_to during rendering, once per visible row.
        try:
            return bool(entry.extra.get("docker.compose"))
        except AttributeError:
            return False

    @staticmethod
    def _container(loc: VfsPath) -> str:
        return loc.parts[0] if loc.parts else ""

    def _simple_action(self, verb: str, locs: list[VfsPath]) -> OpResult:
        result = OpResult()
        for loc in locs:
            name = self._container(loc)
            if not name:
                continue
            try:
                self._run([verb, name], endpoint=loc.root)
            except OSError as exc:
                result.errors.append(OpError(loc=loc, reason=str(exc)))
        return result

    def _remove(self, locs: list[VfsPath]) -> OpResult:
        result = OpResult()
        for loc in locs:
            name = self._container(loc)
            if not name:
                continue
            try:
                self._run(["rm", "-f", name], endpoint=loc.root)
            except OSError as exc:
                result.errors.append(OpError(loc=loc, reason=str(exc)))
        return result

    def _rebuild(self, locs: list[VfsPath]) -> OpResult:
        result = OpResult()
        for loc in locs:
            name = self._container(loc)
            if not name:
                continue
            try:
                labels = self._compose_labels(name, loc.root)
            except OSError as exc:
                result.errors.append(OpError(loc=loc, reason=str(exc)))
                continue
            if labels is None:
                result.errors.append(
                    OpError(loc=loc, reason="not part of a compose project"))
                continue
            proj = labels["com.docker.compose.project"]
            service = labels.get("com.docker.compose.service", "")
            workdir = labels.get("com.docker.compose.project.working_dir", "")
            args = ["compose", "-p", proj]
            if workdir:
                args += ["--project-directory", workdir]
            args += ["up", "-d", "--build"]
            if service:
                args.append(service)
            try:
                self._run(args, endpoint=loc.root)
            except OSError as exc:
                result.errors.append(OpError(loc=loc, reason=str(exc)))
        return result

    def actions(self) -> list[ProviderAction]:
        stopped = {"exited", "created", "dead"}
        running = {"running", "paused", "restarting"}
        return [
            ProviderAction(
                id="docker.start", label="Start", icon="▶", hotkey="f5",
                applies_to=lambda e: self._state(e) in stopped,
                run=lambda locs: self._simple_action("start", locs)),
            ProviderAction(
                id="docker.stop", label="Stop", icon="⏹", hotkey="f6",
                applies_to=lambda e: self._state(e) in running,
                run=lambda locs: self._simple_action("stop", locs)),
            ProviderAction(
                id="docker.restart", label="Restart", icon="↻", hotkey="f7",
                applies_to=lambda e: self._state(e) in running,
                run=lambda locs: self._simple_action("restart", locs)),
            ProviderAction(
                id="docker.remove", label="Remove", icon="✕", hotkey="f8",
                # any container (regardless of state) — but never a file inside one
                applies_to=lambda e: bool(self._state(e)), run=self._remove),
            ProviderAction(
                id="docker.rebuild", label="Rebuild (compose)", icon="⟳",
                applies_to=self._has_compose, run=self._rebuild),
        ]

    # Sort rank for the "S" column — running first, dead last.
    _STATE_RANK = {
        "running": 0, "restarting": 1, "paused": 2,
        "created": 3, "exited": 4, "dead": 5,
    }

    def columns(self, loc: VfsPath) -> list[ProviderColumn]:
        # Only the container index (local or remote) gets the state column;
        # inside a container the rows are ordinary files (normal Size/Date).
        if loc.parts:
            return []
        return [ProviderColumn(
            key="docker.state",
            label="S",
            width=3,
            value=lambda e: (e.extra.get("glyph") or " "),
            sort_key=lambda e: self._STATE_RANK.get(e.extra.get("docker.state", ""), 9),
        )]

    def copy_within(self, sources, dest, *, rename_to=None,
                    on_progress=None, on_status=None,
                    cancel_event=None) -> OpResult | None:
        return None

    def move_within(self, sources, dest, *, rename_to=None,
                    on_progress=None, cancel_event=None) -> OpResult | None:
        return None
