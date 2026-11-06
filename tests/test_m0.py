"""Milestone 0: manifest chain, recorder (L1, L2), read attestation.

Each test reproduces a defect class from the GRACE artifact audit, or a stated
capture limit.
"""

import json
import sys
from pathlib import Path

import pytest

from hepprov.attest import attest
from hepprov.manifest import Manifest
from hepprov.recorder import Recorder

PY = sys.executable


def run(rec, code, label=""):
    return rec.exec([PY, "-c", code], label=label, echo=False)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "run"
    r.mkdir()
    return r


def kinds(root):
    return [v["kind"] for v in attest(Manifest(root))["violations"]]


# ---------------------------------------------------------------- manifest

def test_chain_detects_edit_delete_and_reorder(root):
    rec = Recorder(root)
    rec.init()
    run(rec, "open('a.txt','w').write('1')")
    run(rec, "open('b.txt','w').write('2')")
    m = Manifest(root)
    assert m.verify_chain() == []
    lines = m.path.read_text().splitlines()

    edited = lines.copy()
    ev = json.loads(edited[1]); ev["data"]["exit_code"] = 0 if ev["data"]["exit_code"] else 1
    edited[1] = json.dumps(ev)
    m.path.write_text("\n".join(edited) + "\n")
    assert m.verify_chain()

    m.path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert m.verify_chain()

    m.path.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n")
    assert m.verify_chain()


# ---------------------------------------------------------------- recorder + attestation

def test_clean_pipeline_attests(root):
    rec = Recorder(root)
    rec.init()
    run(rec, "open('events.csv','w').write('e\\n1\\n2\\n3\\n')", "simulate")
    ev = run(rec, "import json; v=[float(x) for x in open('events.csv').read().split()[1:]];"
                  "print(json.dumps({'mean': sum(v)/len(v)}))", "analyze")
    d = ev["data"]
    assert [r["path"] for r in d["reads"]] == ["events.csv"]
    assert d["capture"]["L2"] is True
    res = attest(Manifest(root))
    assert res["ok"] and res["reads"] == 1


def test_leftover_from_reused_run_dir_is_flagged(root):
    """Audit: two analyses read a 5000-event file no step of the run had written."""
    (root / "det_events.csv").write_text("e\n7430.8\n" * 3)
    rec = Recorder(root)
    rec.init()
    run(rec, "print(open('det_events.csv').read()[:10])", "analyze")
    assert kinds(root) == ["leftover_from_before_run"]


def test_change_outside_recorder_is_flagged(root):
    rec = Recorder(root)
    rec.init()
    run(rec, "open('x.json','w').write('{\"v\": 1}')", "produce")
    (root / "x.json").write_text('{"v": 2, "edited": "by hand"}')   # not through hepprov
    run(rec, "print(open('x.json').read())", "consume")
    assert kinds(root) == ["written_outside_recorder"]
    assert [e["kind"] for e in Manifest(root).events()].count("untracked_change") == 1


def test_declared_input_is_ok(root):
    (root / "detector.gdml").write_text("<gdml/>")
    rec = Recorder(root)
    rec.init(declare=["detector.gdml"])
    run(rec, "print(len(open('detector.gdml').read()))")
    assert attest(Manifest(root))["ok"]


def test_parquet_read_through_pandas_is_captured(root):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    rec = Recorder(root)
    rec.init()
    run(rec, "import pandas as pd; pd.DataFrame({'nHits':[1,2,3]}).to_parquet('ev.parquet')", "sim")
    ev = run(rec, "import pandas as pd; print(pd.read_parquet('ev.parquet').nHits.mean())", "ana")
    assert "ev.parquet" in [r["path"] for r in ev["data"]["reads"]]
    assert attest(Manifest(root))["ok"]


def test_overwrite_by_another_step_is_reported(root):
    """GRACE v1 merged batch files dropped the particle suffix: proton overwrote electron."""
    rec = Recorder(root)
    rec.init()
    run(rec, "open('merged_events.csv','w').write('electron')", "electron_sweep")
    run(rec, "open('merged_events.csv','w').write('proton')", "proton_sweep")
    assert attest(Manifest(root))["overwrites"] == {"merged_events.csv": ["electron_sweep", "proton_sweep"]}


def test_uninstrumented_process_is_reported_not_hidden(root):
    """`python -I` ignores PYTHONPATH: its writes are seen (L1), its reads are not (L2 off)."""
    (root / "in.txt").write_text("x")
    rec = Recorder(root)
    rec.init(declare=["in.txt"])
    ev = rec.exec([PY, "-I", "-c", "open('out.txt','w').write(open('in.txt').read())"], echo=False)
    d = ev["data"]
    assert "out.txt" in d["writes"]
    assert d["reads"] == [] and d["capture"]["L2"] is False


def test_exit_code_and_output_hashes_recorded(root):
    rec = Recorder(root)
    rec.init()
    ev = run(rec, "import sys; print('hi'); sys.exit(3)")
    assert ev["data"]["exit_code"] == 3
    assert len(ev["data"]["stdout_sha256"]) == 64
