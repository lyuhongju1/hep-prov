# HEP-Prov: design (draft 1, 2026-11-05)

**One line:** a provenance recorder and verifier for AI agents doing physics. It records what an agent's run actually read, wrote and executed, without the agent's cooperation. It then checks every number in the agent's report against that record, and publishes its own detection rates.

## 1. Problem

An agent that writes and runs its own analysis code can report numbers that were never computed from its data, and nothing in its output shows it. The GRACE audit (AISciK/NeurIPS 2026) found, in one public corpus:

- a retyped constant reported as a 204.62% improvement;
- a placeholder 1.0 averaged into a "quenching factor";
- two analyses computed from a stale file no step had written;
- an energy point silently suppressed by batching;
- three truncated results files;
- 64 of 64 self-verification records saying `plausible: true`.

All of these sit in plumbing that any agent has, in any physics domain. The fix has to sit outside the agent, because the agent's own record is exactly what can't be trusted.

## 2. Prior art and the gap

| Work | Records | Hashes inputs at read time / stale detection | Number in text → computation | Calibrated (fault injection, FP rate) | Domain |
|---|---|---|---|---|---|
| PROV-AGENT / Flowcept (2508.02866) | prompts, tool I/O, via instrumentation inside the agent | no | lineage queries, not prose numbers | no | HPC workflows |
| LEDGER (2608.18398) | sidecar from agent hooks and transcript | no (paths parsed from text) | claim–evidence edges, LLM-inferred ("audit aid, not source of truth") | no | coding / data analysis |
| ReAgent (2609.22111) | static audit plus re-execution of experiments | no | per claim, not per number | no (manual ground truth) | ML papers |
| Proof-Carrying Numbers (2509.06902) | display-layer tags on numbers | no | to a claim store, not a computation; atomic values only | proofs, no empirical evaluation | statistics |
| HZZ agent case study (2611.03373) | manual practice: checksums, seeds, frozen inputs | partly (manual) | no (tables regenerate figures) | no | HEP |
| claimtrail 0.5.1 (PyPI, github.com/patrickt6/claimtrail, MIT, 2026-09) | cooperative `@tracked` runs and `claim(...)` assertions; non-cooperative "watch" via Claude Code hooks | hashes inputs; flags inputs **changed since the run**; does not check that a read file was produced by the workflow | report markers with MATCH/DRIFT/UNBACKED, tolerance or pattern matching ("treat a DRIFT as 'go look'") | unit, property and mutation tests; no FP/FN rates on real pipelines | general (single author, pre-adoption) |

The survey (2606.04990) names the open gaps: no benchmark covers the complete provenance chain, and tamper-evident or attested traces are rarely reported.

**HEP-Prov's claim to novelty (to be defended).** claimtrail already does cooperative claim binding and change-since-run detection, so neither of those is new. The new parts are:
1. **Read-time attestation against a write manifest.** A read is checked against what this run itself wrote. That catches a file no step produced, a leftover in a reused directory, or an older version. Detecting that an input changed later is a different, weaker check.
2. Capture that **doesn't depend on the agent or the language**: filesystem diffs for any binary (Geant4, Opticks), and import-level hooks for any Python process.
3. **Non-cooperative, exact-digit, null-calibrated derivation** of reported numbers: identity, copy, and a bounded formula grammar with decoy-measured chance rates. This replaces tolerance or pattern matching.
4. A **measured detection profile** from fault injection on physics pipelines, with false-positive rates on clean runs.
5. **Physics plugins:** log-tier quantities and invariants.

Each piece exists somewhere on its own. Their combination, evaluated on real physics agents, does not.

## 3. Architecture

```
 agent (any: GRACE, Claude Code, JFC, a script)
   │  every shell / python execution goes through ──►  hepprov exec -- <cmd>
   ▼
 RECORDER ──► MANIFEST (append-only, hash-chained JSONL; exports to W3C PROV-JSON / RO-Crate)
                   ▲
 report.md ──► VERIFIER ──► per-number verdicts + defect findings + run verdict
                   ▲
             PLUGINS (hep: ROOT/parquet event counts, per-event logs, invariants)
```

**Recorder.** Three capture layers, each with a known blind spot. Each layer reports what it saw, not what it assumed.
- L1, filesystem diff around every command. Catches writes by any binary (Geant4, Opticks, C++).
- L2, in-process Python hooks (`sys.addaudithook`, plus wrappers for pandas, pyarrow, uproot and numpy). Records path and sha256 at read time. The GRACE prelude already does this.
- L3, optional syscall tracing on Linux (strace or eBPF), for reads by non-Python binaries.
- Also recorded: command line, environment hash, interpreter and package versions, seeds, and when the agent is wrapped, the LLM model and sampling settings of the call that produced each script.

**Manifest.** Every event (`exec`, `read`, `write`, `llm_call`, `emit`) is one JSON line carrying the hash of the previous line, so any edit is detectable. Optional signature on the final head hash. The manifest is the "primitive evidence" tier from the audit, made explicit.

**Verifier.** Two modes.
- *Cooperative:* code calls `hepprov.emit("light_yield_1MeV", value)`, and the report cites `{{claim:light_yield_1MeV}}`. Verification is an exact lookup.
- *Non-cooperative:* the audit's method, generalized. Every number in the prose is tested in order against identity, copy, and a bounded formula grammar over the manifest's quantities. Decoy-calibrated nulls, exact-digit gates and the two-lines-of-evidence promotion rule all carry over unchanged.
- Plus checks on the manifest itself: reads of unattested files, stale versions, overwrites, scripts whose outputs come only from literals (AST taint), existence-guarded silent skips, truncated or non-atomic outputs.

**HEP plugin.** It turns per-event logs and ROOT/parquet files into log-tier quantities (n, mean, sigma per configuration). It adds the invariants: bounds, Poisson floor, batch ratio, linearity for EM particles. Other physics domains (optics, materials, cosmology pipelines) are additional plugins later.

## 4. Evaluation plan

1. **Retrospective, GRACE corpus (18 runs).** HEP-Prov must reproduce the audit's asserted findings from the archive alone, in non-cooperative mode with L1 only.
2. **Live, local, no HPC.** A small reference agent (an LLM writing analysis code over toy MC: numpy/uproot event files, plus Geant4's examples if they install) runs under HEP-Prov. Injected defect classes:
   - stale input at the expected path;
   - transcribed constant;
   - placeholder in a mean;
   - existence-guarded skip;
   - truncated JSON;
   - batch-style suppression;
   - overwrite between steps;
   - fabricated report number.

   Report per-layer detection rates and false-positive rates on clean runs.
3. **External corpus.** The HZZ agent study publishes code and data (GitHub plus Hugging Face). Audit it as an independent, non-GRACE physics agent. If others release artifacts, include them too (JFC, AgentRivet).
4. **Overhead.** Wall time and storage per run, with and without L3.

## 5. Milestones

- **M0** (this week): check claimtrail and any other close prior art. Freeze this design. Repo skeleton, manifest format, `hepprov exec`, L1 and L2.
- **M1:** verifier, both modes. Retrospective result on the GRACE corpus (item 1).
- **M2:** reference agent and fault-injection harness, with the detection profile (item 2).
- **M3:** external corpus (item 3), overhead (item 4), paper draft.

## 6. Open decisions

- **Name and scope.** "HEP-Prov" with physics plugins, or a domain-neutral core with "HEP" as the first plugin? Recommendation: neutral core, HEP launch.
- **License.** MIT or Apache-2.0.
- **Where it lives.** A local repo now (`Downloads/HEP-Prov`). A GitHub repo when you say so.
- **Co-authors and venue.** A physics-software venue (CHEP, Computing and Software for Big Science) or an ML venue (NeurIPS Datasets and Benchmarks).
- **Relationship to GRACE.** GRACE becomes the first adopter. `grace.integrity` could depend on HEP-Prov later.
