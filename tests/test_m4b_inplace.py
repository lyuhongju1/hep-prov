"""In-place edits read back within one step, and stale printouts whose numbers live on in current files."""

import sys

from hepprov.audit import audit_run
from hepprov.recorder import Recorder

PY = sys.executable
NL = "chr(10)"


def test_in_place_edit_read_back_is_not_a_violation(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    rec = Recorder(root)
    rec.init(declare=[])
    rec.write_file("cfg.txt", "a=1\n")
    rec.write_file("edit.py",
                   "t = open('cfg.txt').read()\n"
                   "open('cfg.txt', 'w').write(t.replace('a=1', 'a=2'))\n"
                   "t = open('cfg.txt').read()\n"                      # read back the intermediate
                   f"open('cfg.txt', 'w').write(t + 'b=3' + {NL})\n")
    rec.exec([PY, "edit.py"], label="edit", echo=False)
    flags = audit_run(root)["flags"]
    assert not [f for f in flags if f["kind"] == "not_written_by_run"]


def test_stale_printout_whose_number_is_in_a_current_file_is_not_stale(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    rec = Recorder(root)
    rec.init(declare=[])
    rec.write_file("make.py", f"open('table.md', 'w').write('| r50 | 1.9783 |' + {NL})\n")
    rec.exec([PY, "make.py"], label="make v1", echo=False)
    rec.write_file("show.py", "print(open('table.md').read())\n")
    rec.exec([PY, "show.py"], label="show", echo=False)               # prints 1.9783
    rec.write_file("make.py", f"open('table.md', 'w').write('| r50 | 1.9783 |' + {NL} + '| note | rev 2 |')\n")
    rec.exec([PY, "make.py"], label="make v2", echo=False)            # table.md rewritten, same number
    rec.write_file("report.md", "The resolution at 50 GeV is 1.9783 %.\n")
    flags = audit_run(root, root / "report.md")["flags"]
    assert not [f for f in flags if f["kind"] == "stale_claim"]
