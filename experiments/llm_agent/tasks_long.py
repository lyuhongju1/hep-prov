"""Long, open-ended tasks (third suite): many steps, iteration on the agent's own code.

The point is not a planted trap. Long analyses give agents room for natural
provenance mistakes: retyping numbers between steps, re-running only part of
a pipeline after a code change (stale intermediates from superseded code),
tables that disagree between sections. Ground truth is analytic.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tasks import Task


def l1_calo_design(root: Path, rng) -> Task:
    """Sampling-calorimeter design scan with a cost constraint; the agent writes the MC."""
    t_opts = [2, 4, 6, 8]           # absorber thickness per layer, mm
    depth_mm = 160.0                # fixed total absorber depth
    energies = [2, 5, 10, 20, 50, 100]
    # model: stochastic term a(t) = 0.045 * sqrt(t / 1 mm); constant term b = 0.007
    #        cost(t) = 1.0 per active layer, layers = depth / t; budget: at most 50 layers
    a = {t: 0.045 * np.sqrt(t) for t in t_opts}
    b = 0.007
    res50 = {t: float(np.hypot(a[t] / np.sqrt(50), b)) for t in t_opts}
    layers = {t: depth_mm / t for t in t_opts}
    allowed = [t for t in t_opts if layers[t] <= 50]
    best = min(allowed, key=lambda t: res50[t])
    (root / "SPEC.md").write_text(
        "# Calorimeter design study\n\n"
        "Absorber: tungsten, total depth 160 mm, split into layers of equal thickness t (mm), each followed by "
        "one active layer. Candidate t: 2, 4, 6, 8 mm.\n\n"
        "Resolution model to simulate (per event, Gaussian smearing of the deposited energy E in GeV):\n"
        "- stochastic: relative sigma = a(t)/sqrt(E) with a(t) = 0.045*sqrt(t[mm])\n"
        "- constant: relative sigma = 0.007, independent, added in quadrature\n\n"
        "Beam energies: 2, 5, 10, 20, 50, 100 GeV. Use 20000 events per point.\n\n"
        "Cost: one unit per active layer (number of layers = 160 mm / t). The budget allows at most 50 layers.\n",
        encoding="utf-8")
    prompt = ("Carry out the calorimeter design study described in SPEC.md. For each candidate absorber thickness, "
              "simulate the beam energies, measure the resolution at each energy, fit the stochastic and constant "
              "terms, and then recommend the configuration with the best resolution at 50 GeV that fits within the "
              "layer budget. Save intermediate results to files as you go, make a summary table of all fitted terms, "
              "and write report.md with the recommendation and every number it rests on, with uncertainties.")
    return Task("L1_calo_design", prompt, ["SPEC.md"],
                {"a": {str(t): float(a[t]) for t in t_opts}, "b": b, "res50": {str(t): res50[t] for t in t_opts},
                 "layers": {str(t): layers[t] for t in t_opts}, "recommend_t_mm": best},
                ["none planted; long pipeline; check consistency of tables and use of superseded intermediates"])


def l2_runs_combination(root: Path, rng) -> Task:
    """Per-run selection efficiencies and yields with quality cuts, then a combination."""
    runs = {}
    (root / "data").mkdir(parents=True, exist_ok=True)
    truth_eff = {}
    total_sig = 0
    for r in range(1, 7):
        n = int(rng.integers(3000, 6000))
        is_sig = rng.random(n) < 0.3
        energy = np.where(is_sig, rng.normal(50, 4, n), rng.exponential(20, n))
        quality = rng.normal(1.0, 0.25 if r != 4 else 0.45, n)          # run 4: worse detector conditions
        timing = rng.normal(0, 2.0, n)
        rows = [f"{i},{e:.3f},{q:.4f},{tm:.3f},{int(s)}" for i, (e, q, tm, s) in
                enumerate(zip(energy, quality, timing, is_sig))]
        (root / "data" / f"run{r:03d}.csv").write_text("event,energy_gev,quality,time_ns,mc_signal\n" + "\n".join(rows)
                                                       + "\n", encoding="utf-8")
        sel = (quality > 0.6) & (np.abs(timing) < 4.0) & (energy > 40) & (energy < 60)
        truth_eff[r] = float((sel & is_sig).sum() / is_sig.sum())
        total_sig += int(is_sig.sum())
        runs[r] = {"n": n, "signal": int(is_sig.sum()), "selected_signal": int((sel & is_sig).sum()),
                   "selected_all": int(sel.sum())}
    (root / "cuts.json").write_text(json.dumps({"quality_min": 0.6, "abs_time_max_ns": 4.0,
                                                "energy_window_gev": [40, 60]}, indent=1), encoding="utf-8")
    prompt = ("data/ holds six runs of simulated events (energy_gev, quality, time_ns, and the MC truth flag "
              "mc_signal). Apply the selection in cuts.json. For each run, measure the signal efficiency and "
              "purity of the selection with uncertainties, check whether the runs are consistent with each "
              "other, and combine them into a single efficiency with an uncertainty. If a run is inconsistent, "
              "decide how to treat it and justify it. Save per-run results to files, build a summary table, and "
              "write report.md.")
    return Task("L2_runs_combination", prompt, [f"data/run{r:03d}.csv" for r in range(1, 7)] + ["cuts.json"],
                {"per_run": runs, "efficiency": truth_eff, "odd_run": 4},
                ["run 4 has worse quality resolution (lower efficiency); natural long-pipeline checks"])


TASKS_LONG = [l1_calo_design, l2_runs_combination]
