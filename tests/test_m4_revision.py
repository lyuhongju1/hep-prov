"""Revisions: numbers left over from a superseded step, and data updates."""

import sys

from hepprov.audit import audit_run
from hepprov.recorder import Recorder

PY = sys.executable

DATA = "x\n" + "".join(f"{(i * 37) % 100 / 100:.2f}\n" for i in range(400))


def _ana(cut):
    return ("xs = [float(v) for v in open('data.csv').read().split()[1:]]\n"
            f"rate = sum(x > {cut} for x in xs) / 7\n"
            "print('rate', round(rate, 4))\n")


def _setup(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "data.csv").write_text(DATA)
    rec = Recorder(root)
    rec.init(declare=["data.csv"])
    rec.write_file("ana.py", _ana(0.6))
    rec.exec([PY, "ana.py"], label="ana v1", echo=False)          # rate 22.2857
    rec.write_file("ana.py", _ana(0.8))                            # the requester changed the cut
    rec.exec([PY, "ana.py"], label="ana v2", echo=False)          # rate 10.8571
    return root, rec


def test_unmarked_leftover_number_is_a_stale_claim(tmp_path):
    root, rec = _setup(tmp_path)
    rec.write_file("report.md", "With the new cut the rate is 10.8571. The summary table lists 22.2857.\n")
    res = audit_run(root, root / "report.md")
    stale = [f for f in res["flags"] if f["kind"] == "stale_claim"]
    assert [f["detail"].split()[0] for f in stale] == ["22.2857"]
    assert stale[0]["severity"] == "defect"


def test_earlier_value_marked_as_such_is_info(tmp_path):
    root, rec = _setup(tmp_path)
    rec.write_file("report.md", "The rate is now 10.8571 (previously 22.2857 with the old cut).\n")
    stale = [f for f in audit_run(root, root / "report.md")["flags"] if f["kind"] == "stale_claim"]
    assert stale and all(f["severity"] == "info" for f in stale)


def test_number_from_a_step_whose_input_was_updated_is_stale(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "data.csv").write_text(DATA)
    rec = Recorder(root)
    rec.init(declare=["data.csv"])
    rec.write_file("ana.py", _ana(0.6))
    rec.exec([PY, "ana.py"], label="ana", echo=False)              # 22.2857 on the old data
    rec.write_file("data.csv", "x\n" + "0.95\n" * 100 + "0.10\n" * 300, label="data update")
    rec.write_file("report.md", "Rate 22.2857.\n")            # never re-ran on the new data
    stale = [f for f in audit_run(root, root / "report.md")["flags"] if f["kind"] == "stale_claim"]
    assert stale and "replaced later" in stale[0]["detail"]


def test_regeneration_keeps_changed_numbers_and_drops_unchanged_ones(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "data.csv").write_text(DATA)
    rec = Recorder(root)
    rec.init(declare=["data.csv"])
    rec.write_file("ana.py", "xs = [float(v) for v in open('data.csv').read().split()[1:]]\n"
                             "print('events', len(xs) * 3 + 1137)\n"
                             "print('efficiency', round(sum(x > 0.6 for x in xs) / len(xs), 4))\n")
    rec.exec([PY, "ana.py"], label="ana", echo=False)              # events 2337, efficiency 0.39
    rec.write_file("data.csv", "x\n" + "0.95\n" * 100 + "0.10\n" * 300, label="data update")
    rec.write_file("report.md", "We processed 2337 events. Efficiency 0.39.\n")
    res = audit_run(root, root / "report.md", counterfactual=True)
    stale = {f["detail"].split()[0]: f for f in res["flags"] if f["kind"] == "stale_claim"}
    assert "2337" not in stale                                     # same under regeneration
    assert stale["0.39"]["regenerated"] == "changed"


def test_disclosure_paragraph_marks_earlier_values(tmp_path):
    root, rec = _setup(tmp_path)
    rec.write_file("report.md", "Rate 10.8571.\n\nIn my first inspection the rate came out as 22.2857, "
                                "with the old selection; this was superseded.\n")
    stale = [f for f in audit_run(root, root / "report.md")["flags"] if f["kind"] == "stale_claim"]
    assert stale and all(f["severity"] == "info" for f in stale)


def test_presentational_revision_is_info(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "data.csv").write_text(DATA)
    rec = Recorder(root)
    rec.init(declare=["data.csv"])
    body = ("xs = [float(v) for v in open('data.csv').read().split()[1:]]\n"
            "m = sum(xs) / len(xs)\n"
            "print('mean', round(m, 6), 'sum', round(sum(xs), 3), 'n', len(xs) * 7 + 1001)\n")
    rec.write_file("ana.py", body + "print('extra', round(m * 3.1416, 6))\n")
    rec.exec([PY, "ana.py"], label="ana v1", echo=False)
    rec.write_file("ana.py", body)                                 # dropped one column, same computation
    rec.exec([PY, "ana.py"], label="ana v2", echo=False)
    extra = f"{round(sum((i * 37) % 100 / 100 for i in range(400)) / 400 * 3.1416, 6)}"
    rec.write_file("report.md", f"The scaled mean is {extra}.\n")
    stale = [f for f in audit_run(root, root / "report.md")["flags"] if f["kind"] == "stale_claim"]
    assert stale and all(f["presentational"] and f["severity"] == "info" for f in stale)


def test_declared_data_update_is_info_but_script_overwrite_is_defect(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "data.csv").write_text(DATA)
    rec = Recorder(root)
    rec.init(declare=["data.csv"])
    rec.write_file("data.csv", "x\n" + "0.95\n" * 100, label="data update")   # explicit, recorded
    rec.write_file("gen.py", "open('data.csv', 'w').write('x' + chr(10) + '0.5' + chr(10))\n")
    rec.exec([PY, "gen.py"], label="gen", echo=False)                        # a script clobbers it
    ow = [f for f in audit_run(root)["flags"] if f["kind"] == "overwrite" and f["path"] == "data.csv"]
    assert ow and ow[0]["severity"] == "defect"     # the script write is in the chain
    root2 = tmp_path / "run2"
    root2.mkdir()
    (root2 / "data.csv").write_text(DATA)
    rec2 = Recorder(root2)
    rec2.init(declare=["data.csv"])
    rec2.write_file("data.csv", "x\n" + "0.95\n" * 100, label="data update")
    ow2 = [f for f in audit_run(root2)["flags"] if f["kind"] == "overwrite"]
    assert all(f["severity"] == "info" for f in ow2)
