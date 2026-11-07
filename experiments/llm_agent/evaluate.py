"""Evaluate an LLM-agent run directory: HEP-Prov flags next to ground truth, for manual adjudication.

    python experiments/llm_agent/evaluate.py OUT_DIR

Prints, per task: outcome, cost, every HEP-Prov flag, the verifier's claim
counts, the ground truth and the report text. Writes OUT_DIR/evaluation.json.
Adjudicating whether each flag is a real defect, and whether any defect went
unflagged, is done by a human reading the report, the transcript and the
truth; this script only lays the evidence side by side.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from hepprov.audit import audit_run  # noqa: E402
sys.stdout.reconfigure(encoding="utf-8")

out = Path(sys.argv[1])
rows = []
for tdir in sorted(p for p in out.iterdir() if p.is_dir()):
    meta = json.loads((tdir / "meta.json").read_text(encoding="utf-8")) if (tdir / "meta.json").exists() else {}
    truth = json.loads((tdir / "truth.json").read_text(encoding="utf-8"))
    run = tdir / "run"
    report = run / "report.md"
    res = audit_run(run, report if report.exists() else None, counterfactual=True)
    rows.append({"task": truth["id"], "meta": meta, "truth": truth, "flags": res["flags"],
                 "verify_counts": res["verify_counts"]})
    print("=" * 100)
    print(f"{truth['id']}  outcome={meta.get('outcome')}  turns={meta.get('turns')}  ~${meta.get('cost_usd_estimate')}")
    print(f"truth: {json.dumps(truth['truth'])[:300]}")
    if truth.get("traps"):
        print(f"traps: {truth['traps']}")
    print(f"verify: {res['verify_counts']}   flags: {len(res['flags'])} (defect {res.get('n_defect_flags')})")
    for k, c in res.get("counterfactual", {}).items():
        print(f"  counterfactual {k}: {c}")
    for f in res["flags"]:
        where = f.get("path") or f.get("step") or f.get("sweep") or f.get("claim") or ""
        print(f"  [{f.get('severity', '?')}:{f['detector']}:{f['kind']}] {where}  {f['detail'][:140]}")
    if report.exists():
        print("--- report.md ---")
        print(report.read_text(encoding="utf-8", errors="replace")[:3500])
(out / "evaluation.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
