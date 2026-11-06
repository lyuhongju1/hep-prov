"""Recorder: ``init``, ``declare`` and ``exec``.

Capture layers:
  L1  filesystem diff of the run root before/after every command. Any
      language (Geant4, Opticks, C++, shell). Also catches changes made
      *between* commands, outside the recorder ("untracked_change").
  L2  Python read capture via the auto-loaded sitecustomize (path + sha256
      at read time), for every Python process the command starts.
  L3  syscall tracing for non-Python reads: not implemented yet (Linux only).

Each command becomes one ``exec`` event in the hash-chained manifest. The
event records the command, its exit code, sha256 of its stdout and stderr,
the files it wrote, the files it read, and which processes were instrumented.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from hepprov.hashing import atomic_write_text, sha256_bytes, sha256_file
from hepprov.manifest import STATE_DIR, Manifest, state_dir

SITE_DIR = str(Path(__file__).resolve().parent / "_site")
_SKIP_DIRS = {STATE_DIR, "__pycache__", ".git"}


class NotInitialized(RuntimeError):
    pass


def _rel(root: Path, p: Path) -> str:
    return Path(os.path.relpath(p, root)).as_posix()


def scan(root: Path) -> dict[str, tuple[int, int]]:
    """relpath -> (size, mtime_ns) for every file under root (state dir excluded)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            out[_rel(root, p)] = (st.st_size, st.st_mtime_ns)
    return out


def _hash_rel(root: Path, rels: Iterable[str]) -> dict[str, str | None]:
    out = {}
    for r in rels:
        try:
            out[r] = sha256_file(root / r)
        except OSError:
            out[r] = None
    return out


class Recorder:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).resolve()
        self.manifest = Manifest(self.root)
        self.state_path = state_dir(self.root) / "scan.json"

    # ---------------------------------------------------------------- state
    def _load_scan(self) -> dict[str, tuple[int, int]]:
        if not self.state_path.exists():
            raise NotInitialized(f"{self.root} is not a hepprov run (run `hepprov init` first)")
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        return {k: tuple(v) for k, v in raw.items()}

    def _save_scan(self, sc: dict[str, tuple[int, int]]) -> None:
        atomic_write_text(self.state_path, json.dumps(sc))

    @staticmethod
    def _diff(before: dict, after: dict) -> tuple[list[str], list[str]]:
        changed = sorted(k for k, v in after.items() if before.get(k) != v)
        deleted = sorted(k for k in before if k not in after)
        return changed, deleted

    # ---------------------------------------------------------------- public
    def init(self, declare: Iterable[str | os.PathLike] = ()) -> dict[str, Any]:
        if self.manifest.exists():
            raise RuntimeError(f"{self.root} already has a manifest")
        sc = scan(self.root)
        leftovers = _hash_rel(self.root, sc)
        ev = self.manifest.append("init", {
            "root": str(self.root), "python": sys.version.split()[0], "platform": sys.platform,
            "preexisting": leftovers,
        })
        self._save_scan(sc)
        for d in declare:
            self.declare(d)
        return ev

    def declare(self, path: str | os.PathLike, note: str = "") -> dict[str, Any]:
        """Declare a file as a legitimate input (provided GDML, paper, dataset)."""
        p = Path(path)
        if not p.is_absolute():
            p = (self.root / p)
        p = p.resolve()
        inside = str(p).startswith(str(self.root))
        return self.manifest.append("declare", {
            "path": _rel(self.root, p) if inside else p.as_posix(),
            "external": not inside, "sha256": sha256_file(p), "note": note,
        })

    def exec(self, cmd: list[str], label: str = "", cwd: str | os.PathLike | None = None,
             env_extra: dict[str, str] | None = None, echo: bool = True) -> dict[str, Any]:
        before_recorded = self._load_scan()
        now = scan(self.root)
        changed, deleted = self._diff(before_recorded, now)
        if changed or deleted:
            self.manifest.append("untracked_change", {
                "changed": _hash_rel(self.root, changed), "deleted": deleted,
                "note": "files changed outside `hepprov exec` since the last recorded command",
            })
        seq = self.manifest._last()[0] + 1
        log_dir = state_dir(self.root) / "logs" / f"{seq:06d}"
        log_dir.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env["PYTHONPATH"] = SITE_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["HEPPROV_ROOT"] = str(self.root)
        env["HEPPROV_LOG_DIR"] = str(log_dir)
        env.update(env_extra or {})

        t0 = time.time()
        proc = subprocess.run(cmd, cwd=str(cwd or self.root), env=env, capture_output=True)
        dt = time.time() - t0
        (log_dir / "stdout.bin").write_bytes(proc.stdout)
        (log_dir / "stderr.bin").write_bytes(proc.stderr)
        if echo:
            sys.stdout.write(proc.stdout.decode("utf-8", "replace"))
            sys.stderr.write(proc.stderr.decode("utf-8", "replace"))

        after = scan(self.root)
        w_changed, w_deleted = self._diff(now, after)
        reads, procs, emits = [], [], []
        for lf in sorted(log_dir.glob("reads.*.jsonl")):
            for line in lf.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") == "read":
                    ap = Path(rec["path"])
                    rec["path"] = _rel(self.root, ap)
                    reads.append({k: rec.get(k) for k in ("path", "sha256", "size", "via", "error") if k in rec})
                elif rec.get("type") == "process":
                    procs.append({k: rec.get(k) for k in ("pid", "argv", "executable", "version")})
                elif rec.get("type") == "emit":
                    emits.append({k: rec.get(k) for k in ("name", "value", "repr", "unit")})
        ev = self.manifest.append("exec", {
            "label": label,
            "cmd": list(cmd),
            "cwd": str(cwd or self.root),
            "exit_code": proc.returncode,
            "duration_s": round(dt, 3),
            "stdout_sha256": sha256_bytes(proc.stdout),
            "stderr_sha256": sha256_bytes(proc.stderr),
            "writes": _hash_rel(self.root, w_changed),
            "deletes": w_deleted,
            "reads": reads,
            "python_processes": procs,
            "emits": emits,
            "capture": {"L1": True, "L2": bool(procs), "L3": False},
        })
        self._save_scan(after)
        return ev
