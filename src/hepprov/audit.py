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


CODE_SUFFIXES = (".py", ".sh", ".ipynb", ".r", ".jl", ".c", ".cc", ".cpp", ".mac",
                 ".md", ".rst", ".tex")   # code, and documents the agent writes itself (its report)


def _assign_severity(flags: list[dict[str, Any]], m: Manifest) -> None:
    """'defect' flags are evidence problems; 'info' flags are process facts worth a glance.

    info: a failed step whose script later ran successfully (the agent fixed
    its code), and an overwrite of a code file (the agent editing its own
    scripts). Overwrites of data files stay defects.
    """
    ok_scripts, authoring = set(), set()
    for ev in m.events():
        d = ev["data"]
        if ev["kind"] == "exec" and d.get("exit_code") == 0:
            ok_scripts.update(x for x in d.get("cmd", [])[1:] if x.endswith(".py"))
        if ev["kind"] == "exec" and (d.get("cmd") or [""])[0] == "<write_file>":
            authoring.add(d.get("label") or f"exec#{ev['seq']}")
    for f in flags:
        sev = "defect"
        if f["kind"] == "failed_step":
            label = f.get("step") or ""
            if any(label.endswith(sc) for sc in ok_scripts) or not label.endswith(".py"):
                sev = "info"   # fixed later, or the agent "ran" a non-script (e.g. a CSV)
        elif f["kind"] == "overwrite" and str(f.get("path", "")).lower().endswith(CODE_SUFFIXES):
            sev = "info"
        elif f["kind"] == "overwrite" and f.get("by") and all(b in authoring for b in f["by"]):
            sev = "info"     # an explicit, recorded write replaced the file (e.g. a declared data update)
        elif f["kind"] == "stale_claim" and f.get("marked_earlier"):
            sev = "info"     # "previously 0.91, now 0.67": an earlier value presented as such
        elif f["kind"] == "stale_claim" and f.get("presentational"):
            sev = "info"     # the revised script reproduces what it still prints: a layout change
        elif f["kind"] == "superseded_output" and not f.get("read_afterwards"):
            sev = "info"     # stale output nobody read again
        elif f["kind"] == "unexplained_claim" and f.get("approximate"):
            sev = "info"     # "about 360-430", "p ~ 0.97": not a precise claim
        elif f["kind"] == "leftover_from_before_run" and f.get("disclosed"):
            sev = "info"     # the agent opened a leftover file and the report names it (inspected and set aside)
        elif f["kind"] == "stale_claim" and f.get("low_precision"):
            sev = "info"     # 1-2 significant figures: an exact match to an old printout is chance-level
        elif f["kind"] == "placeholder_signature" and (f.get("null_rate") or 0) > WEAK_NULL:
            sev = "info"     # the padded-mean formula matches, but so would a random number this often
        elif f["kind"] == "claim_from_tainted_input" and f.get("disclosed"):
            sev = "info"     # the report says which flagged input the number comes from
        f.setdefault("severity", sev)


_EARLIER_WORDS = ("previous", "before the", "originally", "original ", "earlier", "old ", "first version",
                  "initial", "was ", "were ", "used to", "superseded", "replaced")


def _stale_of(key: str, stale: dict[str, str], producer: dict[str, str]) -> str | None:
    """Reason a support key comes from a superseded step, or None."""
    key = re.sub(r"^(?:x|/)(?:100|1000):", "", key)
    tier, _, rest = key.partition(":")
    if tier == "stdout":
        return stale.get(rest.rpartition(":")[0])
    if tier in ("json", "table"):
        lab = producer.get(rest.split(":", 1)[0])
        return stale.get(lab) if lab else None
    return None


_DISCLOSURE_PHRASES = ("first inspection", "first read", "first look", "first pass", "earlier version",
                       "previous version", "original version", "older version", "before the change",
                       "before it changed", "was modified", "was rewritten", "was replaced", "changed during",
                       "mid-run", "changed between", "rev 1", "revision 1", "first revision", "old selection",
                       "old cut", "before the revision", "before revision", "superseded")


def _paragraph(text: str, pos: int) -> str:
    start = text.rfind("\n\n", 0, pos)
    end = text.find("\n\n", pos)
    return text[start + 1 if start >= 0 else 0:end if end >= 0 else len(text)]


def _table_heading(text: str, pos: int) -> str:
    """For a position inside a markdown table: the text block just before the table (its heading)."""
    start = text.rfind("\n\n", 0, pos)
    block_start = start + 2 if start >= 0 else 0
    if not text[block_start:pos].lstrip().startswith("|"):
        return ""
    prev = text.rfind("\n\n", 0, max(0, start))
    return text[prev + 2 if prev >= 0 else 0:max(0, start)]


def _regen_status(key: str, stale: dict[str, str], regen_of: dict) -> str:
    """'changed' / 'unchanged' / 'inconclusive' / 'not_run' for a stale stdout support key."""
    key = re.sub(r"^(?:x|/)(?:100|1000):", "", key)
    tier, _, rest = key.partition(":")
    if tier != "stdout":
        return "not_run"
    label, _, idx = rest.rpartition(":")
    rg = regen_of.get(label)
    if rg is None:
        return "not_run"
    if rg.status != "ok" or not idx.isdigit() or int(idx) in rg.nondeterministic:
        return "inconclusive"
    return "changed" if int(idx) in rg.dependent else "unchanged"


_SIGNUM = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _sig_numbers(text: str) -> list[str]:
    """Printed numbers with at least 3 significant digits, normalized (trailing zeros dropped)."""
    out = []
    for t in _SIGNUM.findall(text):
        mant = re.split(r"[eE]", t)[0].lstrip("+-")
        if len(mant.replace(".", "").lstrip("0")) >= 3:
            try:
                out.append(repr(float(t)))
            except ValueError:
                pass
    return out


def _presentational_revisions(root: Path, m: Manifest, stale: dict[str, str]) -> set[str]:
    """Steps superseded by a script revision that left the computation unchanged.

    Compares the step's printed numbers with those of the script's LAST run:
    if at least 80% of the latter (3+ significant digits) already appeared in
    the old output, the revision added or dropped columns but did not change
    what it computes.
    """
    runs: dict[str, list[tuple[int, str]]] = {}
    for ev in m.events():
        if ev["kind"] == "exec":
            d = ev["data"]
            script = next((a for a in d.get("cmd", [])[1:] if a.endswith(".py")), None)
            if script:
                runs.setdefault(script, []).append((ev["seq"], d.get("label") or f"exec#{ev['seq']}"))

    def nums(seq: int) -> list[str] | None:
        log = root / ".hepprov" / "logs" / f"{seq:06d}" / "stdout.bin"
        return _sig_numbers(log.read_bytes().decode("utf-8", "replace")) if log.exists() else None

    out = set()
    for script, rs in runs.items():
        for seq, label in rs:
            if "was changed and re-run" not in stale.get(label, ""):
                continue
            old, new = nums(seq), nums(rs[-1][0])
            if old and new and sum(x in set(old) for x in new) >= 0.8 * len(new):
                out.add(label)
    return out


def _label_of(key: str, producer: dict[str, str]) -> str | None:
    key = re.sub(r"^(?:x|/)(?:100|1000):", "", key)
    tier, _, rest = key.partition(":")
    if tier == "stdout":
        return rest.rpartition(":")[0]
    if tier in ("json", "table"):
        return producer.get(rest.split(":", 1)[0])
    return None


_TEXT_SUFFIXES = (".md", ".txt", ".csv", ".tsv", ".json", ".dat", ".log", ".tex", ".rst")
_ARCHIVE = re.compile(r"(?i)(?:^|[\/_.-])(?:v\d+|old|archive\w*|supersed\w*|prev\w*|backup|bak|rev\d+)(?:$|[\/_.-])")


def _current_text(root: Path, files: list[Path], report, stale: dict[str, str],
                  producer: dict[str, str]) -> str:
    """Text of the run's current, attested files, minus the report, archives, and outputs of stale steps.

    A report number found here is part of the run's current state, even if
    the verifier's parsers do not read the file (e.g. a markdown table).
    """
    rep = Path(report).resolve() if report else None
    parts = []
    for p in files:
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            rel = p.name
        if (p.suffix.lower() not in _TEXT_SUFFIXES or (rep and p.resolve() == rep) or _ARCHIVE.search(rel)
                or p.name not in producer or stale.get(producer[p.name], "")):
            continue
        try:
            if p.stat().st_size <= 2_000_000:
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return "\n".join(parts)


def _in_text(num: str, text: str) -> bool:
    num = num.strip().lstrip("+")
    return bool(num) and re.search(r"(?<![\d.])" + re.escape(num) + r"(?![\d])", text) is not None


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
        flags.append(_flag("attest", "overwrite", " -> ".join(steps), path=rel, by=steps[1:]))

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

    # steps whose results a later revision superseded, and who last wrote each file
    from hepprov.superseded import stale_steps
    stale = stale_steps(m)
    presentational = _presentational_revisions(root, m, stale)
    producer: dict[str, str] = {}
    for ev in m.events():
        if ev["kind"] == "exec":
            for rel in ev["data"].get("writes", {}):
                producer[Path(rel).name] = ev["data"].get("label") or f"exec#{ev['seq']}"

    regen_of: dict = {}
    if counterfactual and stale:
        from hepprov.counterfactual import regenerate_step
        for ev in m.events():
            if ev["kind"] != "exec":
                continue
            d = ev["data"]
            label = d.get("label") or f"exec#{ev['seq']}"
            if label not in stale:
                continue
            script = next((a for a in d.get("cmd", [])[1:] if a.endswith(".py")), None)
            log = root / ".hepprov" / "logs" / f"{ev['seq']:06d}" / "stdout.bin"
            if script and log.exists() and (root / script).exists():
                regen_of[label] = regenerate_step(root, script, log.read_bytes().decode("utf-8", "replace"), label)

    # evidence-based verification of the report (attested evidence only)
    current_files = attested_files(m)
    pool = pool_from_files(current_files)
    current_text = _current_text(root, current_files, report, stale, producer)
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
            # stale claims: the number comes only from results that a later revision superseded
            if v["verdict"] in ("verified", "copy") and v["support"] and stale:
                reasons = [_stale_of(k, stale, producer) for k in v["support"]]
                regen = [_regen_status(k, stale, regen_of) for k in v["support"]]
                # a unit/percent conversion multiplies the ways to match by chance ("156 mm" vs 0.156 x 1000)
                weak_conv = v["sig"] < 4 and all(re.match(r"(?:x|/)(?:100|1000):", k) for k in v["support"])
                in_current = _in_text(v["text"], current_text)
                if all(reasons) and "unchanged" not in regen and not weak_conv and not in_current:
                    pos = v.get("pos", 0)
                    before = report_text[max(0, pos - 140):pos].lower().replace("-", " ")
                    para = _paragraph(report_text, pos).lower().replace("-", " ")
                    heading = _table_heading(report_text, pos).lower().replace("-", " ")
                    marked = (any(w in before for w in _EARLIER_WORDS)
                              or any(w in para for w in _DISCLOSURE_PHRASES)
                              or any(w in heading for w in _EARLIER_WORDS + _DISCLOSURE_PHRASES))
                    note = {"changed": "; regenerating the step on the current files changes this number",
                            "inconclusive": "; regeneration inconclusive"}.get(regen[0], "")
                    pres = all(_label_of(k, producer) in presentational for k in v["support"])
                    flags.append(_flag("verify", "stale_claim",
                                       f"{v['text']} rests only on {v['support'][0]}, from a step whose results were "
                                       f"superseded ({reasons[0]}){note}"
                                       + ("; the report marks it as an earlier value" if marked else "")
                                       + ("; the revised script reproduces the values it still prints"
                                          if pres else ""),
                                       claim=v["id"], marked_earlier=marked, regenerated=regen[0],
                                       low_precision=v["sig"] < 3,
                                       presentational=pres))
        for scope in (ver, ver.get("intermediates") or {}):
            for v in scope.get("verdicts", []):
                if v["verdict"] == "diagnosed" and all("+[1.0]" in f for f in v["support"]):
                    flags.append(_flag("verify", "placeholder_signature",
                                       f"{v['text']} = {v['formula']} (null {v['null_rate']:.0%})", claim=v["id"],
                                       null_rate=v["null_rate"]))
    _assign_severity(flags, m)
    defects = [f for f in flags if f.get("severity") == "defect"]
    return {"root": str(root), "flags": flags, "n_flags": len(flags), "n_defect_flags": len(defects),
            "verify_counts": ver["counts"] if ver else None,
            "counterfactual": {k: {"status": c.status, "reason": c.reason, "n_values": c.n_values,
                                   "dependent": len(c.dependent), "nondeterministic": len(c.nondeterministic)}
                               for k, c in cf_results.items()}}
