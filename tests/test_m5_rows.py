"""Table-row and prose-difference derivations, and their limits."""

from hepprov.verify import Pool, extract_claims, verify_report


def _verdicts(text):
    return {v["text"]: v for v in verify_report(text, Pool(), grammar=False, intermediates=False)["verdicts"]}


def test_difference_column_is_derived_from_its_row():
    v = _verdicts("| t | old | new | diff |\n|---|---|---|---|\n| 4 | 1.449 | 1.743 | +0.294 |\n")
    assert v["0.294"]["verdict"] == "derived_from_text"


def test_fabricated_cell_in_a_row_stays_unexplained():
    v = _verdicts("| t | old | new | diff |\n|---|---|---|---|\n| 4 | 1.449 | 1.743 | +0.317 |\n")
    assert v["0.317"]["verdict"] == "unexplained"


def test_difference_of_two_stated_pairs_is_derived():
    v = _verdicts("t=8 gives 2.165 ± 0.006 %, t=6 gives 1.963 ± 0.005 %, so it is 0.202 ± 0.008 % worse.\n")
    assert v["0.202"]["verdict"] == "derived_from_text"


def test_comma_list_is_not_a_thousands_number():
    texts = [c.text for c in extract_claims("energies {2,5,10,20,50,100} GeV and 28,794 events")]
    assert "50100" not in texts and "28794" in texts
