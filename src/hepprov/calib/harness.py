"""Fault-injection calibration of HEP-Prov's detectors.

For each defect class, three instances are injected into the reference
pipeline (different energies, particles or values). An instance counts as
**detected by a detector** if that detector raises a flag absent from the
clean control run with the same seed. The rule is fixed before running, so
scoring is differential, as in the GRACE audit's calibration.

False alarms: N clean runs with different seeds, every flag counted. A
detection profile without a false-alarm rate is half a measurement.

    python -m hepprov.calib.harness OUT_DIR [--clean-seeds 20]
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from hepprov.audit import audit_run
from hepprov.calib.pipeline import Defects, run_pipeline

# class -> (expected flag kind(s), three instances)
CLASSES: dict[str, tuple[set[str], list[Defects]]] = {
    "D1 stale input (leftover at expected path)": (
        {"leftover_from_before_run"},
        [Defects(leftover=("baseline", "electron", 0.005)), Defects(leftover=("optimized", "proton", 0.002)),
         Defects(leftover=("baseline", "proton", 0.001))]),
    "D2 transcribed constant": (
        {"transcribed_literal"},
        [Defects(transcribe_baseline=True, params={"gain": g}) for g in (1.286, 1.20, 1.35)]),
    "D3 placeholder in a mean": (
        {"placeholder_signature"},
        [Defects(placeholder_energy=e) for e in (0.005, 0.002, 0.001)]),
    "D4 existence-guarded silent skip": (
        {"silent_skip"},
        [Defects(silent_skip=s) for s in (("baseline", "proton", 0.002), ("optimized", "electron", 0.005),
                                         ("optimized", "proton", 0.001))]),
    "D5 truncated results file": (
        {"truncated_output"},
        [Defects(truncate=True) for _ in range(3)]),
    "D6 overwrite between steps": (
        {"overwrite"},
        [Defects(merged_overwrite=True) for _ in range(3)]),
    "D7 fabricated report number": (
        {"unexplained_claim"},
        [Defects(fabricate=v) for v in ("2.37", "141.6", "0.0834")]),
    "D8 batching suppression": (
        {"I3"},
        [Defects(batch=b, batch_factor=f) for b, f in ((("baseline", "electron", 0.005), 0.47),
                                                       (("optimized", "electron", 0.005), 0.55),
                                                       (("baseline", "proton", 0.005), 0.22))]),
    "D10 output of a superseded script version": (
        {"superseded_output"},
        [Defects(superseded=f) for f in (1.02, 0.97, 1.05)]),
    "D9 edit outside the recorder": (
        {"written_outside_recorder"},
        [Defects(hand_edit=k) for k in (("points", "baseline/electron/1MeV", "yield_per_mev"),
                                        ("points", "optimized/electron/1MeV", "yield_per_mev"),
                                        ("quenching_mean",))]),
}


def _sig(flag: dict) -> tuple:
    return (flag["detector"], flag["kind"], flag.get("path"), flag.get("step"), flag.get("detail", "")[:60])


def run(out: Path, clean_seeds: int = 20, seed: int = 1) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    control_dir = out / "control"
    shutil.rmtree(control_dir, ignore_errors=True)
    control = audit_run(control_dir, run_pipeline(control_dir, seed=seed))
    control_sigs = {_sig(f) for f in control["flags"]}

    profile = {}
    detectors = sorted({"attest", "outputs", "invariants", "verify", "static", "chain", "provenance"})
    for cname, (expected, instances) in CLASSES.items():
        rows = []
        for i, df in enumerate(instances):
            d = out / f"{cname.split()[0]}_{i}"
            shutil.rmtree(d, ignore_errors=True)
            res = audit_run(d, run_pipeline(d, df, seed=seed + i))  # instances differ by seed too
            new = [f for f in res["flags"] if _sig(f) not in control_sigs]
            by_det = defaultdict(list)
            for f in new:
                by_det[f["detector"]].append(f["kind"])
            rows.append({"instance": i, "new_flags": [(f["detector"], f["kind"]) for f in new],
                         "expected_hit": any(f["kind"] in expected for f in new),
                         "expected_defect": any(f["kind"] in expected and f.get("severity") == "defect"
                                                for f in new),
                         "detectors": {k: sorted(set(v)) for k, v in by_det.items()}})
        profile[cname] = {
            "expected": sorted(expected),
            "detected_expected": sum(r["expected_hit"] for r in rows),
            "detected_as_defect": sum(r["expected_defect"] for r in rows),
            "detected_any": sum(bool(r["new_flags"]) for r in rows),
            "by_detector": {det: sum(det in r["detectors"] for r in rows) for det in detectors},
            "instances": rows,
        }

    fa = Counter()
    fa_runs = 0
    fa_defect_runs = 0
    for s in range(clean_seeds):
        d = out / f"clean_{s:02d}"
        shutil.rmtree(d, ignore_errors=True)
        res = audit_run(d, run_pipeline(d, seed=1000 + s))
        fa_runs += bool(res["flags"])
        fa_defect_runs += any(f.get("severity") == "defect" for f in res["flags"])
        for f in res["flags"]:
            fa[f"{f['detector']}:{f['kind']}"] += 1
    result = {"control_flags": control["flags"], "profile": profile,
              "false_alarms": {"clean_runs": clean_seeds, "runs_with_any_flag": fa_runs, "runs_with_defect_flag": fa_defect_runs, "by_kind": dict(fa)}}
    (out / "calibration.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def table(result: dict) -> str:
    dets = ["attest", "outputs", "static", "verify", "invariants", "provenance"]
    lines = [f"{'defect class':44} {'expected':>8} {'defect':>8} " + " ".join(f"{d[:8]:>8}" for d in dets)]
    for c, p in result["profile"].items():
        lines.append(f"{c[:44]:44} {p['detected_expected']:>6}/3 {p.get('detected_as_defect', 0):>6}/3 "
                     + " ".join(f"{p['by_detector'].get(d, 0):>6}/3" for d in dets))
    fa = result["false_alarms"]
    lines.append(f"\nclean runs with any flag: {fa['runs_with_any_flag']}/{fa['clean_runs']}  {fa['by_kind'] or ''}")
    lines.append(f"control flags: {len(result['control_flags'])}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--clean-seeds", type=int, default=20)
    a = ap.parse_args()
    print(table(run(Path(a.out), a.clean_seeds)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
