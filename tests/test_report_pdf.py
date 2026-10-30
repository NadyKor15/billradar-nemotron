"""PDF report (pack A) and fixed wording in three languages (i18n). Fixtures are SYNTHETIC."""

import io
import re
from datetime import date
from decimal import ROUND_DOWN, Decimal

import pytest
from pypdf import PdfReader

from billradar import checks, i18n, report_pdf, view
from billradar.checks import Finding, Kind
from billradar.policies import source
from tests import test_appeal, test_writer

needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")


def pages(pdf: bytes) -> list[str]:
    return [p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages]


def flat(pdf: bytes) -> str:
    return re.sub(r"\s+", " ", " ".join(pages(pdf)))


@needs_text
def test_english_report_matches_the_screen_and_the_findings():
    c = test_writer.case(demo=True)
    r = checks.run(c)
    pdf = report_pdf.build(c, r)
    assert pdf.startswith(b"%PDF")
    text = flat(pdf)
    screen = view.build(c, r, [], True)
    assert report_pdf.headline(r, "en") == screen.headline == "Up to $1,100 may come off your bills"
    assert screen.headline in text and "Apply for financial assistance by September 2, 2026" in text
    for f in r.findings:
        assert f.title in text and f"${f.amount:,.2f}" in text if f.amount else f.title in text
    for title in ("Request for an itemized statement", "Billing dispute: amount does not match my insurance"):
        assert title in text
    assert all("SAMPLE - FICTIONAL DATA" in p for p in pages(pdf))
    assert "replayed from recordings, not a live run" in text and "It does not give medical advice" in text


@needs_text
def test_every_amount_in_the_report_comes_from_the_case_or_the_findings():
    c = test_writer.case(demo=True)
    r = checks.run(c)
    blob = c.model_dump_json() + r.model_dump_json()
    known = {Decimal(x).quantize(Decimal("0.01")) for x in re.findall(r'"(-?\d+(?:\.\d+)?)"', blob)}
    known.add(r.total_may_come_off.to_integral_value(rounding=ROUND_DOWN))
    text = flat(report_pdf.build(c, r))
    # amounts inside policy quotes ("more than $500") are the policy's words, not ours
    ours = re.sub(r"“[^”]*”", "", text)
    for amount in re.findall(r"\$([\d,]+(?:\.\d\d)?)", ours):
        assert Decimal(amount.replace(",", "")) in known, amount


@needs_text
def test_statutes_from_a_web_page_get_no_page_number():
    c = test_writer.case(demo=True)
    text = flat(report_pdf.build(c, checks.run(c)))
    assert "application period, page" not in text
    assert "Financial Assistance Policy (current public version), page 11" in text  # no revision month a dated letter could contradict


def test_live_run_names_the_models():
    c = test_appeal.case()
    text = flat(report_pdf.build(c, checks.run(c), replayed=False))
    assert "Nemotron 3.5 Super VL" in text and "Nemotron 3 Ultra" in text and "replayed" not in text


@needs_text
def test_russian_report_is_readable():
    c = test_writer.case(demo=True)
    text = flat(report_pdf.build(c, checks.run(c), lang="ru"))
    assert "Отчёт BillRadar" in text and "До 1 100 $ могут снять с ваших счетов" in text.replace(" ", " ")
    assert "Подать на финансовую помощь до 2 сентября 2026 г." in text
    assert "ОБРАЗЕЦ — ВЫМЫШЛЕННЫЕ ДАННЫЕ" in text and "Выписка страховой (EOB)" in text


def test_spanish_report_with_an_appeal():
    c = test_appeal.case()
    text = flat(report_pdf.build(c, checks.run(c), lang="es"))
    assert "Rechazos del seguro que puede apelar" in text and "Apele antes del 13 de abril de 2027" in text
    assert "You may file an appeal within 180 days after you receive notice of a denial." in text
    assert "Appeal of denied claim CLM-7765301" in text


def test_borderline_findings_have_no_amount_and_do_not_count():
    c = test_appeal.case()
    r = checks.run(c)
    maybe = Finding(kind=Kind.P1, title="Room charged for a day after discharge?", detail="Line 9 may repeat line 8.",
                    amount=Decimal("123.45"), severity="borderline")
    r = r.model_copy(update={"findings": r.findings + [maybe]})
    text = flat(report_pdf.build(c, r))
    assert "Worth checking" in text and "Room charged for a day after discharge?" in text
    assert "$123.45" not in text


def test_unknown_language_is_refused():
    c = test_appeal.case()
    with pytest.raises(ValueError):
        report_pdf.build(c, checks.run(c), lang="de")


# --- i18n ----------------------------------------------------------------------------------

def test_every_string_has_all_three_languages():
    for key, by_lang in i18n.STRINGS.items():
        assert set(by_lang) == set(i18n.LANGS), key
        fields = {re.findall(r"\{(\w+)\}", s).__str__() for s in by_lang.values()}
        assert len(fields) == 1, key  # the same placeholders in every language


def test_dates_and_money():
    d = date(2027, 4, 13)
    assert i18n.date_text(d, "en") == "April 13, 2027"
    assert i18n.date_text(d, "ru") == "13 апреля 2027 г."
    assert i18n.date_text(d, "es") == "13 de abril de 2027"
    assert i18n.money(Decimal("6840"), "en") == "$6,840.00"
    assert i18n.money(Decimal("6840"), "ru") == "6 840,00 $"
