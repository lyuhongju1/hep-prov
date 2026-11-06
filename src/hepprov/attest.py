"""Input attestation: is every read a file this run produced (or declared), at its latest version?

The manifest is replayed in order. A read is classified as one of:

  ok_declared              matches a declared input
  ok_latest                matches the latest version written by this run
                           (by an earlier command, or by the same command)
  stale_version            matches an older version this run wrote
  leftover_from_before_run file was in the root at init, undeclared, never rewritten
  written_outside_recorder content came from an untracked change between commands
  not_written_by_run       content matches nothing this run wrote or declared
  unhashable               the reader couldn't hash it at read time

Everything except the two ``ok`` kinds is a violation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from hepprov.manifest import Manifest

OK = {"ok_declared", "ok_latest"}


@dataclass
class ReadVerdict:
    exec_seq: int
    label: str
    path: str
    sha256: str | None
    kind: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind in OK


def attest(manifest: Manifest) -> dict[str, Any]:
    declared: dict[str, set[str]] = {}
    leftovers: dict[str, str] = {}
    versions: dict[str, list[tuple[str, str]]] = {}   # rel -> [(sha, origin)]
    verdicts: list[ReadVerdict] = []
    overwrites: dict[str, list[str]] = {}

    for ev in manifest.events():
        kind, d, seq = ev["kind"], ev["data"], ev["seq"]
        if kind == "init":
            leftovers = {k: v for k, v in d.get("preexisting", {}).items() if v}
        elif kind == "declare":
            declared.setdefault(d["path"], set()).add(d["sha256"])
        elif kind == "untracked_change":
            for rel, sha in d.get("changed", {}).items():
                if sha:
                    versions.setdefault(rel, []).append((sha, "untracked"))
        elif kind == "exec":
            label = d.get("label") or f"exec#{seq}"
            own = {rel: sha for rel, sha in d.get("writes", {}).items() if sha}
            for r in d.get("reads", []):
                rel, sha = r["path"], r.get("sha256")
                hist = versions.get(rel, [])
                if sha is None:
                    v = ReadVerdict(seq, label, rel, None, "unhashable", r.get("error", ""))
                elif sha in declared.get(rel, set()):
                    v = ReadVerdict(seq, label, rel, sha, "ok_declared")
                elif own.get(rel) == sha:
                    v = ReadVerdict(seq, label, rel, sha, "ok_latest", "written by this command")
                elif hist and hist[-1][0] == sha:
                    if hist[-1][1] == "untracked":
                        v = ReadVerdict(seq, label, rel, sha, "written_outside_recorder",
                                        "this version appeared between commands, outside `hepprov exec`")
                    else:
                        v = ReadVerdict(seq, label, rel, sha, "ok_latest", f"written by {hist[-1][1]}")
                elif any(h[0] == sha for h in hist):
                    v = ReadVerdict(seq, label, rel, sha, "stale_version",
                                    f"an older version; latest was written by {hist[-1][1]}")
                elif not hist and leftovers.get(rel) == sha:
                    v = ReadVerdict(seq, label, rel, sha, "leftover_from_before_run",
                                    "present when the run started, never declared or rewritten "
                                    "(likely output of an earlier run)")
                else:
                    v = ReadVerdict(seq, label, rel, sha, "not_written_by_run",
                                    "content matches no version this run wrote or declared")
                verdicts.append(v)
            for rel, sha in own.items():
                hist = versions.setdefault(rel, [])
                if hist and hist[-1][0] != sha and hist[-1][1] != label:
                    overwrites.setdefault(rel, [hist[-1][1]]).append(label)
                hist.append((sha, label))

    violations = [v for v in verdicts if not v.ok]
    return {
        "reads": len(verdicts),
        "violations": [asdict(v) for v in violations],
        "overwrites": overwrites,
        "ok": not violations,
    }
