"""Printed output as evidence, and taint through printed output."""

import sys

from hepprov.audit import audit_run
from hepprov.recorder import Recorder

PY = sys.executable


def _setup(tmp_path, stale: bool):
    root = tmp_path / "run"
    root.mkdir()
    (root / "base.csv").write_text("npe\n" + "1250\n" * 300)
    (root / "opt.csv").write_text("npe\n" + "1525\n" * 300)
    if stale:
        (root / "opt_old.csv").write_text("npe\n" + "1812\n" * 300)
    rec = Recorder(root)
    rec.init(declare=["base.csv", "opt.csv"])
    src = "opt_old.csv" if stale else "opt.csv"
    rec.write_file("ana.py", "import pandas as pd\n"
                   "b = pd.read_csv('base.csv').npe.mean(); o = pd.read_csv(%r).npe.mean()\n"
                   "print('improvement_pct', round((o / b - 1) * 100, 3))\n" % src)
    rec.exec([PY, "ana.py"], label="run ana", echo=False)
    rec.write_file("report.md", "The optimized geometry improves light yield by %s percent.\n"
                   % ("44.96" if stale else "22.0"))
    return root


def test_printed_result_supports_a_report_number(tmp_path):
    root = _setup(tmp_path, stale=False)
    res = audit_run(root, root / "report.md")
    assert res["flags"] == [], res["flags"]
    assert res["verify_counts"].get("copy", 0) >= 1


def test_printed_result_from_a_stale_input_is_tainted(tmp_path):
    root = _setup(tmp_path, stale=True)
    kinds = {f["kind"] for f in audit_run(root, root / "report.md")["flags"]}
    assert "leftover_from_before_run" in kinds
    assert "claim_from_tainted_input" in kinds
