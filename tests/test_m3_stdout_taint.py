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


def _inspect_and_reject(tmp_path, cite_stale: bool):
    """A careful agent: reads the stale file to compare, uses the production file."""
    root = tmp_path / "run"
    root.mkdir()
    (root / "base.csv").write_text("event,npe\n" + "".join(f"{i},{1250 + i % 5}\n" for i in range(300)))
    (root / "opt.csv").write_text("event,npe\n" + "".join(f"{i},{1525 + i % 5}\n" for i in range(300)))
    (root / "opt_old.csv").write_text("event,npe\n" + "".join(f"{i},{1812 + i % 5}\n" for i in range(300)))
    rec = Recorder(root)
    rec.init(declare=["base.csv", "opt.csv"])
    rec.write_file("ana.py", "import pandas as pd\n"
                   "b = pd.read_csv('base.csv').npe.mean(); o = pd.read_csv('opt.csv').npe.mean()\n"
                   "s = pd.read_csv('opt_old.csv').npe.mean()\n"
                   "print('improvement_pct', round((o / b - 1) * 100, 3))\n"
                   "print('stale_improvement_pct', round((s / b - 1) * 100, 3))\n")
    rec.exec([PY, "ana.py"], label="run ana", echo=False)
    b, o, s = 1252.0, 1527.0, 1814.0   # column means (offsets i % 5 average to 2)
    num = f"{round((s / b - 1) * 100, 3):.3f}" if cite_stale else f"{round((o / b - 1) * 100, 3):.3f}"
    rec.write_file("report.md", f"Improvement {num} percent. opt_old.csv was inspected and not used.\n")
    return root


def test_counterfactual_clears_numbers_that_do_not_depend_on_the_flagged_file(tmp_path):
    root = _inspect_and_reject(tmp_path, cite_stale=False)
    plain = {f["kind"] for f in audit_run(root, root / "report.md")["flags"]}
    assert "claim_from_tainted_input" in plain            # step-level taint over-flags
    res = audit_run(root, root / "report.md", counterfactual=True)
    kinds = {f["kind"] for f in res["flags"]}
    assert "claim_from_tainted_input" not in kinds        # the cited number is independent of it
    assert res["counterfactual"]["run ana"]["status"] == "ok"
    leftover = [f for f in res["flags"] if f["kind"] == "leftover_from_before_run"]
    assert leftover and leftover[0]["disclosed"] is True


def test_counterfactual_keeps_numbers_that_do_depend_on_it(tmp_path):
    root = _inspect_and_reject(tmp_path, cite_stale=True)
    kinds = {f["kind"] for f in audit_run(root, root / "report.md", counterfactual=True)["flags"]}
    assert "claim_from_tainted_input" in kinds
