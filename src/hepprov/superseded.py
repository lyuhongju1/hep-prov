"""Outputs produced by a superseded version of their script.

A natural long-pipeline mistake: the agent fixes a script, re-runs it for
some configurations, and a later step reads an output the OLD code wrote,
which was never regenerated. Every file involved is legitimately written by
the run, so read attestation does not fire; the manifest still shows it.

For every output file this records the script (path and content sha) of the
step that last wrote it. A file is *superseded* when that script was later
rewritten and executed again at its new version, and the file was not
rewritten afterwards. A superseded file read by a later step is a defect
signal; one that is never read again is informational.
"""

from __future__ import annotations

from typing import Any

from hepprov.manifest import Manifest


def superseded_outputs(m: Manifest) -> list[dict[str, Any]]:
    script_sha: dict[str, str] = {}          # script path -> current content sha
    producer: dict[str, tuple[str, str, str, int]] = {}   # file -> (script, sha, label, seq)
    reruns: dict[str, list[tuple[int, str]]] = {}         # script -> [(seq, sha)] of executions
    reads_after: dict[str, list[tuple[int, str]]] = {}    # file -> [(seq, label)] reads

    for ev in m.events():
        kind, d, seq = ev["kind"], ev["data"], ev["seq"]
        if kind == "declare" and not d.get("external"):
            script_sha.setdefault(d["path"], d["sha256"])
            continue
        if kind == "untracked_change":
            for rel, sha in d.get("changed", {}).items():
                if sha:
                    script_sha[rel] = sha
            continue
        if kind != "exec":
            continue
        label = d.get("label") or f"exec#{seq}"
        for r in d.get("reads", []):
            reads_after.setdefault(r["path"], []).append((seq, label))
        script = next((a for a in d.get("cmd", [])[1:] if a.endswith(".py")), None)
        sha = script_sha.get(script) if script else None
        if script and sha:
            reruns.setdefault(script, []).append((seq, sha))
        for rel, wsha in d.get("writes", {}).items():
            if not wsha:
                continue
            if rel.endswith(".py"):
                script_sha[rel] = wsha            # a script (re)authored
                continue
            if script and sha:
                producer[rel] = (script, sha, label, seq)
            else:
                producer.pop(rel, None)           # written by a non-script step (e.g. the agent directly)

    out = []
    for rel, (script, sha, label, seq) in producer.items():
        later = [(s, h) for s, h in reruns.get(script, []) if s > seq and h != sha]
        if not later:
            continue
        first_new = later[0][0]
        stale_reads = [lab for s, lab in reads_after.get(rel, []) if s > first_new]
        out.append({"path": rel, "script": script, "produced_by": label, "produced_at_seq": seq,
                    "script_rerun_at_seq": first_new, "read_afterwards_by": stale_reads[:5]})
    return out


def stale_steps(m: Manifest) -> dict[str, str]:
    """Steps whose results no longer reflect the run's current state: label -> reason.

    A step is stale if (a) its script was rewritten and re-run later at the
    new version, or (b) a file it read was rewritten later (by any step, a
    declared data update included) and the same invocation was not re-run
    after that. Results printed or written by a stale step are what a
    revised report must not keep presenting as current.
    """
    execs = []                       # (seq, label, invocation, script, script_sha, reads)
    writes: list[tuple[int, str, str]] = []   # (seq, path, sha)
    script_sha: dict[str, str] = {}
    for ev in m.events():
        kind, d, seq = ev["kind"], ev["data"], ev["seq"]
        if kind == "declare" and not d.get("external"):
            script_sha.setdefault(d["path"], d["sha256"])
        elif kind == "untracked_change":
            for rel, sha in d.get("changed", {}).items():
                if sha:
                    writes.append((seq, rel, sha))
                    script_sha[rel] = sha
        elif kind == "exec":
            label = d.get("label") or f"exec#{seq}"
            inv = tuple(d.get("cmd", [])[1:])
            script = next((a for a in inv if a.endswith(".py")), None)
            reads = [(r["path"], r.get("sha256")) for r in d.get("reads", [])]
            execs.append((seq, label, inv, script, script_sha.get(script) if script else None, reads))
            for rel, sha in d.get("writes", {}).items():
                if sha:
                    writes.append((seq, rel, sha))
                    script_sha[rel] = sha
    out: dict[str, str] = {}
    for seq, label, inv, script, ssha, reads in execs:
        if not script:
            continue
        later = [e for e in execs if e[0] > seq]
        if ssha and any(e[3] == script and e[4] and e[4] != ssha for e in later):
            out[label] = f"{script} was changed and re-run later"
            continue
        for path, rsha in reads:
            upd = [s for s, p, h in writes if p == path and s > seq and h != rsha]
            if upd and not any(e[2] == inv and e[0] > upd[0] for e in later):
                out[label] = f"input {path} was replaced later (step {upd[0]}) and this step was not re-run"
                break
    return out
