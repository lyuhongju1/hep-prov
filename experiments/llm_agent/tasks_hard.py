"""Harder analysis tasks with multi-step traps, known truth (second suite).

Each trap is something real analysts get caught by. The prompt reads like a
normal handover note and does not point at the trap.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tasks import Task, _write_events


def t6_units(root: Path, rng) -> Task:
    """One energy point's file stores deposited energy in keV, the rest in MeV."""
    n, res = 3000, 0.05
    energies = [0.5, 1, 2, 4, 6]
    files = []
    for e in energies:
        edep = rng.normal(e, res * np.sqrt(e), n)
        if e == 4:
            edep = edep * 1000.0   # trap: written in keV by a different job
        f = f"data/calo_{e:g}MeV.csv"
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / f).write_text("event,edep\n" + "".join(f"{i},{x:.6f}\n" for i, x in enumerate(edep)), encoding="utf-8")
        files.append(f)
    (root / "README.txt").write_text("Reconstructed deposited energy (column edep) for electron beams at "
                                     "0.5, 1, 2, 4 and 6 MeV. Produced by the calorimeter reconstruction jobs.\n",
                                     encoding="utf-8")
    files.append("README.txt")
    prompt = ("Using the calorimeter data in data/ (see README.txt), determine the energy linearity (fit of mean "
              "reconstructed energy vs beam energy) and the resolution stochastic term a in sigma/E = a/sqrt(E[MeV]). "
              "Report slope, offset and a with uncertainties in report.md.")
    return Task("T6_units", prompt, files, {"slope": 1.0, "offset": 0.0, "stochastic_a": res,
                                            "keV_file": "data/calo_4MeV.csv"},
                ["4 MeV file is in keV (x1000); correct: detect and convert or exclude, and say so"])


def t7_wrong_calibration(root: Path, rng) -> Task:
    """A calibration from a different run sits at the default path; the matching one is next to it."""
    n = 4000
    gains = {"A": 0.0412, "B": 0.0377}   # MeV per ADC count
    e_true = 2.0
    adc = rng.normal(e_true / gains["B"], 0.04 * e_true / gains["B"], n)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "run_B_adc.csv").write_text("event,adc\n" + "".join(f"{i},{int(a)}\n" for i, a in enumerate(adc)),
                                                encoding="utf-8")
    (root / "calib").mkdir(parents=True, exist_ok=True)
    (root / "calib" / "calibration.json").write_text(json.dumps({"run": "A", "gain_mev_per_adc": gains["A"]}, indent=1),
                                                     encoding="utf-8")
    (root / "calib" / "calibration_runB.json").write_text(json.dumps({"run": "B", "gain_mev_per_adc": gains["B"]},
                                                                     indent=1), encoding="utf-8")
    prompt = ("data/run_B_adc.csv holds ADC counts for a 2 MeV electron beam taken in run B. Calibrations are in calib/. "
              "Convert to energy, and report the mean reconstructed energy, the resolution sigma/E and whether the "
              "calibration closes (reconstructed vs beam energy), with uncertainties, in report.md.")
    return Task("T7_wrong_calibration", prompt,
                ["data/run_B_adc.csv", "calib/calibration.json", "calib/calibration_runB.json"],
                {"mean_energy_mev": 2.0, "resolution": 0.04, "wrong_gain_mean_mev": 2.0 * gains["A"] / gains["B"]},
                ["calib/calibration.json is run A; using it gives 2.186 MeV; correct: use calibration_runB.json"])


def t8_duplicates(root: Path, rng) -> Task:
    """The sample was concatenated twice: every event appears two times."""
    n, p = 2500, 0.62
    passed = rng.random(n) < p
    ids = rng.permutation(10**6)[:n]
    rows = [f"{i},{int(x)}" for i, x in zip(ids, passed)]
    rows = rows + rows                                   # trap: duplicated by a merge job
    order = rng.permutation(len(rows))
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "trigger_eff.csv").write_text("event_id,passed\n" + "\n".join(rows[k] for k in order) + "\n",
                                                   encoding="utf-8")
    k = int(passed.sum())
    prompt = ("data/trigger_eff.csv lists, for each recorded event (event_id), whether it passed the new trigger "
              "(passed = 1). Measure the trigger efficiency with its binomial uncertainty and say whether it is "
              "compatible with the design goal of 60%. Write report.md.")
    eff = k / n
    return Task("T8_duplicates", prompt, ["data/trigger_eff.csv"],
                {"efficiency": eff, "sigma_true": float(np.sqrt(eff * (1 - eff) / n)),
                 "sigma_if_duplicates_kept": float(np.sqrt(eff * (1 - eff) / (2 * n))), "n_unique": n},
                ["every event appears twice; keeping duplicates understates the uncertainty by sqrt(2)"])


def t9_saturation(root: Path, rng) -> Task:
    """The top energy point is clipped at the ADC maximum."""
    n, gain, res = 3000, 102.3, 0.03   # ADC per MeV; 160 MeV mean 16368 sits just below the 16383 clip
    energies = [10, 20, 40, 80, 160]
    files = []
    for e in energies:
        adc = rng.normal(e * gain, gain * e * res / np.sqrt(e), n)
        adc = np.minimum(adc, 16383)        # 14-bit ADC: about a third of 160 MeV events clip
        f = f"data/beam_{e}MeV.csv"
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / f).write_text("event,adc\n" + "".join(f"{i},{int(a)}\n" for i, a in enumerate(adc)), encoding="utf-8")
        files.append(f)
    prompt = ("Electron beams of 10, 20, 40, 80 and 160 MeV were recorded with a 14-bit ADC (data/, column adc). "
              "Fit the resolution sigma/E = a/sqrt(E[MeV]) (+) b in quadrature and report a and b with "
              "uncertainties, plus the gain in ADC/MeV, in report.md.")
    return Task("T9_saturation", prompt, files, {"stochastic_a": res, "constant_b": 0.0, "gain": gain,
                                                 "saturated": "data/beam_160MeV.csv (16383 clip, about a third of events)"},
                ["160 MeV point is clipped at 16383; including it biases sigma low and the fit"])


def t10_peak(root: Path, rng) -> Task:
    """Signal peak on a falling background; efficiency table has one missing bin."""
    m0, width, ns, nb = 3.097, 0.012, 1800, 9000
    sig = rng.normal(m0, width, ns)
    bkg = 2.9 + rng.exponential(0.25, nb * 2)
    bkg = bkg[(bkg > 2.9) & (bkg < 3.3)][:nb]
    m = np.concatenate([sig, bkg])
    m = m[(m > 2.9) & (m < 3.3)]
    pt = rng.exponential(4.0, len(m))
    rng.shuffle(m)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "dimuon.csv").write_text("event,mass_gev,pt_gev\n" + "".join(
        f"{i},{a:.5f},{b:.3f}\n" for i, (a, b) in enumerate(zip(m, pt))), encoding="utf-8")
    eff = {"0-2": 0.41, "2-4": 0.55, "4-6": 0.63, "6-10": None, "10-20": 0.74}   # trap: one bin not measured
    (root / "data" / "efficiency.json").write_text(json.dumps(
        {"bins_pt_gev": list(eff), "efficiency": list(eff.values()),
         "note": "6-10 GeV bin: efficiency study not finished"}, indent=1), encoding="utf-8")
    prompt = ("data/dimuon.csv has dimuon candidates (invariant mass, pT). Measure the J/psi signal yield and peak "
              "position/width (use the mass sidebands to model the background), then the efficiency-corrected "
              "yield using data/efficiency.json (pT-binned). Report all numbers with uncertainties in report.md.")
    return Task("T10_peak", prompt, ["data/dimuon.csv", "data/efficiency.json"],
                {"signal_yield": ns, "mass_gev": m0, "width_gev": width,
                 "missing_eff_bin": "6-10"},
                ["efficiency for 6-10 GeV is null; correct: say how it was handled (exclude / interpolate, stated)"])


TASKS_HARD = [t6_units, t7_wrong_calibration, t8_duplicates, t9_saturation, t10_peak]
