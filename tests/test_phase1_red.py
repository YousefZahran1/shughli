import pytest

from core import engine, filters, scoring, sources, tailor

LEVEL_CASES = [
    ("Internal Data Analyst", "general"),
    ("International Data Scientist", "general"),
    ("Junior Product Manager", "junior"),
    ("Junior Data Scientist - Lead Scoring", "junior"),
    ("Staffing Data Analyst", "general"),
    ("Data Architect Intern", "internship"),
    ("Leading-edge ML Engineer", "general"),
    ("Data Scientist, Postgraduate Programme", "general"),
    ("Sr. Data Scientist", "senior"),
    ("(Senior) Data Scientist", "senior"),
    ("Senior Data Scientist/ML Ops Engineer", "senior"),
    ("Staff Data Scientist", "senior"),
    ("Head of Data", "senior"),
    ("Lead Data Scientist", "senior"),
    ("Werkstudent Data Science (m/w/d)", "working student"),
    ("Werkstudentin Data Science", "working student"),
    ("Working Student Machine Learning", "working student"),
    ("Praktikant Data Analytics", "internship"),
    ("Praktikantin Data Analytics", "internship"),
    ("Data Science Internship", "internship"),
    ("Data Science Interns (Summer)", "internship"),
    ("Data Science Intern", "internship"),
    ("Graduate Data Analyst", "junior"),
    ("Trainee Data Analyst", "internship"),
    ("Data Scientist II", "general"),
]


@pytest.mark.parametrize("title,expected", LEVEL_CASES)
def test_job_level(title, expected):
    assert filters.job_level(title) == expected


@pytest.mark.parametrize("loc", ["Sydney, Australia", "Brussels, Belgium", "Moscow, Russia"])
def test_work_auth_us_no_false_match(loc):
    prof = {"work_authorization": [{"region": "US", "status": "citizen"}]}
    assert tailor._work_auth_for({"location": loc, "region": ""}, prof) == ""


def test_work_auth_us_true_match():
    prof = {"work_authorization": [{"region": "US", "status": "citizen"}]}
    assert tailor._work_auth_for({"location": "Houston, TX, US", "region": ""}, prof) == "citizen"


def _jobs(n):
    return [{"id": str(i), "title": "t", "company": "c", "location": "l"} for i in range(n)]


def test_scoring_dict_wrapper_and_percent(stub_generate):
    stub_generate.reply = '{"jobs":[{"id":"0","score":"85%","reason":"ok"},{"id":"1","score":70.0,"reason":"x"}, "junk"]}'
    res = scoring.score_jobs(_jobs(2), {"preferences": {}}, log=lambda *_: None)
    assert res["0"]["score"] == 85 and res["1"]["score"] == 70


def test_scoring_bad_item_does_not_kill_batch(stub_generate):
    stub_generate.reply = '[{"id":"0","score":"abc"},{"id":"1","score":999,"reason":"y"}]'
    res = scoring.score_jobs(_jobs(2), {"preferences": {}}, log=lambda *_: None)
    assert res["1"]["score"] == 100


def test_dedupe_same_posting_different_urls():
    prof = {"preferences": {"role_keywords": ["data scientist"], "seniority": []}}
    a = sources._normalize(title="Data Scientist", company="Acme GmbH", location="Berlin, DE",
                           region="x", source="a", url="http://a/1", description="short")
    b = sources._normalize(title="Data Scientist ", company="ACME GmbH", location="Berlin, Germany",
                           region="x", source="b", url="http://b/2", description="a much longer description")
    out = sources.dedupe([a, b])
    assert len(out) == 1 and out[0]["description"].startswith("a much")
