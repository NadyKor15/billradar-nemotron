"""policy_finder: the hospital's site in three steps, the policy on that site only, quotes checked by code.

Synthetic hospitals and pages (Lakeview Test Hospital, lakeviewtest.org) for the logic; the recorded live run on
real hospitals (billradar/policy_finder_run.json) for the replay."""

import json
from datetime import date
from decimal import Decimal

import pytest

from billradar import agent_api, pipeline, policy_finder as pf, tavily_client as tc
from billradar.case import Case, Document, DocKind, LocalDetails, Profile, Source

LAKEVIEW = """Lakeview Test Hospital
Financial Assistance Policy
Revised: March 2026
Patients with household income at or below 200% of the Federal Poverty Guidelines (FPG) qualify for free care.
Patients with household income between 201% and 400% of the FPG receive a 75% discount on charges.
"""
TIERS = [
    {"from_fpl_pct": 0, "up_to_fpl_pct": 200, "benefit": "free", "discount_pct": 100,
     "quote": "household income at or below 200% of the Federal Poverty Guidelines (FPG) qualify for free care"},
    {"from_fpl_pct": 201, "up_to_fpl_pct": 400, "benefit": "discount", "discount_pct": 75,
     "quote": "between 201% and 400% of the FPG receive a 75% discount"},
]


class FakeTransport:
    def __init__(self, search=None, extract=None):
        self.search, self.extract, self.bodies = search or (lambda b: {"results": []}), extract, []

    def answer(self, endpoint, body):
        self.bodies.append((endpoint, body))
        fn = self.search if endpoint == "search" else (self.extract or (lambda b: {"results": []}))
        return tc.Answer({"request_id": f"req-{len(self.bodies)}", "usage": {"credits": tc.credits_for(endpoint, body)},
                          **fn(body)})


class FakeNemotron:
    provider = "nebius"

    def __init__(self, answer):
        self.answer, self.calls, self.bodies = answer, [], []

    def respond(self, body, label=""):
        self.bodies.append(body)
        self.calls.append({"label": label, "model": pf.MODEL, "answered_by": pf.MODEL, "response_id": "chatcmpl-1",
                           "usd": 0.0012, "live": False})
        return type("R", (), {"text": json.dumps(self.answer)})()


def page(url, text, title="Financial Assistance Policy", score=0.9):
    return {"url": url, "title": title, "content": text[:200], "raw_content": text, "score": score}


def not_laws(bodies):
    """The policy requests only: without the state-law search (state_laws.py) that follows a found policy."""
    from billradar.state_laws import STATES
    hosts = {h for c in STATES.values() for h in c["hosts"]}
    return [(e, b) for e, b in bodies if not set(b.get("include_domains") or []) & hosts]


def client(transport):
    return tc.TavilyClient(transport=transport, record_to=None, journal=None)


def policy_search(*pages):
    return lambda b: {"results": list(pages)} if b.get("include_domains") else {"results": []}


def run(hint, pages, answer=None, confirmed=False, site_results=None):
    t = FakeTransport(search=lambda b: {"results": list(pages)} if b.get("include_domains")
                      else {"results": list(site_results or [])})
    n = FakeNemotron(answer if answer is not None else {"title": "Financial Assistance Policy",
                                                          "date_quote": "Revised: March 2026", "tiers": TIERS})
    return pf.find(hint, client(t), n, confirmed=confirmed), t, n


SITE = pf.Hint(name="Lakeview Test Hospital", city="Springfield", state="TX", website="Pay online at www.lakeviewtest.org/pay")


# --- the quote check: must light on the real quote, go dark on a changed one ----------------------

def test_quotes_found_in_the_text_light():
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/fap.pdf", LAKEVIEW)])
    assert found.status == "verified"
    assert [t["up_to_fpl_pct"] for t in found.tiers] == [200, 400] and not found.dropped
    assert found.doc_date == "Revised: March 2026" and found.date_quote == "Revised: March 2026"


def test_a_changed_quote_goes_dark_and_the_unchanged_one_still_lights():
    changed = [dict(TIERS[0], quote=TIERS[0]["quote"].replace("200%", "300%"), up_to_fpl_pct=300), TIERS[1]]
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/fap.pdf", LAKEVIEW)],
                      {"title": "", "date_quote": "", "tiers": changed})
    assert [t["up_to_fpl_pct"] for t in found.tiers] == [400]  # control: the true quote still passes
    assert found.dropped[0]["why"] == "quote not found in the policy text"


def test_a_true_quote_with_a_wrong_number_in_the_tier_is_dropped():
    wrong = [dict(TIERS[1], discount_pct=85)]
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/fap.pdf", LAKEVIEW)], {"title": "", "date_quote": "", "tiers": wrong})
    assert found.status == "link_only" and found.dropped[0]["why"] == "discount 85% is not what the quote prints"
    assert "check it yourself" in found.message


def test_a_free_tier_needs_free_in_its_quote():
    wrong = [dict(TIERS[0], quote="household income at or below 200% of the Federal Poverty Guidelines")]
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/fap.pdf", LAKEVIEW)], {"title": "", "date_quote": "", "tiers": wrong})
    assert found.dropped[0]["why"] == "the quote does not say the care is free"


def test_quote_check_ignores_line_breaks_and_markdown_marks():
    md = LAKEVIEW.replace("qualify for free care", "qualify for **free\ncare**")
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/fap", md)])
    assert len(found.tiers) == 2


# --- the site: three steps ---------------------------------------------------------------------

def test_step_1_the_site_on_the_bill_is_the_only_site_searched():
    found, t, _ = run(SITE, [page("https://www.lakeviewtest.org/fap.pdf", LAKEVIEW)])
    assert found.domain == "lakeviewtest.org" and found.domain_step == 1
    assert [e for e, _ in not_laws(t.bodies)] == ["search"]  # no search for the site itself
    assert t.bodies[0][1]["include_domains"] == ["lakeviewtest.org"]


def test_a_page_from_another_site_is_rejected_even_if_the_search_returns_it():
    found, _, _ = run(SITE, [page("https://careroute.ai/lakeview", LAKEVIEW.replace("75%", "90%")),
                             page("https://lakeviewtest.org.evil.com/fap", LAKEVIEW),
                             page("https://www.lakeviewtest.org/fap.pdf", LAKEVIEW)])
    assert found.url == "https://www.lakeviewtest.org/fap.pdf"
    assert {r["url"] for r in found.rejected} >= {"https://careroute.ai/lakeview", "https://lakeviewtest.org.evil.com/fap"}


def test_only_other_sites_means_not_found_and_no_model_call():
    found, _, n = run(SITE, [page("https://careroute.ai/lakeview", LAKEVIEW)])
    assert found.status == "not_found" and not n.bodies


MEMORIAL = pf.Hint(name="Memorial Regional Hospital", city="Hollywood", zip="33021", state="FL")
OTHER_MEMORIAL = {"url": "https://www.memorialregionalhealth.com/patients/financial-assistance",
                  "title": "Financial Assistance | Memorial Regional Health",
                  "content": "Memorial Regional Health, 750 Hospital Loop, Craig, Colorado 81625", "score": 0.95}
OUR_MEMORIAL = {"url": "https://www.mhs.net/locations/memorial-regional-hospital",
                "title": "Memorial Regional Hospital | Memorial Healthcare System",
                "content": "Memorial Regional Hospital, 3501 Johnson Street, Hollywood, FL 33021", "score": 0.8}


def test_step_2_drops_the_memorial_of_another_city_and_asks_about_the_right_one():
    found, t, n = run(MEMORIAL, [], site_results=[OTHER_MEMORIAL, OUR_MEMORIAL])
    assert found.status == "ask_user" and found.domain == ""
    assert found.candidate == {"name": "Memorial Regional Hospital", "city": "Hollywood", "site": "mhs.net",
                               "url": OUR_MEMORIAL["url"]}
    why = {r["url"]: r["why"] for r in found.rejected}
    assert "does not show Hollywood" in why[OTHER_MEMORIAL["url"]]
    assert "include_domains" not in t.bodies[0][1] and not n.bodies


def test_step_2_takes_a_site_that_shows_the_name_and_city():
    hint = pf.Hint(name="Houston Methodist Hospital", city="Houston", state="TX")
    hm = {"url": "https://www.houstonmethodist.org/locations/houston-methodist-hospital/",
          "title": "Houston Methodist Hospital", "content": "6565 Fannin St, Houston, TX 77030"}
    found, t, _ = run(hint, [page("https://www.houstonmethodist.org/fap", LAKEVIEW)], site_results=[hm])
    assert found.domain == "houstonmethodist.org" and found.domain_step == 2
    assert t.bodies[1][1]["include_domains"] == ["houstonmethodist.org"]


def test_step_2_a_directory_page_is_never_the_hospital_site():
    hint = pf.Hint(name="Lakeview Test Hospital", city="Springfield", state="TX")
    hg = {"url": "https://www.healthgrades.com/hospital/lakeview-test-hospital", "title": "Lakeview Test Hospital",
          "content": "Lakeview Test Hospital, Springfield, TX"}
    found, _, _ = run(hint, [], site_results=[hg])
    assert found.status == "ask_user" and found.candidate is None and "type the site" in found.message


def test_step_3_the_family_confirms_and_only_that_site_is_searched():
    hint = pf.Hint(name="Memorial Regional Hospital", city="Hollywood", zip="33021", state="FL", website="mhs.net")
    found, t, _ = run(hint, [page("https://www.mhs.net/fap.pdf", LAKEVIEW)], confirmed=True)
    assert found.domain_step == 3 and "you confirmed" in found.domain_note
    assert t.bodies[0][1]["include_domains"] == ["mhs.net"]


# --- the document: version, kind, language, empty text ----------------------------------------

def test_the_newest_version_wins_and_the_older_is_named():
    old = LAKEVIEW.replace("Revised: March 2026", "Effective: January 2018").replace("75%", "60%")
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/2018/fap.pdf", old, score=0.99),
                             page("https://www.lakeviewtest.org/fap.pdf", LAKEVIEW, score=0.5)])
    assert found.url == "https://www.lakeviewtest.org/fap.pdf"
    assert any("older version (2018)" in r["why"] for r in found.rejected)


def test_an_undated_policy_says_check_it_is_current():
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/fap", LAKEVIEW.replace("Revised: March 2026\n", ""))],
                      {"title": "", "date_quote": "", "tiers": TIERS})
    assert found.status == "undated" and "check that it is the current policy" in found.date_note
    assert "may be out of date" in found.message


def test_a_needs_assessment_is_not_the_policy():
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/chna-2025.pdf", LAKEVIEW + " 400% FPL",
                                  title="Community Health Needs Assessment 2025")])
    assert found.status == "not_found" and "another document" in found.rejected[0]["why"]


def test_another_language_is_not_used():
    korean = "재정 지원 정책 " * 200
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/ko/fap.pdf", LAKEVIEW, title="FAP Korean"),
                             page("https://www.lakeviewtest.org/fap-kr.pdf", "Lakeview Test Hospital " + korean)])
    assert found.status == "not_found" and all("English" in r["why"] for r in found.rejected)


def test_empty_text_tries_extract_then_gives_the_link_only():
    t = FakeTransport(search=lambda b: {"results": [page("https://www.lakeviewtest.org/fap.pdf", "")]},
                      extract=lambda b: {"results": [{"url": b["urls"][0], "raw_content": ""}]})
    n = FakeNemotron({"title": "", "date_quote": "", "tiers": TIERS})
    found = pf.find(SITE, client(t), n)
    assert [e for e, _ in not_laws(t.bodies)] == ["search", "search", "extract"]  # policy, a dated copy, then extract
    assert found.status == "link_only" and not n.bodies
    assert found.url == "https://www.lakeviewtest.org/fap.pdf" and "check it yourself" in found.message


def test_extract_text_is_used_when_search_has_none():
    t = FakeTransport(search=lambda b: {"results": [page("https://www.lakeviewtest.org/fap.pdf", "")]},
                      extract=lambda b: {"results": [{"url": b["urls"][0], "raw_content": LAKEVIEW}]})
    found = pf.find(SITE, client(t), FakeNemotron({"title": "", "date_quote": "", "tiers": TIERS}))
    assert found.status == "verified" and found.doc_date == "Revised: March 2026"


# --- what leaves the device -------------------------------------------------------------------

def _case(provider="Lakeview Test Hospital", state="TX"):
    doc = Document(doc_id="S1", source=Source.PAPER, kind=DocKind.HOSPITAL_STATEMENT, provider=provider,
                   account_ref="ACCT_1", patient="PATIENT_1", patient_balance=Decimal("812.40"))
    prof = Profile(household_size=3, annual_income=Decimal("41000"),
                   local=LocalDetails(street="12 Secret Lane", city="Springfield", state=state, zip="62704",
                                      tokens={"ACCT_1": "998877665544"}))
    return Case(case_id="c1", policy_id="memorial-2026-05", today=date(2026, 10, 29), profile=prof, documents=[doc])


def test_flag_off_means_no_search(monkeypatch):
    monkeypatch.delenv(pf.FLAG, raising=False)
    monkeypatch.setattr(pf, "find", lambda *a, **k: pytest.fail("searched with the flag off"))
    assert pf.for_case(_case()) is None


def test_flag_on_a_hospital_in_the_base_is_not_searched(monkeypatch):
    monkeypatch.setenv(pf.FLAG, "1")
    monkeypatch.setattr(pf, "find", lambda *a, **k: pytest.fail("searched a hospital that is in the base"))
    assert pf.for_case(_case("Memorial Regional Hospital", "FL"))["status"] == "in_base"
    assert pf.known_policy("Baptist Hospital", "TX") is None  # same word, other state: not ours


def test_the_pipeline_sends_nothing_until_the_family_gives_a_site_or_a_city(monkeypatch):
    monkeypatch.setenv(pf.FLAG, "1")
    t = FakeTransport(search=lambda b: {"results": []})
    monkeypatch.setattr(pf, "TavilyClient", lambda: client(t))
    found = pf.for_case(_case())
    assert found["status"] == "ask_user" and "Type the hospital's site" in found["message"] and t.bodies == []


def test_only_the_hospital_fields_leave_the_device_when_the_family_types_them(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from billradar import app as app_module
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    monkeypatch.setenv(pf.FLAG, "1")
    d = _saved_case(tmp_path)
    case = _case()  # bills from Lakeview Test Hospital; the family lives at 12 Secret Lane, Springfield 62704
    case.case_id = "t1"
    (d / "case.json").write_text(case.model_dump_json())
    t = FakeTransport(search=lambda b: {"results": []})
    monkeypatch.setattr(pf, "TavilyClient", lambda: client(t))
    c = TestClient(app_module.app)
    assert c.post("/cases/t1/policy-finder", data={"place": "Austin", "state": "tx"}, follow_redirects=False).status_code == 303
    sent = json.dumps([b for _, b in t.bodies])
    for secret in ("12 Secret Lane", "62704", "Springfield", "PATIENT", "ACCT"):
        assert secret not in sent
    assert t.bodies == [("search", {"query": "Lakeview Test Hospital Austin TX", "search_depth": "basic",
                                    "max_results": 5, "country": "united states"})]
    assert c.post("/cases/t1/policy-finder", data={"place": "33021 4111111111111111"}).status_code == 400
    assert c.post("/cases/t1/policy-finder", data={"place": "Austin"},
                  headers={"Origin": "https://evil.example"}).status_code == 403
    assert len(t.bodies) == 1  # neither bad request reached Tavily


def test_pipeline_saves_the_result_only_when_there_is_one(tmp_path, monkeypatch):
    monkeypatch.setenv(pf.FLAG, "1")
    run_ = pipeline.CaseRun(case=_case(), report=None, sheets=[], overlays=[], found_policy={"status": "ask_user"})
    run_.report = type("R", (), {"model_dump_json": lambda self, indent=2: "{}"})()
    pipeline.save(run_, tmp_path / "a")
    assert json.loads((tmp_path / "a" / "policy-finder.json").read_text()) == {"status": "ask_user"}
    run_.found_policy = None
    pipeline.save(run_, tmp_path / "b")
    assert not (tmp_path / "b" / "policy-finder.json").exists()


# --- credits ----------------------------------------------------------------------------------

def test_the_credit_cap_stops_before_the_call(tmp_path, monkeypatch):
    monkeypatch.setattr(tc.requests, "post", lambda *a, **k: pytest.fail("called Tavily past the cap"))
    live = tc.LiveTransport("key", ledger=tc.CreditLedger(tmp_path / "c.jsonl", cap=1))
    with pytest.raises(tc.CreditCapReached):
        live.answer("search", {"query": "x", "search_depth": "advanced"})
    assert tc.credits_for("search", {}) == 1 and tc.credits_for("extract", {"urls": list("abcdef")}) == 2


def test_replay_without_a_recording_is_an_error_not_a_guess(tmp_path):
    t = tc.TavilyClient(transport=tc.ReplayTransport(tmp_path), record_to=None, journal=None)
    found = pf.find(SITE, t, FakeNemotron({"title": "", "date_quote": "", "tiers": TIERS}))
    assert found.status == "error" and "no Tavily recording" in found.message


# --- the recorded live run on real hospitals --------------------------------------------------

recorded = pytest.mark.skipif(not pf.run_plan().get("recorded"), reason="the live run is not recorded yet")


@recorded
def test_the_recorded_run_replays_offline_and_every_shown_tier_is_in_its_page():
    out = pf.replay_run()
    assert out["hospitals"] >= 5 and out["states"] >= 4 and len(out["results"]) == len(pf.run_plan()["runs"])
    assert not [r for r in out["results"] if r["status"] == "error"]
    for r in out["results"]:
        assert all(c.get("request_id") for c in r["tavily_calls"])
        if r["tiers"]:
            assert r["domain"] and r["url"] and pf.on_domain(r["url"], r["domain"])


@recorded
def test_the_recorded_memorial_search_never_takes_the_colorado_hospital():
    out = pf.replay_run()
    memorial = [r for r in out["results"] if r["hospital"] == "Memorial Regional Hospital"]
    assert memorial and all("memorialregionalhealth.com" not in (r["domain"] + r["url"]) for r in memorial)


@recorded
def test_find_policy_tool_replays_a_recorded_hospital_and_refuses_bad_input():
    item = pf.run_plan()["runs"][0]["hint"]
    out = agent_api.find_policy(item["name"], item["state"], item["city"], item["zip"], item["website"])
    assert out["replayed"] and out["status"] != "error" and "not medical or legal advice" in out["note"]
    with pytest.raises(agent_api.AgentError):
        agent_api.find_policy("X", "Texas")
    unknown = agent_api.find_policy("Nowhere Test Hospital", "TX", website="nowheretest.org")
    assert unknown["status"] == "error" and "not in the recorded run" in unknown["message"]


# --- the screens -------------------------------------------------------------------------------

@recorded
def test_policy_finder_page_shows_the_recorded_run_with_its_date_and_caveat():
    from fastapi.testclient import TestClient
    from billradar import app as app_module
    html = TestClient(app_module.app).get("/policy-finder").text
    assert f"Recorded live run, {pf.run_plan()['recorded']}" in html
    assert "Policies change; check the hospital" in html and "Nothing here promises a discount" in html
    assert "Houston Methodist Hospital" in html and "Is this your hospital?" in html
    assert "Tavily request_id" in html and 'class="pf-quote"' in html


def _saved_case(tmp_path):
    from tests.test_app import fake_client
    from tests.test_pipeline import profile
    run_ = pipeline.run(fake_client(), [], [], profile(), "memorial-2026-05", "Lakeview Demo Hospital",
                        date(2026, 6, 15), "t1")
    pipeline.save(run_, tmp_path / "t1")
    return tmp_path / "t1"


def test_case_screen_shows_the_block_only_with_a_result_and_the_form_only_with_the_flag(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from billradar import app as app_module, history
    monkeypatch.setattr(app_module, "RUNS", tmp_path)
    monkeypatch.delenv(pf.FLAG, raising=False)
    d = _saved_case(tmp_path)
    c = TestClient(app_module.app)
    assert "Your hospital's own policy" not in c.get("/cases/t1").text
    found = pf.Found(status="ask_user", hospital="Lakeview Test Hospital", state="TX",
                     candidate={"name": "Lakeview Test Hospital", "city": "Springfield", "site": "lakeviewtest.org",
                                "url": "https://www.lakeviewtest.org/"}).to_dict()
    (d / "policy-finder.json").write_text(json.dumps(found))
    assert "Your hospital's own policy" not in c.get("/cases/t1").text  # flag off: the screen as before, file or not
    assert c.post("/cases/t1/policy-finder", data={"website": "lakeviewtest.org", "confirmed": "1"}).status_code == 404
    monkeypatch.setenv(pf.FLAG, "1")
    html = c.get("/cases/t1").text
    assert "Your hospital's own policy" in html and "Is this your hospital?" in html and "Yes, this is my hospital" in html
    monkeypatch.setenv("BILLRADAR_PUBLIC", "1")  # a public deploy: only the browser that made the case may search
    assert c.post("/cases/t1/policy-finder", data={"website": "lakeviewtest.org", "confirmed": "1"},
                  follow_redirects=False).status_code == 403
    seen = {}
    monkeypatch.setattr(pf, "find", lambda hint, confirmed=False: seen.update(hint=hint, confirmed=confirmed) or
                        pf.Found(status="verified", hospital=hint.name, domain="lakeviewtest.org", domain_step=3,
                                 tiers=TIERS, url="https://www.lakeviewtest.org/fap.pdf"))
    c.cookies.set(history.cookie_name("t1"), history.issue_token(d))
    r = c.post("/cases/t1/policy-finder", data={"website": "lakeviewtest.org", "confirmed": "1"}, follow_redirects=False)
    assert r.status_code == 303 and seen["confirmed"] and seen["hint"].website == "lakeviewtest.org"
    html = c.get("/cases/t1").text
    assert "you confirmed" in html and "Free care" in html and "75% off" in html


# --- attacks on the quote check (independent review, 29.10) -------------------------------------

TABLE = """Lakeview Test Hospital Financial Assistance Policy, Revised: March 2026
| Income | Discount |
| 0% - 200% of FPG | Free |
| 201% - 300% of FPG | 75% Discount |
| 301% - 400% of FPG | 50% Discount |
Patients with 201% to 300% of FPL qualify for charity care at 75%.
"""


@pytest.mark.parametrize("tier,why", [
    ({"from_fpl_pct": 0, "up_to_fpl_pct": 300, "benefit": "discount", "discount_pct": 75,
      "quote": "201% - 300% of FPG | 75% Discount"}, "does not start at zero"),
    ({"from_fpl_pct": 201, "up_to_fpl_pct": 200, "benefit": "discount", "discount_pct": 75,
      "quote": "201% - 300% of FPG | 75% Discount"}, "up to 200%"),
    ({"from_fpl_pct": 201, "up_to_fpl_pct": 300, "benefit": "discount", "discount_pct": 100,
      "quote": "201% - 300% of FPG | 75% Discount"}, "75% discount, not free"),
    ({"from_fpl_pct": 201, "up_to_fpl_pct": 300, "benefit": "free", "discount_pct": 100,
      "quote": "201% to 300% of FPL qualify for charity care at 75%"}, "does not say the care is free"),
    ({"from_fpl_pct": 0, "up_to_fpl_pct": 300, "benefit": "free", "discount_pct": 100,
      "quote": "Free |\n| 201% - 300% of FPG"}, "across table rows"),
    ({"from_fpl_pct": 200, "up_to_fpl_pct": 400, "benefit": "discount", "discount_pct": 50,
      "quote": "301% - 400% of FPG | 50% Discount"}, "from 200%"),
    ({"from_fpl_pct": 0, "up_to_fpl_pct": 75, "benefit": "discount", "discount_pct": 300,
      "quote": "201% - 300% of FPG | 75% Discount"}, "up to 75%"),
])
def test_a_tier_must_match_what_its_quote_prints(tier, why):
    assert why in pf.check_tier(tier, pf._key(TABLE), below=200)


def test_the_true_rows_of_the_same_table_still_pass():
    key = pf._key(TABLE)
    ok = [{"from_fpl_pct": 0, "up_to_fpl_pct": 200, "benefit": "free", "discount_pct": 100, "quote": "0% - 200% of FPG | Free"},
          {"from_fpl_pct": 201, "up_to_fpl_pct": 300, "benefit": "discount", "discount_pct": 75,
           "quote": "201% - 300% of FPG | 75% Discount"}]
    assert [pf.check_tier(t, key) for t in ok] == ["", ""]


def test_a_row_the_model_left_out_makes_the_result_partial():
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/fap.pdf", TABLE)], {"title": "", "date_quote": "", "tiers": [
        {"from_fpl_pct": 0, "up_to_fpl_pct": 200, "benefit": "free", "discount_pct": 100, "quote": "0% - 200% of FPG | Free"},
        {"from_fpl_pct": 201, "up_to_fpl_pct": 300, "benefit": "discount", "discount_pct": 75,
         "quote": "201% - 300% of FPG | 75% Discount"}]})
    assert found.status == "partial" and found.missing == ["301%–400%"] and "301%–400%" in found.message


def test_a_discount_without_a_size_is_said_in_words_not_as_zero():
    assert pf.shows({"benefit": "discount", "discount_pct": 0}) == "a discount, amount not stated"
    assert pf.shows({"benefit": "free", "discount_pct": 100}) == "free care"


def test_a_spend_cap_is_a_visible_error_not_a_crash():
    class Capped(FakeNemotron):
        def respond(self, body, label=""):
            from billradar.nemotron_client import SpendCapReached
            raise SpendCapReached("over the cap")
    t = FakeTransport(search=lambda b: {"results": [page("https://www.lakeviewtest.org/fap.pdf", LAKEVIEW)]})
    found = pf.find(SITE, client(t), Capped({}))
    assert found.status == "error" and found.message.startswith("stopped:") and not found.tiers


def test_shared_hosts_odd_schemes_and_two_level_suffixes():
    assert pf.domain_of("https://storage.googleapis.com/b/lakeviewtest.org/fap.pdf") == ""
    assert pf.domain_of("sites.google.com/view/lakeview") == "" and pf.domain_of("www.nhs.co.uk/x") == "nhs.co.uk"
    assert not pf.on_domain("javascript://lakeviewtest.org/%0aalert(1)", "lakeviewtest.org")
    assert pf.on_domain("https://pay.lakeviewtest.org/fap", "lakeviewtest.org")
    found, _, _ = run(pf.Hint(name="Lakeview Test Hospital", state="TX", website="https://storage.googleapis.com/x"), [])
    assert found.status == "ask_user" and "shared host" in found.message


def test_west_hollywood_in_california_is_not_hollywood_florida():
    west = {"url": "https://www.memorialcare.org/locations", "title": "Memorial Regional Hospital | MemorialCare",
            "content": "Memorial Regional Hospital, 100 Sunset Blvd, West Hollywood, CA"}
    other_state = {"url": "https://www.memorialhollywood.org/", "title": "Memorial Regional Hospital",
                   "content": "Memorial Regional Hospital, Hollywood, CA 90028"}
    found, _, _ = run(pf.Hint(name="Memorial Regional Hospital", city="Hollywood", state="FL"), [],
                      site_results=[west, other_state])
    assert found.status == "ask_user" and found.domain == "" and found.candidate is None


def test_a_policy_page_must_name_the_hospital():
    other = LAKEVIEW.replace("Lakeview Test Hospital", "Riverside Test Clinic")
    found, _, _ = run(SITE, [page("https://www.lakeviewtest.org/riverside-fap.pdf", other)])
    assert found.status == "not_found" and "does not name this hospital" in found.rejected[0]["why"]


def test_the_mcp_tool_never_goes_live_with_the_flag_off(monkeypatch):
    monkeypatch.delenv(pf.FLAG, raising=False)
    for k, v in {"BILLRADAR_LIVE": "1", "BILLRADAR_AGENT_LIVE": "1", "TAVILY_API_KEY": "x"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(tc.LiveTransport, "answer", lambda *a, **k: pytest.fail("live Tavily call with the flag off"))
    token = agent_api.authorized.set(True)
    try:
        out = agent_api.find_policy("Lakeview Test Hospital", "TX", website="lakeviewtest.org")
    finally:
        agent_api.authorized.reset(token)
    assert out["replayed"] and out["status"] == "error"


# --- guards on the recorded run (NM2, 29.10) ----------------------------------------------------

@recorded
def test_recorded_memorial_tiers_are_exactly_the_checked_base():
    from billradar import policy
    out = pf.replay_run()
    mem = next(r for r in out["results"] if r["domain"] == "mhs.net" and r["tiers"])
    base = [(t.max_fpl_pct, t.discount_pct) for t in policy.load("memorial-2026-05").tiers]
    assert [(t["up_to_fpl_pct"], t["discount_pct"]) for t in mem["tiers"]] == base
    assert mem["status"] == "verified" and not mem["missing"]


@recorded
def test_recorded_run_never_calls_an_undated_page_verified():
    for r in pf.replay_run()["results"]:
        if r["status"] == "verified":
            assert r["doc_date"] and not r["missing"]
        for t in r["tiers"]:
            assert not t["shows"].startswith("0%")
            if not t["discount_pct"]:
                assert t["shows"] in ("free care", "a discount, amount not stated")


def test_a_city_inside_another_city_name_or_in_another_state_does_not_match():
    h = pf.Hint(name="Memorial Regional Hospital", city="Hollywood", state="FL")
    assert not pf.place_match(h, "Memorial Regional Hospital in West Hollywood near Sunset Strip")  # prefix only
    assert not pf.place_match(h, "Memorial Regional Hospital, Hollywood, CA")  # state only
    assert not pf.place_match(h, "Memorial Regional Hospital, Hollywood, California")
    assert pf.place_match(h, "Memorial Regional Hospital, Hollywood, FL") and pf.place_match(h, "located in Hollywood.")
    assert pf.place_match(pf.Hint(name="X", zip="33021"), "Johnson St, Hollywood, Florida 33021")
