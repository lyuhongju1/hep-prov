"""One call that runs every detector on a recorded run and returns flags.

A flag is something a reviewer should look at. Detectors and their flags:

  chain        chain_broken
  attest       leftover_from_before_run, written_outside_recorder, stale_version,
               not_written_by_run, unhashable, overwrite
  outputs      truncated_output (a written .json that does not parse),
               failed_step (non-zero exit code)
  invariants   I3 batch suppression, I4 nonlinearity (HEP plugin)
  verify       unexplained_claim (a report number nothing supports),
               placeholder_signature (a value explained only with a padded 1.0)
  static       transcribed_literal, silent_skip

Ordinary "diagnosed" matches are explanations, not flags: a correctly
computed derived value is also reproduced by a formula. Only the placeholder
variants of the grammar are defect signatures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hepprov.attest import attest
from hepprov.manifest import Manifest
from hepprov.static import executed_scripts, silent_skips, transcribed_literals
from hepprov.verify import attested_files, emitted_values, pool_from_files, verify_report


def _flag(detector: str, kind: str, detail: str, **kw) -> dict[str, Any]:
    return {"detector": detector, "kind": kind, "detail": detail, **kw}


def audit_run(root: str | Path, report: str | Path | None = None, min_claim_sig: int = 3) -> dict[str, Any]:
    root = Path(root).resolve()
    m = Manifest(root)
    flags: list[dict[str, Any]] = []

    for e in m.verify_chain():
        flags.append(_flag("chain", "chain_broken", f"line {e.line}: {e.reason}"))

    at = attest(m)
    for v in at["violations"]:
        flags.append(_flag("attest", v["kind"], v["detail"], path=v["path"], step=v["label"]))
    for rel, steps in at["overwrites"].items():
        flags.append(_flag("attest", "overwrite", " -> ".join(steps), path=rel))

    # Taint propagation: a step that read a violating input, or a tainted file,
    # taints everything it wrote. Numbers supported only by tainted files are
    # flagged below. A bad input then cannot reach the report as a "copy" of a
    # file the run legitimately wrote.
    bad_reads: dict[int, list[str]] = {}
    for v in at["violations"]:
        bad_reads.setdefault(v["exec_seq"], []).append(v["path"])
    tainted: dict[str, str] = {}
    for ev in m.events():
        if ev["kind"] != "exec":
            continue
        d = ev["data"]
        reasons = [f"read flagged input {p}" for p in bad_reads.get(ev["seq"], [])]
        reasons += [f"read tainted {r['path']}" for r in d.get("reads", []) if r["path"] in tainted]
        if reasons:
            for rel in d.get("writes", {}):
                tainted.setdefault(rel, f"{d.get('label')}: {reasons[0]}")

    # per-step outputs and prior quantities (for transcription checks)
    prior_by_seq: dict[int, dict[str, float]] = {}
    produced: dict[str, float] = {}
    for ev in m.events():
        if ev["kind"] != "exec":
            continue
        d = ev["data"]
        prior_by_seq[ev["seq"]] = dict(produced)
        if d.get("exit_code"):
            flags.append(_flag("outputs", "failed_step", f"exit code {d['exit_code']}", step=d.get("label")))
        for rel, sha in d.get("writes", {}).items():
            p = root / rel
            if p.suffix == ".json" and sha:
                try:
                    obj = json.loads(p.read_text(encoding="utf-8"))
                except ValueError:
                    flags.append(_flag("outputs", "truncated_output", "written JSON does not parse",
                                       path=rel, step=d.get("label")))
                    continue
                except OSError:
                    continue
                from hepprov.verify import json_leaves
                produced.update(json_leaves(p, f"json@{d.get('label')}")[0])
        for e in d.get("emits", []):
            produced[f"emit:{d.get('label')}:{e['name']}"] = float(e["value"])

    # static checks on executed scripts
    for seq, label, script in executed_scripts(m):
        try:
            code = script.read_text(encoding="utf-8")
        except OSError:
            continue
        for s in silent_skips(code):
            flags.append(_flag("static", "silent_skip", s["test"], step=label, line=s["line"], path=script.name))
        for s in transcribed_literals(code, prior_by_seq.get(seq, {})):
            flags.append(_flag("static", "transcribed_literal", f"{s['literal']} = {s['equals']}",
                               step=label, line=s["line"], path=script.name))

    # evidence-based verification of the report (attested evidence only)
    pool = pool_from_files(attested_files(m))
    pool.add(emitted_values(m))
    for inv in getattr(pool, "invariants", []):
        flags.append(_flag("invariants", inv["code"], inv["detail"], sweep=inv["sweep"]))
    ver = None
    if report is not None and Path(report).exists():
        ver = verify_report(Path(report).read_text(encoding="utf-8", errors="ignore"), pool)
        tainted_names = {Path(r).name: why for r, why in tainted.items()}
        for v in ver["verdicts"]:
            if v["verdict"] == "unexplained" and v["sig"] >= min_claim_sig:
                flags.append(_flag("verify", "unexplained_claim", f"{v['text']}: ...{v['ctx']}..."))
            elif v["verdict"] in ("verified", "copy") and v["support"]:
                srcs = [s.split(":")[1] if s.startswith("json:") else None for s in v["support"]]
                if all(s is not None and s in tainted_names for s in srcs):
                    flags.append(_flag("verify", "claim_from_tainted_input",
                                       f"{v['text']} rests only on {srcs[0]} ({tainted_names[srcs[0]]})",
                                       claim=v["id"]))
        for scope in (ver, ver.get("intermediates") or {}):
            for v in scope.get("verdicts", []):
                if v["verdict"] == "diagnosed" and all("+[1.0]" in f for f in v["support"]):
                    flags.append(_flag("verify", "placeholder_signature",
                                       f"{v['text']} = {v['formula']} (null {v['null_rate']:.0%})", claim=v["id"]))
    return {"root": str(root), "flags": flags, "n_flags": len(flags),
            "verify_counts": ver["counts"] if ver else None}
