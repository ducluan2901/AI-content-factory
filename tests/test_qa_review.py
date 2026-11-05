from app.qa_review import as_check


def test_qa_review_becomes_one_warning_row():
    good = as_check({"scenes": [{"scene": 1, "ok": True, "issue": ""}], "facts": [], "score": 9})
    bad = as_check({"scenes": [{"scene": 2, "ok": False, "issue": "hổ, không phải mèo"}], "facts": ["x"] * 9, "score": 4})
    assert good["passed"] and good["severity"] == "warning"
    assert not bad["passed"] and "cảnh 2: hổ" in bad["message"] and bad["message"].count("fact:") == 4  # capped at 5 issues
    assert as_check(None)["actual"] == "not reviewed"
