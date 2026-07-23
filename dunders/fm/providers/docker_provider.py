"""DockerProvider — browse Docker objects and container filesystems via the CLI,
locally or on a remote host over SSH.  Implements the v2 PyCharm-style hierarchy.

Hierarchy and typed tokens
--------------------------
``VfsPath(scheme="docker", root=<endpoint>, parts=...)``

  root=""                       local Docker daemon
  root="ssh://u@h:port"         remote daemon via multiplexed SSH

  parts=()                      top level — compose groups + section dirs
  parts=("compose:<proj>",)     compose project group (lists services)
  parts=("compose:<proj>",
         "service:<svc>")       service (lists its containers)
  parts=(..., "container:<n>")  container root (browseable FS via docker exec)
  parts=(..., "container:<n>",
         "etc", "nginx")        /etc/nginx inside that container
  parts=("containers",)         Containers section (standalone containers only)
  parts=("images",)             Images section
  parts=("networks",)           Networks section
  parts=("volumes",)            Volumes section
  parts=("volumes",
         "volume:<name>")       named volume root (browseable via helper container)
  parts=("volumes",
         "volume:<name>", …)    path inside a named volume

Each ``parts`` element that names a Docker object carries a typed prefix
(``compose:``, ``service:``, ``container:``, ``image:``, ``network:``,
``volume:``) so ``_level()`` can determine the current context without
ambiguity and file-system path segments can coexist with object tokens.

Transport
---------
Local: ``docker <args>`` subprocess.  Remote: the same CLI executed over a
multiplexed SSH ControlMaster (~15 ms/call vs ~200 ms for a fresh connection).
No SFTP/paramiko; auth is key/agent only (``BatchMode=yes``).

Phase notes
-----------
Phase A (current): hierarchy scan, container-FS ops (exec), resolve_target,
  per-level columns.
Phase D: ``_scan_volume`` mounts named volumes via a short-lived ``busybox``
  helper container (``docker run --rm -v <vol>:/mnt busybox …``); ``busybox``
  is pulled from Docker Hub on first use.  Browsing is read-only in v1.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import time
from typing import BinaryIO

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProviderAction, ProviderColumn, PreviewResult
from dunders.fm.actions import OpError, OpResult, ProgressCallback
from dunders.fm.file_entry import FileEntry

__all__ = ["DockerProvider", "docker_available"]

_DOCKER = "docker"
_REMOTE_SCHEMES = ("ssh://", "tcp://", "unix://")

_COMPOSE = "compose:"
_SERVICE = "service:"
_CONTAINER = "container:"
_IMAGE = "image:"
_NETWORK = "network:"
_VOLUME = "volume:"
_SECTIONS = ("containers", "images", "networks", "volumes")


def _typed(part: str, prefix: str) -> str | None:
    return part[len(prefix):] if part.startswith(prefix) else None


def _container_name(loc: VfsPath) -> str:
    for p in loc.parts:
        v = _typed(p, _CONTAINER)
        if v is not None:
            return v
    return ""


def _volume_name(loc: VfsPath) -> str:
    for p in loc.parts:
        v = _typed(p, _VOLUME)
        if v is not None:
            return v
    return ""


def _fs_path(loc: VfsPath, prefix: str) -> str:
    for i, p in enumerate(loc.parts):
        if p.startswith(prefix):
            tail = loc.parts[i + 1:]
            return "/" + "/".join(tail) if tail else "/"
    return "/"


def _level(loc: VfsPath) -> str:
    parts = loc.parts
    if not parts:
        return "top"
    if any(p.startswith(_CONTAINER) for p in parts):
        return "cfs"
    if any(p.startswith(_VOLUME) for p in parts):
        return "vfs"
    last = parts[-1]
    if last.startswith(_COMPOSE):
        return "compose"
    if last.startswith(_SERVICE):
        return "service"
    if last in _SECTIONS:
        return last
    return "top"  # defensive


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

    def close(self):
        if not self._flushed and not self.closed:
            self._flushed = True
            data = self.getvalue()
            loc = self._loc
            container = _container_name(loc)
            member = loc.parts[-1]
            parent = _fs_path(loc.parent, _CONTAINER)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                info = tarfile.TarInfo(name=member)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            self._provider._run(["cp", "-", f"{container}:{parent}"],
                                endpoint=loc.root, input=buf.getvalue())
        io.BytesIO.close(self)


class DockerProvider:
    scheme = "docker"
    display_name = "Docker"
    capabilities = frozenset({"read", "write", "stream", "slow"})
    # Greyed hint in the "_" → Docker open dialog.
    open_placeholder = (
        "containers/images/networks/volumes · container-name[/path] · "
        "ssh://user@host[:port][/name]"
    )
    # Opening with an empty spec is valid here — it lands on the local index.
    accepts_empty_open = True

    _HELPER = "busybox"

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

    def _is_running(self, name: str, endpoint: str = "") -> bool:
        out = self._run(["inspect", "-f", "{{.State.Running}}", name], endpoint=endpoint)
        return out.decode(errors="replace").strip() == "true"

    # -- scan --------------------------------------------------------------

    _SECTION_LABELS = {
        "containers": "Containers", "images": "Images",
        "networks": "Networks", "volumes": "Volumes",
    }

    def scan(self, loc, *, show_hidden=False, include_parent=True):
        level = _level(loc)
        if level == "top":
            return self._scan_top(loc)
        if level in _SECTIONS:
            return self._scan_section(loc, level, include_parent=include_parent)
        if level == "compose":
            return self._scan_services(loc, include_parent=include_parent)
        if level == "service":
            return self._scan_service_containers(loc, include_parent=include_parent)
        if level == "cfs":
            return self._scan_fs(loc, show_hidden=show_hidden, include_parent=include_parent)
        if level == "vfs":
            return self._scan_volume(loc, show_hidden=show_hidden, include_parent=include_parent)
        return []

    def _ps_records(self, endpoint):
        out = self._run(["ps", "-a", "--format", "{{json .}}"], endpoint=endpoint)
        recs = []
        for line in out.decode(errors="replace").splitlines():
            line = line.strip()
            if line:
                recs.append(json.loads(line))
        return recs

    @staticmethod
    def _labels_map(rec):
        out = {}
        for kv in (rec.get("Labels") or "").split(","):
            k, _, v = kv.partition("=")
            if k:
                out[k.strip()] = v.strip()
        return out

    def _scan_top(self, loc):
        recs = self._ps_records(loc.root)
        projects = sorted({
            self._labels_map(r).get("com.docker.compose.project")
            for r in recs
            if self._labels_map(r).get("com.docker.compose.project")
        })
        entries = [
            FileEntry(
                loc=loc.child(_COMPOSE + proj), name=proj, size=0, mtime=0.0,
                is_dir=True,
                extra={"docker.compose": "1", "glyph": "▤", "glyph_role": "muted"},
            )
            for proj in projects
        ]
        for key in _SECTIONS:
            entries.append(FileEntry(
                loc=loc.child(key), name=self._SECTION_LABELS[key],
                size=0, mtime=0.0, is_dir=True,
            ))
        return entries

    _GLYPHS = {
        "running": ("▶", "success"),
        "paused": ("⏸", "warning"),
        "restarting": ("↻", "warning"),
    }

    def _parent_entry(self, loc):
        return FileEntry(loc=loc.parent, name="..", size=0, mtime=0.0, is_dir=True)

    def _container_entry(self, parent, rec):
        name = (rec.get("Names") or rec.get("ID") or "").split(",")[0]
        state = (rec.get("State") or "").lower()
        glyph, role = self._GLYPHS.get(state, ("■", "muted"))
        return FileEntry(
            loc=parent.child(_CONTAINER + name), name=name, size=0, mtime=0.0,
            is_dir=True,
            extra={
                "docker.state": state, "glyph": glyph, "glyph_role": role,
                "docker.image": rec.get("Image") or "",
                "docker.status": rec.get("Status") or "",
            },
        )

    def _scan_section(self, loc, section, *, include_parent=True):
        head = [self._parent_entry(loc)] if include_parent else []
        if section == "containers":
            recs = [r for r in self._ps_records(loc.root)
                    if "com.docker.compose.project" not in self._labels_map(r)]
            return head + [self._container_entry(loc, r) for r in recs]
        if section == "images":
            return head + self._scan_images(loc)
        if section == "networks":
            return head + self._scan_networks(loc)
        if section == "volumes":
            return head + self._scan_volumes(loc)
        return head

    def _project_recs(self, loc, proj):
        return [r for r in self._ps_records(loc.root)
                if self._labels_map(r).get("com.docker.compose.project") == proj]

    def _scan_services(self, loc, *, include_parent=True):
        proj = _typed(loc.parts[-1], _COMPOSE) or ""
        services = sorted({
            self._labels_map(r).get("com.docker.compose.service", "")
            for r in self._project_recs(loc, proj)
        } - {""})
        head = [self._parent_entry(loc)] if include_parent else []
        return head + [
            FileEntry(loc=loc.child(_SERVICE + s), name=s, size=0, mtime=0.0, is_dir=True)
            for s in services
        ]

    def _scan_service_containers(self, loc, *, include_parent=True):
        proj = _typed(loc.parts[-2], _COMPOSE) or ""
        svc = _typed(loc.parts[-1], _SERVICE) or ""
        recs = [r for r in self._project_recs(loc, proj)
                if self._labels_map(r).get("com.docker.compose.service") == svc]
        head = [self._parent_entry(loc)] if include_parent else []
        return head + [self._container_entry(loc, r) for r in recs]

    def _json_lines(self, args, endpoint):
        out = self._run(args, endpoint=endpoint)
        for line in out.decode(errors="replace").splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)

    def _scan_images(self, loc):
        rows = []
        for r in self._json_lines(["images", "--format", "{{json .}}"], loc.root):
            repo, tag, iid = r.get("Repository", ""), r.get("Tag", ""), r.get("ID", "")
            name = f"{repo}:{tag}" if repo and repo != "<none>" else iid
            rows.append(FileEntry(
                loc=loc.child(_IMAGE + iid), name=name, size=0, mtime=0.0,
                is_dir=False,
                extra={"docker.size": r.get("Size", ""),
                       "docker.created": r.get("CreatedSince", ""),
                       "docker.id": iid},
            ))
        return rows

    def _scan_networks(self, loc):
        rows = []
        for r in self._json_lines(["network", "ls", "--format", "{{json .}}"], loc.root):
            nid = r.get("ID", "")
            rows.append(FileEntry(
                loc=loc.child(_NETWORK + nid), name=r.get("Name", nid),
                size=0, mtime=0.0, is_dir=False,
                extra={"docker.driver": r.get("Driver", ""),
                       "docker.scope": r.get("Scope", ""), "docker.id": nid},
            ))
        return rows

    def _scan_volumes(self, loc):
        rows = []
        # One pass to learn which container(s) reference each volume, so the
        # panel can show a "Used by" column (a volume's name — often an opaque
        # hash — reveals nothing about its owner on its own).
        umap = self._container_volume_map(loc.root)
        for r in self._volume_rows(loc.root):
            name = r.get("Name", "")
            rows.append(FileEntry(
                loc=loc.child(_VOLUME + name), name=name, size=0, mtime=0.0,
                is_dir=True,
                extra={"docker.driver": r.get("Driver", ""),
                       "docker.mountpoint": r.get("Mountpoint", ""),
                       # ``Size`` only comes from the disk-usage scan below; the
                       # ``volume ls`` fallback leaves it blank (dash in the col).
                       "docker.size": r.get("Size", ""),
                       "docker.usedby": self._fmt_usedby(umap.get(name, []))},
            ))
        return rows

    def _container_volume_map(self, endpoint) -> dict[str, list[tuple[str, str]]]:
        """Map each volume name to the containers that mount it, as
        ``{volume: [(container, image), …]}``.

        ``docker ps`` truncates volume names in its ``.Mounts`` field, so the
        full names come from ``docker inspect`` → ``.Mounts[].Name`` instead. One
        ``ps -aq`` + one ``inspect`` of every container. Returns ``{}`` on any
        failure so the "Used by" column simply stays blank."""
        out: dict[str, list[tuple[str, str]]] = {}
        try:
            ids = self._run(["ps", "-a", "-q"], endpoint=endpoint).split()
            if not ids:
                return out
            data = json.loads(
                self._run(["inspect", *[i.decode() for i in ids]],
                          endpoint=endpoint).decode(errors="replace")
            )
            for c in data:
                name = (c.get("Name") or "").lstrip("/")
                image = (c.get("Config") or {}).get("Image", "")
                for m in c.get("Mounts") or []:
                    vol = m.get("Name")
                    if vol:
                        out.setdefault(vol, []).append((name, image))
        except Exception:
            return {}
        return out

    @staticmethod
    def _fmt_usedby(users: list[tuple[str, str]]) -> str:
        """Column text for a volume's containers: the first container name, plus
        ``+N`` when more than one references it. Blank when unused (dangling)."""
        if not users:
            return ""
        first = users[0][0]
        return first if len(users) == 1 else f"{first} +{len(users) - 1}"

    def _volume_users(self, ident: str, endpoint) -> list[tuple[str, str, str]]:
        """Containers referencing volume ``ident`` as ``[(name, image, state)]``
        via ``docker ps --filter volume=`` (full names, one CLI call). Returns
        ``[]`` on failure or when nothing references the volume."""
        try:
            out = self._run(
                ["ps", "-a", "--filter", f"volume={ident}", "--format",
                 "{{.Names}}\t{{.Image}}\t{{.State}}"],
                endpoint=endpoint,
            )
        except Exception:
            return []
        users = []
        for line in out.decode(errors="replace").splitlines():
            parts = line.split("\t")
            if parts and parts[0]:
                name = parts[0]
                image = parts[1] if len(parts) > 1 else ""
                state = parts[2] if len(parts) > 2 else ""
                users.append((name, image, state))
        return users

    def _volume_rows(self, endpoint):
        """Volume records including a ``Size`` field via ``docker system df -v``
        (a disk-usage scan — slower than ``volume ls`` but the only source of
        per-volume size). Falls back to ``volume ls`` (no Size) when df is
        unavailable or its verbose ``--format`` isn't supported."""
        try:
            out = self._run(
                ["system", "df", "-v", "--format", "{{json .Volumes}}"],
                endpoint=endpoint,
            )
            data = json.loads(out.decode(errors="replace").strip() or "null")
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return list(
            self._json_lines(["volume", "ls", "--format", "{{json .}}"], endpoint)
        )

    @staticmethod
    def _parse_size(s: str) -> float:
        """Parse a Docker human-readable size ("1.2GB", "0B", "512kB") to a byte
        count for sorting. Docker prints decimal (1000-based) units; binary
        (``*iB``) units are handled too. Returns 0.0 on blank/unparseable input."""
        m = re.match(r"^\s*([0-9.]+)\s*([A-Za-z]*)\s*$", s or "")
        if not m:
            return 0.0
        units = {"": 1, "B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12,
                 "PB": 1e15, "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3,
                 "TIB": 1024 ** 4, "PIB": 1024 ** 5}
        try:
            return float(m.group(1)) * units.get(m.group(2).upper(), 1)
        except ValueError:
            return 0.0

    def _scan_fs(self, loc: VfsPath, *, show_hidden: bool, include_parent: bool) -> list[FileEntry]:
        container = _container_name(loc)
        if not self._is_running(container, loc.root):
            raise OSError(f"container {container!r} is not running — start it to browse")
        args = ["exec", container, "ls", "-la", "--full-time"]
        if show_hidden:
            args.append("-A")
        args.append(_fs_path(loc, _CONTAINER))
        out = self._run(args, endpoint=loc.root)
        entries = [self._parent_entry(loc)] if include_parent else []
        for line in out.decode(errors="replace").splitlines():
            e = self._parse_ls_line(line, loc, show_hidden)
            if e is not None:
                entries.append(e)
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

    def _volume_mount(self, loc: VfsPath) -> str:
        return f"{_volume_name(loc)}:/mnt"

    def _scan_volume(self, loc: VfsPath, *, show_hidden: bool, include_parent: bool) -> list[FileEntry]:
        sub = _fs_path(loc, _VOLUME)
        target = "/mnt" + ("" if sub == "/" else sub)
        args = ["run", "--rm", "-v", self._volume_mount(loc), self._HELPER,
                "ls", "-la", "--full-time"]
        if show_hidden:
            args.append("-A")
        args.append(target)
        out = self._run(args, endpoint=loc.root)
        entries = [self._parent_entry(loc)] if include_parent else []
        for line in out.decode(errors="replace").splitlines():
            e = self._parse_ls_line(line, loc, show_hidden)
            if e is not None:
                entries.append(e)
        return entries

    # -- file ops ----------------------------------------------------------

    def is_dir(self, loc: VfsPath) -> bool:
        last = loc.parts[-1] if loc.parts else ""
        # Image and network tokens are leaf entries, never navigable directories
        if last.startswith(_IMAGE) or last.startswith(_NETWORK):
            return False
        level = _level(loc)
        if level in ("top", "containers", "images", "networks", "volumes",
                     "compose", "service"):
            return True
        if level == "vfs":
            if _fs_path(loc, _VOLUME) == "/":
                return True
            try:
                self._run(["run", "--rm", "-v", self._volume_mount(loc), self._HELPER,
                           "test", "-d", "/mnt" + _fs_path(loc, _VOLUME)], endpoint=loc.root)
                return True
            except OSError:
                return False
        if level == "cfs":
            try:
                self._run(["exec", _container_name(loc), "test", "-d",
                           _fs_path(loc, _CONTAINER)], endpoint=loc.root)
                return True
            except OSError:
                return False
        return False

    def open_read(self, loc: VfsPath) -> BinaryIO:
        if _level(loc) == "vfs":
            data = self._run(["run", "--rm", "-v", self._volume_mount(loc), self._HELPER,
                              "cat", "--", "/mnt" + _fs_path(loc, _VOLUME)], endpoint=loc.root)
            return io.BytesIO(data)
        data = self._run(["exec", _container_name(loc), "cat", "--",
                          _fs_path(loc, _CONTAINER)], endpoint=loc.root)
        return io.BytesIO(data)

    def mkdir(self, parent: VfsPath, name: str) -> OpResult:
        remote = _fs_path(parent.child(name), _CONTAINER)
        try:
            self._run(["exec", _container_name(parent), "mkdir", "-p", remote],
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
                self._delete_target(t)
            except OSError as exc:
                result.errors.append(OpError(loc=t, reason=str(exc)))
            if on_progress is not None:
                on_progress(i, len(targets))
        return result

    def _delete_target(self, t: VfsPath) -> None:
        """Delete one target with the command that fits its kind. F8 routes here
        for every Docker entry, so it must dispatch — the old code blindly ran
        ``exec <container> rm`` and failed on volumes/images/networks (empty
        container name). Mirrors the per-entry Remove actions."""
        last = t.parts[-1] if t.parts else ""
        root = t.root
        # Section-level resources: image / network / volume by id/name.
        if last.startswith(_IMAGE):
            self._run(["rmi", _typed(last, _IMAGE)], endpoint=root)
            return
        if last.startswith(_NETWORK):
            self._run(["network", "rm", _typed(last, _NETWORK)], endpoint=root)
            return
        if last.startswith(_VOLUME):
            self._run(["volume", "rm", _typed(last, _VOLUME)], endpoint=root)
            return
        level = _level(t)
        if level == "vfs":  # a file/dir INSIDE a volume → helper container rm
            sub = _fs_path(t, _VOLUME)
            self._run(["run", "--rm", "-v", self._volume_mount(t), self._HELPER,
                       "rm", "-rf", "--", "/mnt" + ("" if sub == "/" else sub)],
                      endpoint=root)
            return
        if level == "cfs":
            container = _container_name(t)
            path = _fs_path(t, _CONTAINER)
            if path == "/":  # the container entry itself → remove the container
                self._run(["rm", "-f", container], endpoint=root)
            else:            # a file/dir inside the container
                self._run(["exec", container, "rm", "-rf", "--", path],
                          endpoint=root)
            return
        raise OSError(f"don't know how to delete {last or t.root!r}")

    def open_write(
        self, loc: VfsPath, *, size_hint: int | None = None, overwrite: bool = False
    ) -> BinaryIO:
        if not overwrite:
            try:
                self._run(["exec", _container_name(loc), "test", "-e",
                           _fs_path(loc, _CONTAINER)], endpoint=loc.root)
                raise FileExistsError(f"{_fs_path(loc, _CONTAINER)} already exists")
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
        if len(parts) == 1 and parts[0] in _SECTIONS:
            return VfsPath(scheme="docker", root=endpoint, parts=(parts[0],))
        name = parts[0]
        loc = self._locate_container(name, endpoint)
        if loc is None:
            raise OSError(f"container {name!r} not found (check the name)")
        if not self._is_running(name, endpoint):
            raise OSError(f"container {name!r} is not running — start it to browse")
        # append any trailing filesystem path segments from the spec
        for seg in parts[1:]:
            loc = loc.child(seg)
        return loc

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
    def _container(loc: VfsPath) -> str:  # kept name; now token-aware
        return _container_name(loc)

    def _locate_container(self, name: str, endpoint: str) -> VfsPath | None:
        """Full typed path for a container by name, or None if absent."""
        for r in self._ps_records(endpoint):
            rname = (r.get("Names") or r.get("ID") or "").split(",")[0]
            if rname != name:
                continue
            labels = self._labels_map(r)
            proj = labels.get("com.docker.compose.project")
            if proj:
                svc = labels.get("com.docker.compose.service", "")
                return VfsPath(scheme="docker", root=endpoint,
                               parts=(_COMPOSE + proj, _SERVICE + svc, _CONTAINER + name))
            return VfsPath(scheme="docker", root=endpoint,
                           parts=("containers", _CONTAINER + name))
        return None

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

    @staticmethod
    def _last_typed(loc, prefix):
        return _typed(loc.parts[-1], prefix) if loc.parts else None

    def _by_prefix_action(self, entries_prefix, argv):
        def run(locs):
            result = OpResult()
            for loc in locs:
                ident = self._last_typed(loc, entries_prefix)
                if ident is None:
                    continue
                try:
                    self._run(argv(ident), endpoint=loc.root)
                except OSError as exc:
                    result.errors.append(OpError(loc=loc, reason=str(exc)))
            return result
        return run

    def _compose_action(self, tail):
        def run(locs):
            result = OpResult()
            for loc in locs:
                proj = self._last_typed(loc, _COMPOSE)
                if proj is None:
                    continue
                try:
                    self._run(["compose", "-p", proj, *tail], endpoint=loc.root)
                except OSError as exc:
                    result.errors.append(OpError(loc=loc, reason=str(exc)))
            return result
        return run

    @staticmethod
    def _is_prefix(prefix):
        return lambda e: bool(_typed(e.loc.parts[-1], prefix)) if e.loc.parts else False

    def prune_argv(self, loc):
        level = _level(loc)
        _SCOPE = {"images": "image", "volumes": "volume", "networks": "network"}
        if level not in _SCOPE:
            # _level returns "top"/"vfs"/etc. for entries *inside* a section;
            # walk parts to find the enclosing section.
            for p in loc.parts:
                if p in _SECTIONS:
                    level = p
                    break
        scope = _SCOPE.get(level)
        return [scope, "prune", "-f"] if scope else ["system", "prune", "-f"]

    def _run_prune(self, locs):
        result = OpResult()
        for loc in locs:
            try:
                self._run(self.prune_argv(loc), endpoint=loc.root)
            except OSError as exc:
                result.errors.append(OpError(loc=loc, reason=str(exc)))
        return result

    def pull(self, ref: str, endpoint: str = "") -> "OpResult":
        """Pull a Docker image by ref (e.g. ``nginx:1.27``).

        Runs ``docker pull <ref>`` locally or on *endpoint* over SSH.
        Returns an :class:`OpResult`; any :class:`OSError` is collected as an
        :class:`OpError` rather than propagated so callers can report it via
        the normal notification path.
        """
        result = OpResult()
        try:
            self._run(["pull", ref], endpoint=endpoint)
        except OSError as exc:
            result.errors.append(OpError(
                loc=VfsPath(scheme="docker", root=endpoint, parts=("images",)),
                reason=str(exc)))
        return result

    @staticmethod
    def run_argv(spec: dict) -> list[str]:
        """Build the ``docker run`` argv from *spec*.

        Keys: ``image`` (required), ``name`` (str|""), ``ports`` (list[str]),
        ``volumes`` (list[str]), ``detach`` (bool, default True).
        """
        argv = ["run", "-d"] if spec.get("detach", True) else ["run"]
        if spec.get("name"):
            argv += ["--name", spec["name"]]
        for pmap in spec.get("ports") or []:
            argv += ["-p", pmap]
        for vmap in spec.get("volumes") or []:
            argv += ["-v", vmap]
        argv.append(spec["image"])
        return argv

    def run_image(self, spec: dict, endpoint: str = "") -> "OpResult":
        """Run a Docker image from *spec* locally or on *endpoint* over SSH.

        Returns an :class:`OpResult`; any :class:`OSError` is collected as an
        :class:`OpError` rather than propagated.
        """
        result = OpResult()
        try:
            self._run(self.run_argv(spec), endpoint=endpoint)
        except OSError as exc:
            result.errors.append(OpError(
                loc=VfsPath(scheme="docker", root=endpoint, parts=("containers",)),
                reason=str(exc)))
        return result

    def actions(self) -> list[ProviderAction]:
        stopped = {"exited", "created", "dead"}
        running = {"running", "paused", "restarting"}
        return [
            ProviderAction(
                id="docker.start", label="Start", icon="›", hotkey="f5",
                applies_to=lambda e: self._state(e) in stopped,
                run=lambda locs: self._simple_action("start", locs)),
            ProviderAction(
                id="docker.stop", label="Stop", icon="▪", hotkey="f6",
                applies_to=lambda e: self._state(e) in running,
                run=lambda locs: self._simple_action("stop", locs)),
            ProviderAction(
                id="docker.restart", label="Restart", icon="↺", hotkey="f7",
                applies_to=lambda e: self._state(e) in running,
                run=lambda locs: self._simple_action("restart", locs)),
            ProviderAction(
                id="docker.remove", label="Remove", icon="×", hotkey="f8",
                # any container (regardless of state) — but never a file inside one
                applies_to=lambda e: bool(self._state(e)), run=self._remove),
            ProviderAction(
                id="docker.rebuild", label="Rebuild (compose)", icon="✱",
                applies_to=self._has_compose, run=self._rebuild),
            ProviderAction(id="docker.image.remove", label="Remove image", icon="×",
                applies_to=self._is_prefix(_IMAGE),
                run=self._by_prefix_action(_IMAGE, lambda i: ["rmi", i])),
            ProviderAction(id="docker.network.remove", label="Remove network", icon="×",
                applies_to=self._is_prefix(_NETWORK),
                run=self._by_prefix_action(_NETWORK, lambda i: ["network", "rm", i])),
            ProviderAction(id="docker.volume.remove", label="Remove volume", icon="×",
                applies_to=self._is_prefix(_VOLUME),
                run=self._by_prefix_action(_VOLUME, lambda i: ["volume", "rm", i])),
            ProviderAction(id="docker.compose.up", label="Compose Up", icon="+",
                applies_to=self._is_prefix(_COMPOSE),
                run=self._compose_action(["up", "-d"])),
            ProviderAction(id="docker.compose.down", label="Compose Down", icon="−",
                applies_to=self._is_prefix(_COMPOSE),
                run=self._compose_action(["down"])),
            ProviderAction(id="docker.compose.restart", label="Compose Restart", icon="↺",
                applies_to=self._is_prefix(_COMPOSE),
                run=self._compose_action(["restart"])),
            ProviderAction(id="docker.compose.stop", label="Compose Stop", icon="▪",
                applies_to=self._is_prefix(_COMPOSE),
                run=self._compose_action(["stop"])),
            ProviderAction(id="docker.compose.build", label="Compose Build", icon="✱",
                applies_to=self._is_prefix(_COMPOSE),
                run=self._compose_action(["build"])),
            ProviderAction(id="docker.prune", label="Prune (cleanup)", icon="∷",
                applies_to=lambda e: _level(e.loc) in
                    ("top", "images", "networks", "volumes"),
                run=self._run_prune),
            # Sentinel: run is a no-op; the app intercepts by id to open a prompt
            # (mirrors how db.sql is intercepted in _run_provider_action).
            ProviderAction(id="docker.pull", label="Pull image…", icon="↓",
                applies_to=lambda e: _level(e.loc) in ("images", "top"),
                run=lambda locs: OpResult()),
            # Sentinel: app intercepts to open DockerRunDialog prefilled with
            # the cursor image's name.
            ProviderAction(id="docker.run", label="Run image…", icon="›",
                applies_to=self._is_prefix(_IMAGE),
                run=lambda locs: OpResult()),
        ]

    # Sort rank for the "S" column — running first, dead last.
    _STATE_RANK = {
        "running": 0, "restarting": 1, "paused": 2,
        "created": 3, "exited": 4, "dead": 5,
    }

    def _col(self, key, label, width, extra_key, *, sort_key=None, align="center"):
        return ProviderColumn(
            key=key, label=label, width=width,
            value=lambda e, k=extra_key: (e.extra.get(k) or ""),
            sort_key=sort_key or (lambda e, k=extra_key: (e.extra.get(k) or "")),
            align=align,
        )

    def _state_sort_key(self, e) -> int:
        return self._STATE_RANK.get(e.extra.get("docker.state", ""), 9)

    def default_sort(self, loc: VfsPath):
        """Preferred initial sort for a listing, or ``None``.

        Container/service listings sort *running-first* via ``_STATE_RANK``.
        Returns ``(sort_id, sort_key, descending)``. The state glyph lives in the
        Actions cluster (no clickable header), so this hook is the only way the
        listing acquires its running-first order.
        """
        if _level(loc) in ("containers", "service"):
            return ("docker.state", self._state_sort_key, False)
        return None

    def columns(self, loc: VfsPath) -> list[ProviderColumn]:
        level = _level(loc)
        if level in ("containers", "service"):
            # The "S" state glyph moved into the right-side Actions cluster
            # (passive leading element), so it is no longer a standalone column.
            return [self._col("docker.image", "Image", 20, "docker.image"),
                    self._col("docker.status", "Status", 18, "docker.status")]
        if level == "images":
            return [self._col("docker.size", "Size", 10, "docker.size"),
                    self._col("docker.created", "Created", 16, "docker.created")]
        if level == "networks":
            return [self._col("docker.driver", "Driver", 10, "docker.driver"),
                    self._col("docker.scope", "Scope", 8, "docker.scope")]
        if level == "volumes":
            # Mountpoint (an opaque /var/lib/docker/… path) is dropped from the
            # columns in favour of "Used by" — the owning container — which is
            # what makes an anonymous volume identifiable. Mountpoint stays in the
            # F3 `volume inspect` output.
            return [self._col("docker.size", "Size", 9, "docker.size",
                              sort_key=lambda e: self._parse_size(
                                  e.extra.get("docker.size", ""))),
                    self._col("docker.driver", "Driver", 8, "docker.driver"),
                    self._col("docker.usedby", "Used by", 26, "docker.usedby",
                              align="left")]
        return []

    def hide_default_columns(self, loc: VfsPath) -> bool:
        """Render Name-only at pure grouping levels (no meaningful Size/Date).

        The top index (compose projects + the Containers/Images/Networks/Volumes
        sections) and a compose project's service list carry ``mtime=0``, so the
        panel's default "Date" column would only ever show the epoch. Container
        and volume filesystems ("cfs"/"vfs") hold real files with real dates, so
        keep the default columns there; the section listings declare their own
        columns via :meth:`columns`, so this only governs the fallback path."""
        return _level(loc) in ("top", "compose")

    def copy_within(self, sources, dest, *, rename_to=None,
                    on_progress=None, on_status=None,
                    cancel_event=None, skip_existing=False) -> OpResult | None:
        return None

    def move_within(self, sources, dest, *, rename_to=None,
                    on_progress=None, cancel_event=None) -> OpResult | None:
        return None

    # -- preview -----------------------------------------------------------

    _LOG_TAIL = 2000

    def preview(self, loc, entry):
        level = _level(loc)
        last = loc.parts[-1] if loc.parts else ""
        if level == "cfs" and _fs_path(loc, _CONTAINER) == "/":
            name = _container_name(loc)
            out = self._run(["logs", "--tail", str(self._LOG_TAIL), name],
                            endpoint=loc.root)
            return PreviewResult(out.decode(errors="replace"), "log", f"logs: {name}")
        if level == "compose":
            proj = _typed(last, _COMPOSE) or ""
            out = self._run(["compose", "-p", proj, "logs", "--tail",
                             str(self._LOG_TAIL)], endpoint=loc.root)
            return PreviewResult(out.decode(errors="replace"), "log", f"compose logs: {proj}")
        for prefix, sub in ((_IMAGE, "image"), (_NETWORK, "network"), (_VOLUME, "volume")):
            ident = _typed(last, prefix)
            if ident is not None:
                out = self._run([sub, "inspect", ident], endpoint=loc.root)
                text = out.decode(errors="replace")
                if sub == "volume":
                    # Lead with the owning container(s) — the thing an opaque
                    # volume name hides — then the raw inspect JSON.
                    text = self._volume_usedby_header(ident, loc.root) + "\n" + text
                return PreviewResult(text, "json", f"{sub} inspect: {entry.name}")
        return None

    def _volume_usedby_header(self, ident: str, endpoint) -> str:
        """A human "Used by:" block naming the containers (and images) that mount
        volume ``ident``, for the top of its F3 preview."""
        users = self._volume_users(ident, endpoint)
        if not users:
            return "Used by:\n  (none — no container references this volume)\n"
        lines = "\n".join(f"  {n}  ({img})  [{st}]" for n, img, st in users)
        return f"Used by:\n{lines}\n"
