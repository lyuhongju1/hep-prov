"""Cooperative mode: a script declares the numbers it computed.

    import hepprov
    hepprov.emit("light_yield_1MeV", ly, unit="PE/MeV")

Under ``hepprov exec`` the value goes into the run's manifest (``emit`` tier).
The verifier then treats a report number equal to it, at the printed
precision, as verified. Outside a recorded run, ``emit`` is a no-op that
returns the value, so instrumented code still runs standalone.
"""

from __future__ import annotations

import json
import math
import os
import uuid

_LOG = None


def emit(name: str, value: float, unit: str | None = None) -> float:
    global _LOG
    log_dir = os.environ.get("HEPPROV_LOG_DIR")
    if not log_dir:
        return value
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"hepprov.emit({name!r}): value {value!r} is not finite")
    if _LOG is None:
        _LOG = os.path.join(log_dir, f"reads.{os.getpid()}.emit{uuid.uuid4().hex[:6]}.jsonl")
    with open(_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "emit", "name": str(name), "value": v, "repr": repr(v), "unit": unit}) + "\n")
    return value
