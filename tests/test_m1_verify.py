"""Milestone 1: the verifier.

Synthetic runs exercise each verdict. The corpus test pins the GRACE artifact
audit's asserted findings, rediscovered from primitive evidence.
"""

import json
import os
import sys
from pathlib import Path

import pytest

from hepprov.manifest import Manifest
from hepprov.plugins import hep
from hepprov.recorder import Recorder
from hepprov.verify import (Pool, attested_files, emitted_values, extract_claims, pool_from_files,
                            verify_report)

PY = sys.executable
CORPUS = Path(os.environ.get("GRACE_CORPUS", r"C:\Users\lyuho\Downloads\GRACE_v2\GRACE_whitepaper_data"))


def _log(n_hits):
    return "".join(f"Event {i}: {h} hits detected\n" for i, h in enumerate(n_hits))


def verdict_of(res, text):
    return {v["text"]: v["verdict"] for v in res["verdicts"]}[text]


def test_claim_extraction_skips_years_ids_and_trivial():
    cs = extract_claims("In 2024 (arXiv 1802.07198) we got 1479.23 PE/MeV, 1.0 and 5,000 events.")
    assert [c.text for c in cs] == ["1479.23", "5000"]


def test_attested_run_verdicts(tmp_path):
    root = tmp_path / "run"
    (root / "energy_0.001GeV").mkdir(parents=True)
    rec = Recorder(root)
    rec.init()
    hits = [1470, 1480, 1490, 1476]          # mean 1479.0
    rec.exec([PY, "-c", f"open('energy_0.001GeV/det_electron_geant4.log','w').write({_log(hits)!r})"],
             label="simulate", echo=False)
    code = (
        "import json, hepprov, re\n"
        "h=[int(x) for x in re.findall(r'(\\d+) hits detected', open('energy_0.001GeV/det_electron_geant4.log').read())]\n"
        "m=sum(h)/len(h); hepprov.emit('mean_hits', m)\n"
        "json.dump({'baseline': m, 'improvement_pct': 12.345, 'optimized': m*(1+12.345/100)}, open('ana.json','w'))\n"
    )
    rec.exec([PY, "-c", code], label="analyze", echo=False)
    report = ("Mean of 1479.0 hits at 1 MeV. Improvement 12.345 percent, optimized 1661.58. "
              "A spurious 987.65 also appears.")
    m = Manifest(root)
    pool = pool_from_files(attested_files(m))
    pool.add(emitted_values(m))
    res = verify_report(report, pool)
    assert verdict_of(res, "1479.0") == "verified"       # log tier (and emit)
    assert verdict_of(res, "12.345") == "copy"            # only in an intermediate
    assert verdict_of(res, "1661.58") == "copy"           # stored in ana.json
    assert verdict_of(res, "987.65") == "unexplained"
    # the intermediate itself is derived by percent rescaling, at a low chance rate
    iv = {v["id"]: v for v in res["intermediates"]["verdicts"]}
    opt = iv["J:ana.json:optimized"]
    assert opt["verdict"] == "diagnosed" and "(1 +" in opt["formula"] and opt["null_rate"] <= 0.05


def test_stale_file_is_not_evidence_in_attested_mode(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "old_results.json").write_text(json.dumps({"light_yield": 7430.8254}))
    rec = Recorder(root)
    rec.init()
    rec.exec([PY, "-c", "open('x.txt','w').write('1')"], echo=False)
    report = "Light yield 7430.8254 PE."
    attested = verify_report(report, pool_from_files(attested_files(Manifest(root))), intermediates=False)
    archive = verify_report(report, pool_from_files([root / "old_results.json"]), intermediates=False)
    assert verdict_of(attested, "7430.8254") == "unexplained"
    assert verdict_of(archive, "7430.8254") == "copy"


def test_placeholder_mean_is_corroborated():
    """Audit: mean(0.3964, 0.4978, 1.0)=0.6314, std=0.2639, a placeholder padded into a family."""
    pool = Pool()
    pool.add({"log:a": 123.0}, {"log:det:proton/electron_ratio": [0.3964, 0.4978]})
    res = verify_report("Quenching 0.6314 ± 0.2639.", pool, intermediates=False)
    assert res["corroborated"] and res["corroborated"][0]["joint_null"] <= 0.02
    assert "+[1.0]" in res["corroborated"][0]["family_variant"]


def test_batch_suppression_invariant(tmp_path):
    files = []
    for e, hits, batched in ((0.001, [1479] * 5, False), (0.005, [3495] * 5, True)):
        d = tmp_path / f"energy_{e:.3f}GeV" / ("batch_000" if batched else "")
        d.mkdir(parents=True, exist_ok=True)
        f = d / "det_electron_geant4.log"
        f.write_text(_log(hits))
        files.append(f)
    inv = hep.invariants(hep.log_quantities(files), files)
    assert [(i["code"], i["ratio"]) for i in inv] == [("I3", 0.473)]


@pytest.mark.skipif(not CORPUS.exists(), reason="GRACE corpus not available")
def test_rediscovers_audit_findings_on_grace_darkside_v9():
    run = next((CORPUS / "opticks_run_files_v9").glob("darkside_lar_*"))
    files = [p for p in run.rglob("*") if p.is_file()]
    res = verify_report((run / "academic_report.md").read_text(encoding="utf-8"), pool_from_files(files))
    iv = {v["text"]: v for v in res["intermediates"]["verdicts"]}
    # 4491.29 = 1474.39 * (1 + 204.62/100): retyped constant rescaled by a retyped percentage
    d = iv["4491.286818"]
    assert d["verdict"] == "diagnosed" and "(1 +" in d["formula"] and d["null_rate"] == 0.0
    # quenching 0.6314 +- 0.2639: two log-derived ratios plus a placeholder 1.0
    corr = res["intermediates"]["corroborated"]
    assert any(c["mean_value"] == "0.6314" and c["std_value"] == "0.2639" and c["joint_null"] == 0.0
               and "+[1.0]" in c["family_variant"] for c in corr)
    # batching suppression at 5 MeV
    ratios = sorted((i["sweep"], i["ratio"]) for i in res["invariants"] if i["code"] == "I3")
    assert [r for _, r in ratios] == [0.47, 0.221, 0.473] or [round(r, 2) for _, r in ratios] == [0.47, 0.22, 0.47]
