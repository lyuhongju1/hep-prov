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
