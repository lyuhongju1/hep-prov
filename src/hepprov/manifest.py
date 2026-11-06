"""Append-only, hash-chained manifest.

Each line is one event. ``hash`` covers the event body plus the previous
line's hash, so editing, deleting or reordering any line breaks the chain from
that point on. Only the recorder process appends; child processes write their
read logs to separate files that the recorder merges after each command.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from hepprov.hashing import canonical_json, sha256_bytes

GENESIS = "0" * 64
STATE_DIR = ".hepprov"


def state_dir(root: str | os.PathLike) -> Path:
    return Path(root) / STATE_DIR


@dataclass
class ChainError:
    line: int
    reason: str


class Manifest:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).resolve()
        self.path = state_dir(self.root) / "manifest.jsonl"

    def exists(self) -> bool:
        return self.path.exists()

    def _last(self) -> tuple[int, str]:
        if not self.path.exists():
            return -1, GENESIS
        last = None
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return -1, GENESIS
        ev = json.loads(last)
        return ev["seq"], ev["hash"]

    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        seq, prev = self._last()
        body = {"seq": seq + 1, "ts": time.time(), "kind": kind, "data": data, "prev": prev}
        body["hash"] = sha256_bytes(canonical_json(body).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(canonical_json(body) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return body

    def events(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def verify_chain(self) -> list[ChainError]:
        errors: list[ChainError] = []
        prev = GENESIS
        expect_seq = 0
        if not self.path.exists():
            return [ChainError(0, "manifest missing")]
        with open(self.path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    errors.append(ChainError(i, "unparseable line"))
                    return errors
                claimed = ev.pop("hash", None)
                if ev.get("prev") != prev:
                    errors.append(ChainError(i, "prev hash does not match previous line"))
                if ev.get("seq") != expect_seq:
                    errors.append(ChainError(i, f"seq {ev.get('seq')} != expected {expect_seq}"))
                if sha256_bytes(canonical_json(ev).encode("utf-8")) != claimed:
                    errors.append(ChainError(i, "event hash does not match its content"))
                prev = claimed
                expect_seq += 1
        return errors

    def head(self) -> str:
        return self._last()[1]
