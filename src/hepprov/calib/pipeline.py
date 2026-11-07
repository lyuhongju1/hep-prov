"""Reference pipeline: a toy liquid-argon scintillation study written the way an agent writes it.

Four steps, each a Python script run under ``hepprov exec``:

  simulate  per-event logs ("Event i: N hits detected") and per-event tables
            for {baseline, optimized} x {electron, proton} x {1, 2, 5} MeV,
            Poisson photostatistics, proton quenching
  analyze   per-point mean / resolution / yield per MeV, proton/electron
            quenching per energy and its mean and std -> results.json
  compare   optimized vs baseline light yield -> comparison.json
  report    report.md with numbers formatted from the JSON files

``Defects`` switches on one injected defect class. The clean pipeline (no
defect) is the control: whatever the detectors flag on it is a false alarm.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hepprov.recorder import Recorder

ENERGIES = (0.001, 0.002, 0.005)

SIMULATE = r'''
import os, sys, json, numpy as np
P = json.load(open("params.json"))
PARTS = sys.argv[1:] or ["electron", "proton"]
rng = np.random.default_rng([P["seed"], len(PARTS[0])])
LY = {"baseline": 1480.0, "optimized": 1480.0 * P["gain"]}
QF = {0.001: 0.50, 0.002: 0.42, 0.005: 0.36}
for geom, ly in LY.items():
    for part in PARTS:
        for E in P["energies"]:
            if [geom, part, E] in P.get("omit", []):
                continue
            d = f"energy_{E:.3f}GeV"
            batched = P.get("batch") == [geom, part, E]
            mean = ly * E * 1000 * (QF[E] if part == "proton" else 1.0)
            if batched:
                mean *= P.get("batch_factor", 1.0)
            n = P["n_events"]
            hits = rng.poisson(mean, n)
            chunks = [hits[: n // 2], hits[n // 2:]] if batched else [hits]
            for bi, ch in enumerate(chunks):
                dd = os.path.join(d, f"batch_{bi:03d}") if batched else d
                os.makedirs(dd, exist_ok=True)
                with open(os.path.join(dd, f"{geom}_{part}_geant4.log"), "w") as f:
                    f.write("".join(f"Event {i}: {h} hits detected\n" for i, h in enumerate(ch)))
            with open(os.path.join(d, f"{geom}_{part}_events.csv"), "w") as f:
                f.write("eventID,nHits\n" + "".join(f"{i},{h}\n" for i, h in enumerate(hits)))
            if P.get("merged_name"):
                with open(os.path.join(d, f"{geom}_events_merged.csv"), "w") as f:
                    f.write("eventID,nHits\n" + "".join(f"{i},{h}\n" for i, h in enumerate(hits)))
'''

ANALYZE = r'''
import os, json, math, hepprov
P = json.load(open("params.json"))
def load(geom, part, E):
    f = os.path.join(f"energy_{E:.3f}GeV", f"{geom}_{part}_events.csv")
    __SKIP__
    rows = open(f).read().split()[1:]
    return [int(r.split(",")[1]) for r in rows]
res = {"points": {}, "quenching": {}}
for geom in ("baseline", "optimized"):
    for part in ("electron", "proton"):
        for E in P["energies"]:
            h = load(geom, part, E)
            if h is None:
                continue
            n = len(h); mu = sum(h) / n
            sd = math.sqrt(sum((x - mu) ** 2 for x in h) / n)
            res["points"][f"{geom}/{part}/{E*1000:g}MeV"] = {
                "n_events": n, "mean_hits": mu, "resolution": sd / mu, "yield_per_mev": mu / (E * 1000)}
q = []
for E in P["energies"]:
    e = res["points"].get(f"baseline/electron/{E*1000:g}MeV"); p = res["points"].get(f"baseline/proton/{E*1000:g}MeV")
    if e and p:
        qf = p["mean_hits"] / e["mean_hits"]
        __PLACEHOLDER__
        res["quenching"][f"{E*1000:g}MeV"] = qf
        q.append(qf)
m = sum(q) / len(q)
res["quenching_mean"] = m
res["quenching_std"] = math.sqrt(sum((x - m) ** 2 for x in q) / len(q))
hepprov.emit("quenching_mean", m)
text = json.dumps(res, indent=1)
__TRUNCATE__
open("results.json", "w").write(text)
'''

COMPARE = r'''
import json
r = json.load(open("results.json"))
__BASELINE__
opt = r["points"]["optimized/electron/1MeV"]["yield_per_mev"]
pct = (opt / base - 1) * 100
json.dump({"baseline_yield_per_mev": base, "optimized_yield_per_mev": opt, "improvement_pct": pct},
          open("comparison.json", "w"), indent=1)
'''

REPORT = r'''
import json
r = json.load(open("results.json")); c = json.load(open("comparison.json"))
pts = r["points"]
L = ["# Light-yield study", ""]
for k in sorted(pts):
    p = pts[k]
    L.append(f"- {k}: mean {p['mean_hits']:.2f} hits over {p['n_events']} events, "
             f"resolution {p['resolution']:.4f}, yield {p['yield_per_mev']:.2f} PE/MeV")
L.append(f"\nAverage quenching factor {r['quenching_mean']:.4f} with spread {r['quenching_std']:.4f}.")
L.append(f"The optimized geometry improves the 1 MeV light yield from {c['baseline_yield_per_mev']:.2f} "
         f"to {c['optimized_yield_per_mev']:.2f} PE/MeV, a {c['improvement_pct']:.2f} percent gain.")
__FABRICATE__
open("report.md", "w").write("\n".join(L) + "\n")
'''


@dataclass
class Defects:
    """Exactly one field set = one injected defect instance; all default = clean control."""
    leftover: tuple | None = None          # (geom, part, E): file left from an earlier run; simulate omits it
    transcribe_baseline: bool = False      # compare.py retypes the baseline yield as a literal
    placeholder_energy: float | None = None  # analyze substitutes 1.0 for the quenching at this energy
    silent_skip: tuple | None = None       # (geom, part, E): simulate omits it, analyze skips silently
    truncate: bool = False                 # analyze writes a cut-off results.json (and a whole copy elsewhere)
    merged_overwrite: bool = False         # one merged file name shared by two particles
    fabricate: str | None = None           # a number the report states that nothing computed
    batch: tuple | None = None             # (geom, part, E) run in batches with this suppression
    batch_factor: float = 0.47
    hand_edit: tuple | None = None         # results.json edited outside the recorder: key path
    superseded: float | None = None        # simulate.py changed (light yield x this) and re-run for electrons only
    params: dict = field(default_factory=dict)


def _scripts(df: Defects) -> dict[str, str]:
    skip = ("if not os.path.exists(f):\n        return None" if df.silent_skip else
            "assert os.path.exists(f), f'missing {f}'")
    ana = ANALYZE.replace("__SKIP__", skip)
    ph = "pass"
    if df.placeholder_energy is not None:
        ph = f"if abs(E - {df.placeholder_energy}) < 1e-12: qf = 1.0  # TODO compute (placeholder)"
    ana = ana.replace("__PLACEHOLDER__", ph)
    tr = "pass"
    if df.truncate:
        tr = 'open("results_full.json", "w").write(text); text = text[: len(text) // 2]'
    ana = ana.replace("__TRUNCATE__", tr)
    # with a silent skip the loop must tolerate None: a continue-based variant
    if df.silent_skip:
        ana = ana.replace("            h = load(geom, part, E)\n            if h is None:\n                continue",
                          "            f = os.path.join(f\"energy_{E:.3f}GeV\", f\"{geom}_{part}_events.csv\")\n"
                          "            if not os.path.exists(f):\n                continue\n"
                          "            h = load(geom, part, E)")
    cmp_ = COMPARE.replace("__BASELINE__", 'base = r["points"]["baseline/electron/1MeV"]["yield_per_mev"]')
    rep = REPORT.replace("__FABRICATE__",
                         f'L.append("The timing resolution is {df.fabricate} ns.")' if df.fabricate else "pass")
    if df.truncate:
        cmp_ = cmp_.replace('json.load(open("results.json"))', 'json.load(open("results_full.json"))')
        rep = rep.replace('r = json.load(open("results.json"))', 'r = json.load(open("results_full.json"))')
    return {"simulate.py": SIMULATE, "analyze.py": ana, "compare.py": cmp_, "report.py": rep}


def run_pipeline(root: Path, df: Defects | None = None, seed: int = 1, n_events: int = 400,
                 gain: float = 1.286) -> Path:
    """Run the reference pipeline under hepprov in ``root``; return the report path."""
    df = df or Defects()
    root.mkdir(parents=True, exist_ok=True)
    params = {"seed": seed, "n_events": n_events, "gain": gain, "energies": list(ENERGIES), **df.params}
    omit = []
    if df.leftover:
        omit.append(list(df.leftover))
    if df.silent_skip:
        omit.append(list(df.silent_skip))
    params["omit"] = omit
    if df.batch:
        params["batch"] = list(df.batch)
        params["batch_factor"] = df.batch_factor
    if df.merged_overwrite:
        params["merged_name"] = True

    scripts = _scripts(df)
    for name, code in scripts.items():
        (root / name).write_text(code, encoding="utf-8")
    (root / "params.json").write_text(json.dumps(params), encoding="utf-8")
    if df.leftover:   # an earlier run's file at the expected path, different statistics
        g, p, E = df.leftover
        d = root / f"energy_{E:.3f}GeV"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{g}_{p}_events.csv").write_text(
            "eventID,nHits\n" + "".join(f"{i},{int(1480 * E * 1000 * 1.7) + (i % 7)}\n" for i in range(5000)))

    rec = Recorder(root)
    rec.init(declare=list(scripts) + ["params.json"])
    py = sys.executable
    rec.exec([py, "simulate.py", "electron"], label="simulate_electron", echo=False)
    rec.exec([py, "simulate.py", "proton"], label="simulate_proton", echo=False)
    if df.superseded:
        # the agent "fixes" the simulation and re-runs only one particle: proton outputs are now from old code
        new_sim = scripts["simulate.py"].replace('LY = {"baseline": 1480.0,',
                                                 f'LY = {{"baseline": 1480.0 * {df.superseded},')
        assert new_sim != scripts["simulate.py"]
        rec.write_file("simulate.py", new_sim, label="rewrite simulate.py")
        rec.exec([py, "simulate.py", "electron"], label="simulate_electron_fixed", echo=False)
    rec.exec([py, "analyze.py"], label="analyze", echo=False)
    if df.hand_edit:
        r = json.loads((root / "results.json").read_text())
        node = r
        keys = list(df.hand_edit)
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = node[keys[-1]] * 1.10
        (root / "results.json").write_text(json.dumps(r, indent=1))
    if df.transcribe_baseline:
        src = root / "results_full.json" if df.truncate else root / "results.json"
        base = json.loads(src.read_text())["points"]["baseline/electron/1MeV"]["yield_per_mev"]
        code = scripts["compare.py"].replace(
            'base = r["points"]["baseline/electron/1MeV"]["yield_per_mev"]',
            f"base = {base:.2f}  # baseline from previous step")
        (root / "compare.py").write_text(code, encoding="utf-8")
        rec.declare("compare.py", note="rewritten by the agent")
    rec.exec([py, "compare.py"], label="compare", echo=False)
    rec.exec([py, "report.py"], label="report", echo=False)
    return root / "report.md"
