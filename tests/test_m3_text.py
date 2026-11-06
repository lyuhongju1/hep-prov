"""Report parsing and in-text derivation, motivated by the external HZZ agent corpus."""

from hepprov.verify import Pool, extract_claims, verify_report


def texts(s):
    return [c.text for c in extract_claims(s)]


def test_signs_metadata_and_word_boundaries():
    assert texts("A20 = -0.473641571 and C = +0.665084833") == ["-0.473641571", "0.665084833"]
    assert texts("range 10-20 GeV") == ["10", "20"]                       # hyphen in a range is not a sign
    assert texts("seed 20240790, `20240792`, 13,779,861 bytes, GCC 11.5.0 20240719") == []
    assert texts("accepts 3935/3971 eligible muons") == ["3935", "3971"]  # 'eligible' is not 'GiB'
    assert texts("see references/2410.17025-source") == []


def test_neighbourhood_derivation_uses_full_precision_evidence():
    # the report rounds both inputs; only their full-precision difference reproduces the shift
    pool = Pool()
    pool.add({"table:i3.csv:0.I3": 2.6090614, "table:i3.csv:1.I3": 3.2699115})
    res = verify_report("Isolation gives delta I3 = +0.660850 (2.609061 -> 3.269912).", pool, grammar=False,
                        intermediates=False)
    v = {x["text"]: x for x in res["verdicts"]}
    assert v["2.609061"]["verdict"] == "copy" and v["3.269912"]["verdict"] == "copy"
    assert v["0.660850"]["verdict"] == "derived" and v["0.660850"]["null_rate"] <= 0.05


def test_repeated_number_does_not_explain_itself():
    res = verify_report("Shift 0.660850. Later the shift 0.660850 again, with 0.1 and 0.1.", Pool(), intermediates=False)
    assert all(x["verdict"] == "unexplained" for x in res["verdicts"] if x["text"] == "0.660850")


def test_derived_from_text_only_when_inputs_unsupported():
    res = verify_report("Efficiency accepts 3935/3971 muons (99.093%).", Pool(), intermediates=False)
    v = {x["text"]: x for x in res["verdicts"]}
    assert v["99.093"]["verdict"] == "derived_from_text"
