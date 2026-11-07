# Detection profile, v0.0.1 (2026-11-05)

Command: `python -m hepprov.calib.harness OUT --clean-seeds 20`. The raw output is `OUT/calibration.json`.

**Setup.** The reference pipeline (`hepprov.calib.pipeline`) is a toy LAr scintillation study:
- simulate electrons and protons;
- analyze (per-point statistics, quenching);
- compare (optimized vs baseline);
- report.

It runs under `hepprov exec`. For each defect class, three instances were injected; instances differ in energy, particle, value and seed. An instance is detected by a detector if that detector raises a flag absent from the clean control. The rule was fixed before running.

| Defect class | Expected flag | Detected | attest | outputs | static | verify | invariants | provenance |
|---|---|---|---|---|---|---|---|---|
| D1 stale input (leftover at the expected path) | leftover_from_before_run | 3/3 | 3/3 | 0/3 | 0/3 | 3/3 | 0/3 | 0/3 |
| D2 transcribed constant | transcribed_literal | 3/3 | 0/3 | 0/3 | 3/3 | 0/3 | 0/3 | 0/3 |
| D3 placeholder in a mean | placeholder_signature | 3/3 | 0/3 | 0/3 | 0/3 | 3/3 | 0/3 | 0/3 |
| D4 existence-guarded silent skip | silent_skip | 3/3 | 0/3 | 0/3 | 3/3 | 0/3 | 0/3 | 0/3 |
| D5 truncated results file | truncated_output | 3/3 | 0/3 | 3/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| D6 overwrite between steps | overwrite | 3/3 | 3/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 |
| D7 fabricated report number | unexplained_claim | 3/3 | 0/3 | 0/3 | 0/3 | 3/3 | 0/3 | 0/3 |
| D8 batching suppression | I3 | 3/3 | 0/3 | 0/3 | 0/3 | 1/3 | 3/3 | 0/3 |
| D9 edit outside the recorder | written_outside_recorder | 3/3 | 3/3 | 0/3 | 0/3 | 3/3 | 0/3 | 0/3 |
| D10 output of a superseded script version, read afterwards | superseded_output | 3/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 3/3 |

**False alarms:** 0 of 20 clean runs (seeds 1000–1019) raised any flag. (The table is the 2026-11-07 run with ten classes: 30/30 detected.) The control run also raised none.

D1 and D9 are each caught by two independent detectors: attestation at the bad read, and taint propagation in the verifier. Every other class is caught by exactly one detector. Taint works at file level: everything written by a step that read a flagged input is tainted, even values that didn't use it. That over-approximation errs toward flagging.

## What these numbers do not show

- **In-sample.** The defect classes and the detectors were designed together, from the GRACE audit's findings. The table shows the detectors are *implemented* correctly for these classes. It doesn't show they find unfamiliar defects. Evidence for that has to come from runs whose defects were not known in advance (planned: the HZZ agent study; LLM-driven runs).
- **Coarse.** Three instances per class give detection rates to the nearest third. Twenty clean runs bound the false-alarm rate per run at roughly ≤ 14% (95%, zero events). They don't show it is zero.
- **Toy pipeline.** Report numbers are formatted straight from JSON or logs. Real agent reports paraphrase, round differently and restate values in other units. The unexplained-claim false-alarm rate on real reports is unmeasured. On the GRACE v9 DarkSide report, 14 of 108 numbers are unexplained, so many would be flagged.
- **Classes not tested:**
  - reads by non-Python binaries (L3 is not built);
  - placeholders that aren't 1.0;
  - transcription into non-Python code;
  - numbers fused to letters ("3.14x", "50mm"), which aren't extracted;
  - tampering by an adversary who also rewrites the manifest (there's a chain, but no signature yet).
- **Reruns on 2026-11-06 at commits `0111e6a` and `c143426`:** after the extraction fixes from the HZZ corpus and the false-alarm fixes from the live-agent pilot (see `docs/LIVE_AGENT_PILOT.md`), the table is unchanged: 27/27 detected, 0/20 clean runs flagged, 0 control flags.
- **2026-11-07 (commit `0ac6a77`):** D10 added (superseded script output). The ten-class run detects 30/30 with 0/20 clean runs flagged, after the prose-arithmetic, superseded-output and overwrite-rule changes.
