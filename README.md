# HEP-Prov

Provenance recorder and verifier for AI agents doing physics. **Status: milestone 0 (recorder and read attestation). The verifier is not built yet.** Design: [DESIGN.md](DESIGN.md).

```bash
hepprov init --declare detector.gdml        # start a run; pre-existing files are recorded, not trusted
hepprov exec --label simulate -- python sim.py
hepprov exec --label analyze  -- python ana.py
hepprov check                               # hash chain + is every read a file this run produced?
```

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
