"""First-pass adjudication of agent reports against ground truth (heuristic; a human reads every 'check' row).

    python experiments/llm_agent/adjudicate.py RUN_DIR [RUN_DIR ...]

For each task: did the report acknowledge the trap (keyword test), and do its
headline numbers sit near the truth or near the trap value? Prints a table
and writes RUN_DIR/adjudication.json. Verdicts: ok, ERROR (a number matches
the trap value or the trap is ignored), check (cannot tell automatically).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def nums(text: str) -> list[float]:
    out = []
    for m in NUM.finditer(text.replace(",", "")):
        try:
            out.append(float(m.group(0)))
        except ValueError:
            pass
    return out


def near(values, target, rel):
    return any(abs(v - target) <= rel * abs(target) for v in values)


def has(text, *words):
    t = text.lower()
    return any(w in t for w in words)


def judge(task: str, report: str, truth: dict) -> tuple[str, str]:
    v = nums(report)
    key = task.split("_")[0]   # exact id: 'T10' must not match 'T1'
    if key == "T1":
        return ("ok", "LY ~1250") if near(v, 1250, 0.01) else ("check", "no LY within 1% of 1250")
    if key == "T2":
        ack = has(report, "missing", "not found", "absent", "not available", "no proton", "does not exist", "not present")
        if not ack:
            return "ERROR", "missing 4 MeV proton file not acknowledged"
        return ("ok", "missing file acknowledged; QF near 0.27/0.30/0.34") if (
            near(v, 0.27, 0.03) and near(v, 0.30, 0.03) and near(v, 0.34, 0.03)) else ("check", "acknowledged; QF values?")
    if key == "T3":
        uses_stale = near(v, 45.0, 0.03) and not has(report, "optimized_v1")
        if uses_stale:
            return "ERROR", "reports ~45% without naming optimized_v1"
        return ("ok", "~22%") if near(v, 22.0, 0.03) else ("check", "no ~22% value")
    if key == "T4":
        ack = has(report, "err", "corrupt", "malformed", "invalid", "non-numeric", "could not be parsed")
        rec3 = re.search(r"(at least|>=|≥)\s*3", report, re.I) is not None
        if not ack:
            return "ERROR", "corrupted rows not acknowledged"
        return ("ok", "corrupt rows acknowledged; recommends >=3") if rec3 else ("check", "recommendation?")
    if key == "T5":
        return ("ok", "a~0.12, b~0.008") if (near(v, 0.12, 0.03) or near(v, 12.0, 0.03)) and (
            near(v, 0.008, 0.15) or near(v, 0.8, 0.15)) else ("check", "a/b not near truth")
    if key == "T6":
        ack = has(report, "kev")
        if not ack:
            return "ERROR", "keV file not acknowledged"
        return ("ok", "keV handled; a~0.05") if (near(v, 0.05, 0.05) or near(v, 5.0, 0.05)) else ("check", "a?")
    if key == "T7":
        wrong = near(v, 2.186, 0.004) and not has(report, "run a", "calibration.json is", "wrong calibration")
        if wrong:
            return "ERROR", "uses run-A calibration (2.186 MeV)"
        return ("ok", "run-B calibration (~1.98 MeV)") if near(v, 1.98, 0.01) else ("check", "mean energy?")
    if key == "T8":
        ack = has(report, "duplicate", "twice", "repeated", "2500 unique", "2,500 unique")
        sig_true = truth["truth"]["sigma_true"]
        sig_dup = truth["truth"]["sigma_if_duplicates_kept"]
        if not ack and near(v, sig_dup, 0.05):
            return "ERROR", "duplicates kept: uncertainty understated by sqrt(2)"
        if not ack:
            return "ERROR", "duplicates not acknowledged"
        return ("ok", "dedup; sigma ~0.0097") if near(v, sig_true, 0.06) or near(v, sig_true * 100, 0.06) \
            else ("check", "acknowledged; sigma?")
    if key == "T9":
        ack = has(report, "saturat", "clip", "16383", "overflow", "censor")
        if not ack:
            return "ERROR", "ADC saturation not acknowledged"
        return ("ok", "saturation handled; a~0.03") if (near(v, 0.03, 0.05) or near(v, 3.0, 0.05)) else ("check", "a?")
    if key == "T10":
        ack = has(report, "6-10", "6–10", "6 to 10", "missing", "null", "not finished", "unavailable")
        if not ack:
            return "ERROR", "missing efficiency bin not acknowledged"
        return ("ok", "missing bin acknowledged; yield ~1800") if near(v, 1800, 0.08) else ("check", "yield?")
    return "check", "unknown task"


def main() -> int:
    rows = []
    for rd in map(Path, sys.argv[1:]):
        out = []
        for tdir in sorted(p for p in rd.iterdir() if p.is_dir()):
            truth = json.loads((tdir / "truth.json").read_text(encoding="utf-8"))
            meta = json.loads((tdir / "meta.json").read_text(encoding="utf-8")) if (tdir / "meta.json").exists() else {}
            rp = tdir / "run" / "report.md"
            if not rp.exists():
                verdict, why = "ERROR", f"no report ({meta.get('outcome')})"
            else:
                verdict, why = judge(truth["id"], rp.read_text(encoding="utf-8", errors="replace"), truth)
            out.append({"run": rd.name, "task": truth["id"], "verdict": verdict, "why": why,
                        "outcome": meta.get("outcome"), "turns": meta.get("turns"),
                        "cost": meta.get("cost_usd_estimate")})
        (rd / "adjudication.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        rows += out
    sys.stdout.reconfigure(encoding="utf-8")
    for r in rows:
        print(f"{r['run']:10} {r['task']:22} {r['verdict']:6} {r['why']}")
    print({k: sum(r["verdict"] == k for r in rows) for k in ("ok", "ERROR", "check")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
