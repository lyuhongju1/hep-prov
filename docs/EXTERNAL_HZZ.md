# External corpus: the HZZ / CEPC Bell-inequality agent study (2026-11-06)

**Corpus.** github.com/AItutorialjrb/agentic_HZZ_case at commit `9234f29`, the code and reproducibility materials of arXiv:2611.03373. It is a Codex-driven analysis that is independent of GRACE and of HEP-Prov's authors. It contains 59 agent-written Markdown reports (`docs/`) and 207 results files (`results/`, JSON and CSV). The raw event ntuples live on Hugging Face and were not used. The repository was read as data; none of its code was run.

**What was tested.** Archive mode, report against archive: is every number in the agent's reports supported by its archived results? There's no manifest, so read attestation doesn't apply. Script: `examples/hzz_audit.py REPO OUT.json`.

## Result

Regenerated on 2026-11-14 with the current code (`examples/hzz_audit.py`). An earlier version of this page reported an evidence pool of 260,934 values and correspondingly higher chance rates; the verifier has since changed how it reads large results files, and the pool is now 45,340 values.

| Significant figures | Claims | Exact match to an archived value | Derived from nearby supported numbers | Unexplained | Exact matches expected by chance |
|---|---|---|---|---|---|
| 3 | 427 | 422 | 0 | 5 | 162 (38%) |
| 4 | 318 | 278 | 11 | 29 | 49 (16%) |
| ≥5 | 468 | 424 | 25 | 19 | 1.1 (0.2%) |

**The chance column is the main finding about method.** At 3 significant figures, about 38% of numbers would match some archived value by chance, and at 4 about 16%. Only from 5 figures on is an exact match informative: the chance rate is 1.1% at 5 figures, 0.3% at 6 and 0.02% at 7, and 405 of the 468 numbers with ≥5 figures have 6 or more. A verifier that reports "91% of numbers supported" without this null misleads. The chance rate of exact matches is measured by this script. HEP-Prov's main check accepts exact matches at any precision, and the chance-rate rules apply to derived matches (see the limitations in the paper).

**At ≥5 significant figures, 449 of 468 claims (96%) are supported.** 424 match an archived value exactly, with about 1 match expected by chance. 25 are derived from neighbouring supported numbers, all with local chance rates ≤ 5%. Example: "+0.660850 (2.609061 → 3.269912)" is reproduced exactly only from the full-precision archived I3 values, not from the printed rounded ones.

**The 19 unexplained high-precision numbers:**
- Restatements of a derived value away from its inputs: "+0.660850" (4×) and "+0.693353" (2×).
- Stage-by-stage I3 shifts comparing two detector-card variants (`legacy_hzz_delphes_candidate_v2_comparison.md`, 7 numbers), whose inputs aren't in the archived results.
- Sums over the unarchived ntuples (3741.87 and 6009.64 GeV).
- The Z-boson mass, 91.1876 GeV (2×), an external constant.
- A generated cross section (5.85273e-05 pb) and one table angle (0.053310847), whose sources aren't archived.

**The 34 unexplained 3–4-figure numbers** are mostly not results: the Pythia version 8.317 (14×), the isolation setting 9999 (10×), source line numbers and a test runtime. Four are physical values.

None looks like a defect. They point to evidence that wasn't archived or to metadata. This study is careful: it records input SHA256s, seeds and commits. On it, HEP-Prov finds **no defect**. That's a clean-agent result, the expected outcome for a careful analysis, and a measurement of false-alarm behavior on a real report.

## What this changed in HEP-Prov

All of these were found on this corpus, not designed in advance:
- signed numbers (`-0.473641571`);
- values with 9 or more decimals, which were silently skipped before;
- CSV evidence tables;
- metadata exclusion (seeds, sizes, versions, date stamps, inline code);
- a substring bug ("GiB" matched "eligible");
- in-text derivation over a report's neighbourhood with full-precision evidence;
- alias-aware multiplicity;
- a repeated number explaining itself.

## Limits

- Report-vs-archive consistency is a weaker test than attested provenance. A number can match an archived value that is itself wrong.
- The unexplained count still includes restatements across documents. Cross-document linking isn't implemented.
- The calibration in `docs/CALIBRATION.md` predates these extraction changes. It needs one rerun; the 2026-11-06 rerun was stopped by the host for low memory.
