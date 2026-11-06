"""Milestone 2: static detectors, and the reference pipeline's clean control."""

from pathlib import Path

from hepprov.audit import audit_run
from hepprov.calib.pipeline import Defects, run_pipeline
from hepprov.static import silent_skips, transcribed_literals


def test_transcribed_literal_found_at_its_precision():
    prior = {"json@analyze:results.json:baseline_yield": 1474.3912}
    code = "base = 1474.39  # from previous step\nx = 0.5\ny = 3.14159\n"
    assert [h["literal"] for h in transcribed_literals(code, prior)] == ["1474.39"]


def test_rounded_retype_still_counts_but_short_constants_do_not():
    prior = {"a": 0.5, "b": 1479.23}
    hits = transcribed_literals("x = 0.5\ny = 1480.0\nz = 1479.2\n", prior)
    assert [h["literal"] for h in hits] == ["1479.2"]


def test_silent_skip_patterns():
    code = ("for f in fs:\n    if not os.path.exists(f):\n        continue\n"
            "for g in gs:\n    if not files: continue\n"
            "for h in hs:\n    if h is None:\n        continue\n")
    assert [s["line"] for s in silent_skips(code)] == [2, 5]


def test_clean_pipeline_has_no_flags_and_injection_is_caught(tmp_path):
    clean = audit_run(tmp_path / "c", run_pipeline(tmp_path / "c", seed=7))
    assert clean["flags"] == [], clean["flags"]
    bad = audit_run(tmp_path / "b", run_pipeline(tmp_path / "b", Defects(placeholder_energy=0.005), seed=7))
    assert "placeholder_signature" in {f["kind"] for f in bad["flags"]}
