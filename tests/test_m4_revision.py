"""Revisions: numbers left over from a superseded step, and data updates."""

import sys

from hepprov.audit import audit_run
from hepprov.recorder import Recorder

PY = sys.executable

DATA = "x\n" + "".join(f"{(i * 37) % 100 / 100:.2f}\n" for i in range(400))


def _ana(cut):
    return ("xs = [float(v) for v in open('data.csv').read().split()[1:]]\n"
            f"eff = sum(x > {cut} for x in xs) / len(xs)\n"
            "print('efficiency', round(eff, 4))\n")


def _setup(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "data.csv").write_text(DATA)
    rec = Recorder(root)
    rec.init(declare=["data.csv"])
    rec.write_file("ana.py", _ana(0.6))
    rec.exec([PY, "ana.py"], label="ana v1", echo=False)          # efficiency 0.39
    rec.write_file("ana.py", _ana(0.8))                            # the requester changed the cut
    rec.exec([PY, "ana.py"], label="ana v2", echo=False)          # efficiency 0.19
    return root, rec


def test_unmarked_leftover_number_is_a_stale_claim(tmp_path):
    root, rec = _setup(tmp_path)
    rec.write_file("report.md", "With the new cut the efficiency is 0.19. The summary table lists 0.39.\n")
    res = audit_run(root, root / "report.md")
    stale = [f for f in res["flags"] if f["kind"] == "stale_claim"]
    assert [f["detail"].split()[0] for f in stale] == ["0.39"]
    assert stale[0]["severity"] == "defect"


def test_earlier_value_marked_as_such_is_info(tmp_path):
    root, rec = _setup(tmp_path)
    rec.write_file("report.md", "The efficiency is now 0.19 (previously 0.39 with the old cut).\n")
    stale = [f for f in audit_run(root, root / "report.md")["flags"] if f["kind"] == "stale_claim"]
    assert stale and all(f["severity"] == "info" for f in stale)


def test_number_from_a_step_whose_input_was_updated_is_stale(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "data.csv").write_text(DATA)
    rec = Recorder(root)
    rec.init(declare=["data.csv"])
    rec.write_file("ana.py", _ana(0.6))
    rec.exec([PY, "ana.py"], label="ana", echo=False)              # 0.39 on the old data
    rec.write_file("data.csv", "x\n" + "0.95\n" * 100 + "0.10\n" * 300, label="data update")
    rec.write_file("report.md", "Efficiency 0.39.\n")              # never re-ran on the new data
    stale = [f for f in audit_run(root, root / "report.md")["flags"] if f["kind"] == "stale_claim"]
    assert stale and "replaced later" in stale[0]["detail"]
