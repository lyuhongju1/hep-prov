# External corpus: the HZZ / CEPC Bell-inequality agent study (2026-11-06)

**Corpus.** github.com/AItutorialjrb/agentic_HZZ_case at commit `9234f29`, the code and reproducibility materials of arXiv:2611.03373. It is a Codex-driven analysis that is independent of GRACE and of HEP-Prov's authors. It contains 59 agent-written Markdown reports (`docs/`) and 207 results files (`results/`, JSON and CSV). The raw event ntuples live on Hugging Face and were not used. The repository was read as data; none of its code was run.

**What was tested.** Archive mode, report against archive: is every number in the agent's reports supported by its archived results? There's no manifest, so read attestation doesn't apply. Script: `hzz_audit2.py` `examples/hzz_audit.py REPO OUT.json`.

## Result

| Significant figures | Claims | Exact copy of an archived value | Derived from nearby supported numbers | Unexplained | Copies expected by chance |
|---|---|---|---|---|---|
| 3 | 431 | 411 | 0 | 20 | 398 |
| 4 | 318 | 283 | 4 | 31 | 146 |
| ≥5 | 456 | 413 | 24 | 19 | 3.4 |

**The chance column is the main finding about method.** The evidence pool has 260,934 values. At 3 significant figures, an exact match is no evidence at all (411 matched against 398 expected by chance). At 4 it's weak. Only at 5 or more is identity support informative. A verifier that reports "91% of numbers supported" without this null misleads. HEP-Prov now reports the per-claim chance rate for identity matches as well as for formulas.

**At ≥5 significant figures, 437 of 456 claims (96%) are supported.** 413 match an archived value exactly, with about 3 matches expected by chance. 24 are derived from neighbouring supported numbers, all with local chance rates ≤ 5%. Example: "+0.660850 (2.609061 → 3.269912)" is reproduced exactly only from the full-precision archived I3 values, not from the printed rounded ones.

**The 19 unexplained high-precision numbers** fall into three groups:
- Restatements of a derived value away from its inputs: "+0.660850" in summary documents (5×), "+0.693353".
- Stage-by-stage I3 shifts comparing two detector-card variants (`legacy_hzz_delphes_candidate_v2_comparison.md`), whose inputs aren't in the archived results.
- Sums over the unarchived ntuples (3741.87 and 6009.64 GeV, 2716.40575 GeV).

None looks like a defect. They point to evidence that wasn't archived. This study is careful: it records input SHA256s, seeds and commits. On it, HEP-Prov finds **no defect**. That's a clean-agent result, the expected outcome for a careful analysis, and a measurement of false-alarm behavior on a real report.

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
