# HEP-Prov: results summary (as of 2026-11-07)

This summary collects everything a write-up would cite. Details, raw numbers and caveats are in the linked documents.

## What HEP-Prov is

HEP-Prov is a provenance recorder and verifier for AI agents doing physics analyses (design: [DESIGN.md](../DESIGN.md)).
- **Recorder.** `hepprov exec` wraps every command an agent runs. It records the command, the files it wrote (filesystem diff, any language) and every file each Python process read, with the sha256 at read time. The agent doesn't need to cooperate. Everything goes into a hash-chained manifest.
- **Read attestation.** Every read is checked against what *this run* wrote or declared. That catches leftovers from earlier runs, files changed outside the recorder, older versions, and overwrites between steps.
- **Verifier.** Every number in the agent's report gets a verdict:
  - verified (primitive evidence);
  - copy (intermediate);
  - derived (report-local arithmetic over supported numbers, with full-precision inputs);
  - diagnosed (formula over run quantities, decoy-calibrated chance rate);
  - ambiguous;
  - unexplained.
- **Other detectors.**
  - taint propagation, plus counterfactual re-execution to clear values that don't depend on a flagged input;
  - superseded-output detection;
  - static checks for transcribed literals and silent skips;
  - HEP invariants (batch ratio, linearity);
  - a severity level on every flag (`defect` vs `info`).

## 1. Planted-defect calibration (in-sample) — [CALIBRATION.md](CALIBRATION.md)

**30/30 detected, 0/20 clean runs flagged.** That's ten defect classes with three instances each, injected into a reference LAr pipeline and scored differentially against a clean control. The classes:
- stale input
- transcribed constant
- placeholder in a mean
- silent skip
- truncated output
- overwrite
- fabricated number
- batching suppression
- edit outside the recorder
- superseded script output

The calibration was rerun after every verifier change, and the result never moved.

## 2. Retrospective: the GRACE corpus — [EXTERNAL_HZZ.md](EXTERNAL_HZZ.md), M1 in the README

On the archived GRACE DarkSide-v9 run, HEP-Prov rediscovers the artifact audit's three headline findings without supervision:
- **The 204.62% rescale:** 4491.29 = baseline × (1 + 204.62/100), at a 0% decoy chance rate.
- **The placeholder-padded quenching mean:** 0.6314 ± 0.2639 is the mean and std of two log-derived ratios plus a 1.0, corroborated at a 0% joint chance rate.
- **The batching suppression:** 0.47, 0.22, 0.47.

These checks were designed with those findings in view, so this is a rediscovery, not an independent test.

## 3. External corpus: an independent HEP agent study — [EXTERNAL_HZZ.md](EXTERNAL_HZZ.md)

59 reports and 207 results files from the HZZ / CEPC agent study (arXiv:2611.03373), checked in archive mode.
- At 5 or more significant figures, **449/468 report numbers are supported**, against about 1 expected by chance.
- **No defect found.**
- **A method finding.** In this pool of 45,340 values, about 38% of 3-figure and 16% of 4-figure numbers would match some archived value by chance. Identity support needs a null too.

## 4. Live LLM agents under the recorder — [LIVE_AGENT_PILOT.md](LIVE_AGENT_PILOT.md)

95 runs on Claude Opus 5.5 and Claude Haiku 5.5 (plus 2 environment failures), about $9.90 in total.

| Experiment | Runs | Agents | HEP-Prov |
|---|---|---|---|
| Data-visible traps (missing file, units, duplicates, saturation, wrong calibration, stale directory, corrupt rows, missing efficiency bin) | 30 | **30/30 handled correctly** | verification of the reported numbers; 0–3 false-alarm defect flags per report |
| Long open-ended tasks (design scan, multi-run combination) | 4 | 4/4 correct | 1.8 defect flags per run (prose arithmetic) |
| Revision tasks (cut change, new budget and constant, regenerated data) | 24 | **24/24 correct revisions, 0 undisclosed leftover numbers** | 2.4 defect flags per run; 0 defect-level stale claims |
| Scaled mid-run change of an input file | 6 | noticed 5/6 | **6/6 detected** |
| Physically consistent mid-run substitution (fresh sample, same truth), seeds 4–9 | 30 | noticed 16/30 = 53% [36, 70] (Opus 11/12, Haiku 5/18) | **30/30 detected = 100% [89, 100]**, including all 14 misses |

What this establishes:
1. Current models catch data-visible problems themselves. HEP-Prov's role there is independent verification: it confirms that reported numbers trace to the data.
2. Failures outside the agent's view are caught every time (36/36). In 14 of 30 consistent substitutions the agent did not notice, and nothing showed in the data; only provenance recorded them. Agents notice only when they happen to fingerprint the raw file (first rows, min/max, checksum) and compare across reads. Opus does this habitually, Haiku rarely.
3. Natural (unplanted) agent mistakes did not occur in 95 runs, including 24 revisions built to elicit leftover numbers. Agents label or archive earlier values themselves. A detection result for natural mistakes needs much larger, incremental tasks.
4. The false-alarm cost on untampered runs is 1.0–2.4 defect flags per report (59 runs), mostly prose arithmetic beyond the grammar and abandoned scripts.

## 5. What was learned about building such a verifier

Each item was found on real data, fixed, and rechecked against the calibration:
- **Step-level taint over-flags careful agents.** An agent that opens a stale file to reject it taints its whole analysis. *Counterfactual re-execution* fixes this: rerun the step with the flagged input perturbed, plus an unperturbed control, and keep only the values that change (T3: 1 of 54).
- **Observer effect.** The recorder's import hook pre-loads stdlib modules, which masked an agent script named `inspect.py`. Re-executions now reproduce the recorded conditions.
- **Report parsing needs real-report robustness:** signs, values with 9 or more decimals, exponents (including "(x ± y)e-3"), unit prefixes, metadata (seeds, sizes, versions, date stamps), word-level matching ("GiB" inside "eligible").
- **Chance rates everywhere.** Identity matching in large pools, formula matching, and report-local derivation all need a measured null. Below 4 significant figures, numbers can neither be verified nor refuted against large pools ("ambiguous").
- **Operand discipline.** Extra operands (small constants, all primitive values, unit-loss scales) create coincidences that hid fabricated numbers. Those changes were reverted. Only sample sizes are added as primitive operands.
- **Stale claims after revision.** A report number whose only support is a step superseded later (script re-run at a new version, or an input replaced and the step not re-run) is flagged. Three checks keep it honest: regeneration on the current files (unchanged numbers are dropped), disclosure in the paragraph ("in my first inspection..."), and presentational revisions (the new script reproduces what it still prints). On 71 live runs: 0 defect-level stale flags; 74 informational ones, all agents' disclosures of earlier values, each confirmed by regeneration.
- **What remains.** Residual false alarms come from prose arithmetic beyond the grammar (binomial-model predictions, products with small constants, ranges) and from values computed but never printed.

## Open

- Natural-mistake detection: larger incremental tasks, or more agents and more seeds.
- Reads by non-Python binaries (L3 syscall tracing), and manifest signing.
- Public release: GitHub repository not yet created.
