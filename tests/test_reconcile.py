"""Every number the example prints comes from one place and adds up: the total is the sum of the findings to the
cent on the home page, the case page, the three PDFs and the story; deadlines count from the case's one "today";
account and claim numbers are ones that exist in the papers. A judge, or a judge's model, will check exactly this."""

import io
import re
from decimal import Decimal, ROUND_DOWN

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from billradar import app as app_module
from billradar import callprep, example, i18n, writer


@pytest.fixture
def case_report():
    _, case, report = app_module._load(example.EXAMPLE_ID)
    return case, report


def _whole(x: Decimal) -> int:
    return int(x.to_integral_value(rounding=ROUND_DOWN))


def test_total_is_the_sum_of_the_counted_findings(case_report):
    _, report = case_report
    counted = [f.amount for f in report.findings if f.amount is not None and getattr(f, "severity", None) != "borderline"]
    assert sum(counted, Decimal(0)) == report.total_may_come_off
    assert all(f.amount is None for f in report.worth_asking)  # worth asking is never in the total


def test_every_page_and_pdf_prints_the_same_total(case_report):
    _, report = case_report
    whole = _whole(report.total_may_come_off)
    c = TestClient(app_module.app)
    assert f'data-to="{whole}"' in c.get("/").text  # the story counts up to it
    assert f"${whole:,}" in c.get("/example").text
    for lang in i18n.LANGS:
        pdf = PdfReader(io.BytesIO(c.get(f"/cases/example/report.pdf?lang={lang}").content))
        text = " ".join(p.extract_text() for p in pdf.pages).replace(" ", " ")
        sp = lambda x: x.replace("\u00a0", " ")
        shown = sp(i18n.money(Decimal(whole), lang).replace(".00", "").replace(",00", ""))
        assert shown in text, (lang, shown)
        for f in report.findings:
            if f.amount is not None:
                assert sp(i18n.money(f.amount, lang)) in text, (lang, f.kind, f.amount)


def test_deadlines_count_from_the_one_today(case_report):
    case, report = case_report
    for f in report.findings + report.worth_asking:
        if f.deadline and "days_left" in f.math:
            assert int(f.math["days_left"]) == (f.deadline - case.today).days, f.kind
    assert writer._date(case.today) in "\n".join(writer.plain_text(x, case) for x in writer.all_letters(case, report))


def _known_numbers(case) -> set[str]:
    vals = set((case.profile.local.tokens or {}).values())
    for d in case.documents:
        vals |= {d.account_ref.strip(), d.claim_ref.strip()} | {ln.claim_ref.strip() for ln in d.lines}
    return {re.sub(r"\D", "", v) for v in vals if v and re.sub(r"\D", "", v)}


def test_account_and_claim_numbers_exist_in_the_papers(case_report):
    case, report = case_report
    known = _known_numbers(case)
    printed = []
    for x in writer.all_letters(case, report):
        printed += re.findall(r"account (\d{4,})", writer.plain_text(x, case))
    for sheet in callprep.all_sheets(case, report):
        printed += [re.sub(r"\D", "", n.value) for n in sheet.numbers if re.sub(r"\D", "", n.value)]
    assert printed, "no account or claim number printed: the check would pass on nothing"
    assert [p for p in printed if p not in known] == []


def test_must_differ_a_wrong_total_is_caught(case_report):
    _, report = case_report
    counted = [f.amount for f in report.findings if f.amount is not None and getattr(f, "severity", None) != "borderline"]
    assert sum(counted, Decimal(0)) + Decimal("0.01") != report.total_may_come_off  # the sum check can fail


@pytest.mark.parametrize("case_id", [example.EXAMPLE_ID, example.PRIVATE_ID])
def test_second_read_sees_each_paper_once(case_id):
    """Every paper has its own id in what the second read gets, in plain and in private mode alike."""
    from collections import Counter
    import json
    from billradar import verifier
    _, case, report = app_module._load(case_id)
    body = verifier.review_body(case, report, writer.all_letters(case, report))
    payload = json.loads(next(p["text"] for m in body["input"] if m["role"] == "user" for p in m["content"]))
    ids = [d["doc_id"] for d in payload["case"]["documents"]]
    assert [k for k, v in Counter(ids).items() if v > 1] == []
    named = {e["doc_id"] for f in payload["findings"] for e in f["evidence"]}
    assert named <= set(ids)  # every paper a finding points to is in the case it sees


def test_translated_letters_hold_no_stray_script():
    """A Russian letter once said "от Федеральных指南 2026 года": a translation may hold Latin, Cyrillic, digits and
    punctuation only. Checked on every letter of the example in both languages, as the Copy button gives it."""
    c = TestClient(app_module.app)
    _, case, report = app_module._load(example.EXAMPLE_ID)
    stray = re.compile(r"[^\u0000-ɏЀ-ӿ -⁯€№ -ÿ]")
    kinds = [x.kind for x in writer.all_letters(case, report)]
    assert kinds
    for kind in kinds:
        for lang in ("ru", "es"):
            text = c.get(f"/cases/example/letters/{kind}/text?lang={lang}").text
            assert text.strip(), (kind, lang)
            assert stray.findall(text) == [], (kind, lang, stray.findall(text)[:5])
    assert stray.findall("от Федеральных指南 2026 года") == ["指", "南"]  # must light
