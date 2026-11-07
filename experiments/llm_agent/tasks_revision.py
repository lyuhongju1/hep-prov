"""Revision tasks (fourth suite): the analysis is finished, then the requester changes something.

Real analyses accumulate mistakes through revisions: a cut changes, a run is
dropped, a data file is regenerated, and the analyst re-runs part of the
pipeline and updates part of the report. Each task here has a follow-up
that materially changes the answer. Truth is given before and after the
revision, so numbers left over from the first version can be identified.

A follow-up is (message, action). The action, if any, runs before the
message is sent, e.g. a legitimate regeneration of a data file. It is
recorded through the recorder as a declared update, so it is not a fault.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tasks import Task, _write_events
from tasks_long import l1_calo_design, l2_runs_combination


def r1_cuts_change(root: Path, rng) -> Task:
    base = l2_runs_combination(root, rng)
    # recompute truth under the revised selection from the files just written
    import csv
    eff_after = {}
    for r in range(1, 7):
        rows = list(csv.DictReader(open(root / "data" / f"run{r:03d}.csv", encoding="utf-8")))
        sig = [x for x in rows if x["mc_signal"] == "1"]
        sel = [x for x in sig if float(x["quality"]) > 0.8 and abs(float(x["time_ns"])) < 4.0
               and 44 < float(x["energy_gev"]) < 56]
        eff_after[r] = len(sel) / len(sig)
    base.id = "R1_cuts_change"
    base.truth = {"before": base.truth["efficiency"], "after": eff_after}
    base.followups = [(
        "Update from the detector group: the quality cut must be 0.8 (not 0.6) and the energy window "
        "44-56 GeV (not 40-60). Redo the analysis with the new selection and update report.md so that every "
        "number reflects the new selection.", None)]
    return base


def r2_budget_change(root: Path, rng) -> Task:
    base = l1_calo_design(root, rng)
    t_opts = [2, 4, 6, 8]
    a = {t: 0.045 * np.sqrt(t) for t in t_opts}
    b_new = 0.012
    res50 = {t: float(np.hypot(a[t] / np.sqrt(50), b_new)) for t in t_opts}
    allowed = [t for t in t_opts if 160.0 / t <= 30]
    best = min(allowed, key=lambda t: res50[t])
    base.id = "R2_budget_change"
    base.truth = {"before": {"recommend_t_mm": 4, "b": 0.007},
                  "after": {"recommend_t_mm": best, "b": b_new, "res50": {str(t): res50[t] for t in t_opts}}}
    base.followups = [(
        "Two changes: the layer budget is now at most 30 layers, and a calibration study puts the constant term "
        "at 0.012 instead of 0.007 (update SPEC.md accordingly). Redo the study and update report.md so that "
        "every number and the recommendation reflect the new inputs.", None)]
    return base


def r3_data_regenerated(root: Path, rng) -> Task:
    from tasks import t3_geometry_stale
    base = t3_geometry_stale(root, rng)
    n, ly_new = 2000, 1250.0 * 1.26
    seed = int(rng.integers(1 << 30))

    def regenerate(rec):
        r2 = np.random.default_rng(seed)
        vals = r2.poisson(ly_new * 5, n)
        rec.write_file("optimized/electron_5MeV.csv",
                       "event,npe\n" + "".join(f"{i},{int(v)}\n" for i, v in enumerate(vals)),
                       label="data update: optimized 5 MeV regenerated (final geometry)")

    base.id = "R3_data_regenerated"
    base.truth = {"before": {"improvement_5MeV_pct": 22.0}, "after": {"improvement_5MeV_pct": 26.0,
                                                                       "improvement_1MeV_pct": 22.0}}
    base.followups = [(
        "optimized/electron_5MeV.csv has just been regenerated with the final optical geometry (the file on disk "
        "is now the final one). Redo the comparison and update report.md so that every number reflects the "
        "current data.", regenerate)]
    return base


TASKS_REVISION = [r1_cuts_change, r2_budget_change, r3_data_regenerated]
