# HEP-Prov

Provenance recorder and verifier for AI agents doing physics. **Status:**
- Milestone 0 (recorder and read attestation): done.
- Milestone 1 (verifier): core done. On the archived GRACE corpus it rediscovers the artifact audit's DarkSide findings without supervision.
- Milestone 2 (fault-injection calibration on a reference pipeline): done. 27/27 injected defects detected, 0/20 clean runs flagged. In-sample; see [docs/CALIBRATION.md](docs/CALIBRATION.md).
- External tests: the HZZ agent corpus ([docs/EXTERNAL_HZZ.md](docs/EXTERNAL_HZZ.md)), and 41 live LLM-agent runs under the recorder (Opus 5.5 and Haiku 5.5, [docs/LIVE_AGENT_PILOT.md](docs/LIVE_AGENT_PILOT.md)):
  - The agents handled all 30 data-visible traps themselves.
  - HEP-Prov detected 12 of 12 mid-run input changes, including 2 statistically identical swaps that the agent missed.

Design: [DESIGN.md](DESIGN.md).

```bash
hepprov init --declare detector.gdml        # start a run; pre-existing files are recorded, not trusted
hepprov exec --label simulate -- python sim.py
hepprov exec --label analyze  -- python ana.py
hepprov check                               # hash chain + is every read a file this run produced?
hepprov verify report.md                    # every number in the report vs. this run's evidence
hepprov --root OLD_RUN verify OLD_RUN/report.md --archive   # retrospective, no manifest
```

In a script, `hepprov.emit("light_yield_1MeV", value)` declares a computed number (cooperative mode). Outside a recorded run it does nothing.

Every number in a report gets one verdict:

| Verdict | Meaning |
|---|---|
| verified | Equals a quantity from primitive evidence (per-event logs, event tables) or an emitted value, to every printed digit |
| copy | Found only in an intermediate results file |
| diagnosed | Reproduced only by a formula in a bounded grammar. The formula and its decoy-measured chance rate are attached. This is a screening verdict |
| unexplained | Nothing matches |

The verifier also:
- checks the numbers inside results files the same way (each excluding itself);
- promotes mean/std pairs from the same value family as *corroborated*, with a joint chance rate;
- runs the HEP invariants: batch-ratio suppression, and linearity for EM particles.

In attested mode, only files this run wrote or declared count as evidence.

`hepprov exec` records:
- the command, its exit code, and hashes of its stdout and stderr;
- every file it wrote, from a filesystem diff that works for any program, including Geant4;
- every file each Python process read, with its sha256 at read time.

It needs no change to the agent's code.

`hepprov check` flags reads of:
- leftovers from earlier runs;
- files changed outside the recorder;
- older versions of a file;
- content this run never produced.

It also flags overwrites between steps.

Known limits:
- Reads by non-Python binaries aren't captured yet (L3, Linux syscall tracing, is planned).
- Python started with `-I` or `-E` isn't instrumented. Such processes are reported as uninstrumented, never silently trusted.
