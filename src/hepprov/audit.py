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
import re
from pathlib import Path
from typing import Any

from hepprov.attest import attest
from hepprov.manifest import Manifest
from hepprov.static import executed_scripts, silent_skips, transcribed_literals
from hepprov.verify import (attested_files, code_literals, emitted_values, pool_from_files, stdout_values,
                             verify_report)


WEAK_NULL = 0.05   # a report-level formula match above this chance rate does not explain a number


_APPROX = ("about", "approximately", "approx", "roughly", "around", "~", "\u2248", "of order", "nearly")


def _is_approximate(text: str, pos: int) -> bool:
    """The number is stated as approximate: a marker within the 24 characters before it (ranges included)."""
    if pos < 0 or not text:
        return False
    before = text[max(0, pos - 24):pos].lower()
    return any(m in before for m in _APPROX)


CODE_SUFFIXES = (".py", ".sh", ".ipynb", ".r", ".jl", ".c", ".cc", ".cpp", ".mac")


def _assign_severity(flags: list[dict[str, Any]], m: Manifest) -> None:
    """'defect' flags are evidence problems; 'info' flags are process facts worth a glance.

    info: a failed step whose script later ran successfully (the agent fixed
    its code), and an overwrite of a code file (the agent editing its own
    scripts). Overwrites of data files stay defects.
    """
    ok_scripts = set()
    for ev in m.events():
        d = ev["data"]
        if ev["kind"] == "exec" and d.get("exit_code") == 0:
            ok_scripts.update(x for x in d.get("cmd", [])[1:] if x.endswith(".py"))
    for f in flags:
        sev = "defect"
        if f["kind"] == "failed_step":
            label = f.get("step") or ""
            if any(label.endswith(sc) for sc in ok_scripts) or not label.endswith(".py"):
                sev = "info"   # fixed later, or the agent "ran" a non-script (e.g. a CSV)
        elif f["kind"] == "overwrite" and str(f.get("path", "")).lower().endswith(CODE_SUFFIXES):
            sev = "info"
        elif f["kind"] == "superseded_output" and not f.get("read_afterwards"):
            sev = "info"     # stale output nobody read again
        elif f["kind"] == "unexplained_claim" and f.get("approximate"):
            sev = "info"     # "about 360-430", "p ~ 0.97": not a precise claim
        elif f["kind"] == "claim_from_tainted_input" and f.get("disclosed"):
            sev = "info"     # the report says which flagged input the number comes from
        f.setdefault("severity", sev)


def _flag(detector: str, kind: str, detail: str, **kw) -> dict[str, Any]:
    return {"detector": detector, "kind": kind, "detail": detail, **kw}


def audit_run(root: str | Path, report: str | Path | None = None, min_claim_sig: int = 3,
              counterfactual: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    m = Manifest(root)
    flags: list[dict[str, Any]] = []

    for e in m.verify_chain():
        flags.append(_flag("chain", "chain_broken", f"line {e.line}: {e.reason}"))

    at = attest(m)
    report_text = (Path(report).read_text(encoding="utf-8", errors="ignore")
                   if report is not None and Path(report).exists() else "")
    for v in at["violations"]:
        disclosed = bool(report_text) and v["path"] in report_text
        flags.append(_flag("attest", v["kind"],
                           v["detail"] + ("; the report names this file (disclosed)" if disclosed else ""),
                           path=v["path"], step=v["label"], disclosed=disclosed))
    for rel, steps in at["overwrites"].items():
        flags.append(_flag("attest", "overwrite", " -> ".join(steps), path=rel))

    # Taint propagation: a step that read a violating input, or a tainted file,
    # taints everything it wrote. Numbers supported only by tainted files are
    # flagged below. A bad input then cannot reach the report as a "copy" of a
    # file the run legitimately wrote.
    bad_reads: dict[int, list[str]] = {}
    for v in at["violations"]:
        bad_reads.setdefault(v["exec_seq"], []).append(v["path"])
    bad_paths = sorted({v["path"] for v in at["violations"]})
    tainted: dict[str, str] = {}        # relpath -> why
    tainted_steps: dict[str, str] = {}  # step label -> why (its printed output is tainted too)
    for ev in m.events():
        if ev["kind"] != "exec":
            continue
        d = ev["data"]
        reasons = [f"read flagged input {p}" for p in bad_reads.get(ev["seq"], [])]
        reasons += [f"read tainted {r['path']}" for r in d.get("reads", []) if r["path"] in tainted]
        if reasons:
            label = d.get("label") or f"exec#{ev['seq']}"
            tainted_steps.setdefault(label, reasons[0])
            for rel in d.get("writes", {}):
                tainted.setdefault(rel, f"{label}: {reasons[0]}")

    # Counterfactual re-execution of steps that read a flagged input directly
    cf_results: dict = {}
    if counterfactual:
        from hepprov.counterfactual import rerun_step
        for ev in m.events():
            if ev["kind"] != "exec" or ev["seq"] not in bad_reads:
                continue
            d = ev["data"]
            label = d.get("label") or f"exec#{ev['seq']}"
            script = next((a for a in d.get("cmd", [])[1:] if a.endswith(".py")), None)
            log = root / ".hepprov" / "logs" / f"{ev['seq']:06d}" / "stdout.bin"
            if not script or not log.exists():
                continue
            cf_results[label] = rerun_step(root, script, log.read_bytes().decode("utf-8", "replace"),
                                           sorted(set(bad_reads[ev["seq"]])), label)

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

    # outputs produced by a superseded version of their script (natural long-pipeline mistake)
    from hepprov.superseded import superseded_outputs
    for so in superseded_outputs(m):
        read = so["read_afterwards_by"]
        flags.append(_flag("provenance", "superseded_output",
                           f"written by {so['produced_by']} with an older version of {so['script']}, which was "
                           f"changed and re-run later without regenerating this file"
                           + (f"; read afterwards by {', '.join(read)}" if read else ""),
                           path=so["path"], step=so["produced_by"], read_afterwards=bool(read)))

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
    pool.add(stdout_values(m))
    pool.add(code_literals(m))
    for inv in getattr(pool, "invariants", []):
        flags.append(_flag("invariants", inv["code"], inv["detail"], sweep=inv["sweep"]))
    ver = None
    if report is not None and Path(report).exists():
        ver = verify_report(Path(report).read_text(encoding="utf-8", errors="ignore"), pool)
        tainted_names = {Path(r).name: why for r, why in tainted.items()}

        def taint_of(key: str) -> str | None:
            """Why a support key is tainted, or None. Handles json/table files and printed output."""
            key = re.sub(r"^(?:x|/)(?:100|1000):", "", key)
            tier, _, rest = key.partition(":")
            if tier in ("json", "table"):
                return tainted_names.get(rest.split(":", 1)[0])
            if tier == "stdout":
                label, _, idx = rest.rpartition(":")
                why = tainted_steps.get(label)
                cf = cf_results.get(label)
                if why and cf is not None and cf.status == "ok" and idx.isdigit():
                    i = int(idx)
                    if i not in cf.dependent and i not in cf.nondeterministic:
                        return None   # re-execution with the flagged input perturbed left this value unchanged
                    return why + (" (counterfactual: depends on it)" if i in cf.dependent
                                  else " (counterfactual: nondeterministic, inconclusive)")
                return why
            return None

        for v in ver["verdicts"]:
            weak = v["verdict"] == "diagnosed" and (v["null_rate"] is None or v["null_rate"] > WEAK_NULL)
            if (v["verdict"] == "unexplained" or weak) and v["sig"] >= min_claim_sig:
                note = f" (only a chance-level formula match, null {v['null_rate']:.0%})" if weak else ""
                approx = _is_approximate(report_text, v.get("pos", -1))
                flags.append(_flag("verify", "unexplained_claim",
                                   f"{v['text']}{note}{' (stated as approximate)' if approx else ''}: ...{v['ctx']}...",
                                   approximate=approx))
            elif v["verdict"] in ("verified", "copy") and v["support"]:
                whys = [taint_of(s) for s in v["support"]]
                if all(whys):
                    near = report_text[max(0, v.get("pos", 0) - 400):v.get("pos", 0) + 400]
                    disclosed = any(p in near for p in bad_paths)
                    flags.append(_flag("verify", "claim_from_tainted_input",
                                       f"{v['text']} rests only on {v['support'][0]} ({whys[0]})"
                                       + ("; its sentence names the flagged file (disclosed)" if disclosed else ""),
                                       claim=v["id"], disclosed=disclosed))
        for scope in (ver, ver.get("intermediates") or {}):
            for v in scope.get("verdicts", []):
                if v["verdict"] == "diagnosed" and all("+[1.0]" in f for f in v["support"]):
                    flags.append(_flag("verify", "placeholder_signature",
                                       f"{v['text']} = {v['formula']} (null {v['null_rate']:.0%})", claim=v["id"]))
    _assign_severity(flags, m)
    defects = [f for f in flags if f.get("severity") == "defect"]
    return {"root": str(root), "flags": flags, "n_flags": len(flags), "n_defect_flags": len(defects),
            "verify_counts": ver["counts"] if ver else None,
            "counterfactual": {k: {"status": c.status, "reason": c.reason, "n_values": c.n_values,
                                   "dependent": len(c.dependent), "nondeterministic": len(c.nondeterministic)}
                               for k, c in cf_results.items()}}
