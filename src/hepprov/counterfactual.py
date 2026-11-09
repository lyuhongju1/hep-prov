"""Counterfactual re-execution: which printed numbers actually depend on a flagged input?

Step-level taint marks everything a step printed once the step read a flagged
file. That over-flags careful agents: an agent that opens a stale file to
inspect and reject it taints its whole analysis. Observed in the first live
agent run, where 20 of 20 taint flags were false alarms.

For a step that read flagged inputs, the step's script is re-run three
times in a sandbox copy of the run directory:

  A  unperturbed           -> numbers that differ from the recorded stdout
                              are nondeterministic (masked as inconclusive)
  B  flagged files perturbed (every numeric cell scaled by 1 + EPS)
                           -> numbers that differ from A depend on the
                              flagged input

Only the step's own stdout numbers are compared, position by position. If
the number of printed values changes, the step is marked inconclusive and
keeps its taint. Steps that are tainted only through other tainted files
(chained taint) are not re-run here; they keep their conservative taint.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

EPS = 0.0137
_NUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


@dataclass
class StepCounterfactual:
    label: str
    status: str                     # "ok" | "inconclusive" | "not_rerunnable"
    reason: str = ""
    dependent: set[int] = field(default_factory=set)
    nondeterministic: set[int] = field(default_factory=set)
    n_values: int = 0


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text):
        try:
            out.append(float(m.group(0)))
        except ValueError:
            pass
    return out


def perturb_file(path: Path, eps: float = EPS) -> bool:
    """Scale every numeric cell of a CSV / whitespace table by (1 + eps). False if the format is unsupported."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if path.suffix.lower() not in (".csv", ".tsv", ".txt", ".dat"):
        return False
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delim, lineterminator="\n")
    changed = False
    header = [h.strip().lower() for h in rows[0]] if rows else []
    id_cols = {j for j, h in enumerate(header) if h in ("event", "eventid", "event_id", "id", "index", "entry")}
    for i, row in enumerate(rows):
        new = []
        for j, cell in enumerate(row):
            try:
                v = float(cell)
            except ValueError:
                new.append(cell)
                continue
            if i == 0 or j in id_cols:
                new.append(cell)   # header row and identifier columns stay as they are
                continue
            if re.fullmatch(r"\s*[-+]?\d+\s*", cell):
                nv = int(round(v * (1 + eps)))
                if nv == int(v):
                    nv += 1
                new.append(str(nv))
            else:
                new.append(repr(v * (1 + eps)))
            changed = True
        w.writerow(new)
    path.write_text(buf.getvalue(), encoding="utf-8")
    return changed


def _run(script_rel: str, cwd: Path, timeout: int) -> tuple[int, str, str]:
    # Re-create the recorded conditions: the same L2 capture module is loaded,
    # logging to a throwaway directory. Running without it is a different
    # environment: the capture module imports stdlib modules (e.g. inspect)
    # early, which masks agent scripts that shadow them (an agent named its
    # script inspect.py; without the module, `import pandas` then fails).
    from hepprov.recorder import SITE_DIR
    env = {k: v for k, v in os.environ.items() if not k.startswith("HEPPROV_")}
    logs = cwd.parent / f"_logs_{cwd.name}"
    logs.mkdir(exist_ok=True)
    env["PYTHONPATH"] = SITE_DIR
    env["HEPPROV_ROOT"] = str(cwd)
    env["HEPPROV_LOG_DIR"] = str(logs)
    for attempt in range(3):
        p = subprocess.run([sys.executable, script_rel], cwd=cwd, capture_output=True, timeout=timeout, env=env)
        # 0xC0000142 (STATUS_DLL_INIT_FAILED): the OS could not start the process, typically under
        # memory pressure. That is the machine, not the script: wait and retry.
        if p.returncode not in (3221225794, -1073741502):
            break
        time.sleep(15 * (attempt + 1))
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def rerun_step(root: Path, script_rel: str, recorded_stdout: str, flagged: list[str],
               label: str, timeout: int = 120) -> StepCounterfactual:
    rec = _numbers(recorded_stdout)
    with tempfile.TemporaryDirectory(prefix="hepprov_cf_") as td:
        base = Path(td) / "A"
        pert = Path(td) / "B"
        ignore = shutil.ignore_patterns(".hepprov", "__pycache__")
        shutil.copytree(root, base, ignore=ignore)
        shutil.copytree(root, pert, ignore=ignore)
        if not all(perturb_file(pert / f) for f in flagged):
            return StepCounterfactual(label, "not_rerunnable", "a flagged input has an unsupported format")
        try:
            ca, out_a, err_a = _run(script_rel, base, timeout)
            cb, out_b, err_b = _run(script_rel, pert, timeout)
        except subprocess.TimeoutExpired:
            return StepCounterfactual(label, "inconclusive", "re-execution timed out")
    a, b = _numbers(out_a), _numbers(out_b)
    if ca != 0 or cb != 0:
        tail = (err_a or err_b).strip().splitlines()[-1:] or [""]
        return StepCounterfactual(label, "inconclusive", f"re-execution exit codes {ca}/{cb}: {tail[0][:200]}")
    if not (len(rec) == len(a) == len(b)):
        return StepCounterfactual(label, "inconclusive",
                                  f"printed value counts differ (recorded {len(rec)}, rerun {len(a)}, perturbed {len(b)})")
    nondet = {i for i, (x, y) in enumerate(zip(rec, a)) if x != y}
    dep = {i for i, (x, y) in enumerate(zip(a, b)) if x != y} - nondet
    return StepCounterfactual(label, "ok", dependent=dep, nondeterministic=nondet, n_values=len(rec))


def regenerate_step(root: Path, script_rel: str, recorded_stdout: str, label: str,
                    timeout: int = 120) -> StepCounterfactual:
    """Re-run a superseded step on the run's CURRENT files (twice) and compare its printed numbers.

    `dependent` holds positions whose value changes under regeneration (the
    recorded number is genuinely out of date); `nondeterministic` holds
    positions where the two regenerations disagree with each other.
    """
    rec = _numbers(recorded_stdout)
    with tempfile.TemporaryDirectory(prefix="hepprov_rg_") as td:
        ignore = shutil.ignore_patterns(".hepprov", "__pycache__")
        outs = []
        for name in ("A", "B"):
            d = Path(td) / name
            shutil.copytree(root, d, ignore=ignore)
            try:
                code, out, err = _run(script_rel, d, timeout)
            except subprocess.TimeoutExpired:
                return StepCounterfactual(label, "inconclusive", "regeneration timed out")
            if code != 0:
                tail = err.strip().splitlines()[-1:] or [""]
                return StepCounterfactual(label, "inconclusive", f"regeneration exit code {code}: {tail[0][:200]}")
            outs.append(_numbers(out))
    a, b = outs
    if not (len(rec) == len(a) == len(b)):
        return StepCounterfactual(label, "inconclusive",
                                  f"printed value counts differ (recorded {len(rec)}, regenerated {len(a)}/{len(b)})")
    nondet = {i for i, (x, y) in enumerate(zip(a, b)) if x != y}
    changed = {i for i, (x, y) in enumerate(zip(rec, a)) if x != y} - nondet
    return StepCounterfactual(label, "ok", dependent=changed, nondeterministic=nondet, n_values=len(rec))
