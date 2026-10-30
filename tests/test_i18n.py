"""Translations (pack A, Я15). Model answers are SYNTHETIC: a fake transport answers in the documented
Responses SSE format, and the tests check what our code does with good and bad translations."""

import io
import json
import re

import pytest
from pypdf import PdfReader

from billradar import checks, i18n, report_pdf, writer
from billradar.pipeline import RealDataGate
from tests import test_appeal
from tests.fakes import FakeClient


class FakeTranslator:
    """Answers each request with `translate(id, text)` applied to every item it was sent."""

    def __init__(self, translate):
        self.translate = translate
        self.bodies = []

    def events(self, body):
        self.bodies.append(body)
        sent = json.loads(body["input"][1]["content"][0]["text"])
        items = [{"id": it["id"], "text": self.translate(it["id"], it["text"])} for it in sent]
        return [{"type": "response.output_text.delta", "delta": json.dumps({"items": items}, ensure_ascii=False)},
                {"type": "response.completed", "response": {"id": "resp_SYNTHETIC", "usage": {}}}]


def client(translate) -> tuple[FakeClient, FakeTranslator]:
    fake = FakeTranslator(translate)
    return FakeClient(fake), fake


def faithful(_id, text):
    """A stand-in translation that keeps numbers, quotes and markers, as the instructions ask."""
    return "[ES] " + text


def sent_text(fake: FakeTranslator) -> str:
    return json.dumps([b["input"] for b in fake.bodies], ensure_ascii=False)


# --- the checks on a translation -------------------------------------------------------------

@pytest.mark.parametrize("translation,reason", [
    ("Ваш план оставляет $6,840.00 к оплате.", None),
    ("Ваш план оставляет $6,480.00 к оплате.", "numbers changed"),
    ("Ваш план оставляет 6 840,00 $ к оплате.", "numbers changed"),
    ("", "empty"),
])
def test_numbers_must_come_back_as_written(translation, reason):
    assert i18n.check_translation("Your plan leaves $6,840.00 for me to pay.", translation) == reason


def test_quoted_rule_stays_in_english():
    src = "My plan document says (page 2): “Care for a newborn child is covered.”"
    assert i18n.check_translation(src, "В документе плана (стр. 2): “Care for a newborn child is covered.”") is None
    assert i18n.check_translation(src, "В документе плана (стр. 2): «Уход за новорождённым покрывается».") \
        .startswith("quote changed")


# --- letters ---------------------------------------------------------------------------------

def test_letter_translation_hides_private_values_and_puts_them_back():
    c = test_appeal.case()
    [letter] = writer.appeal_letters(c)
    cl, fake = client(faithful)
    tr = i18n.letter(cl, c, letter, "es")
    out = sent_text(fake)
    for private in ("Maria Sample", "SHP-000-4417"):
        assert private in " ".join([letter.subject] + [p.text for p in letter.paragraphs])
        assert private not in out  # never sent
    assert "⟦V" in out
    assert "SHP-000-4417" in tr.subject and any("Maria Sample" in p for p in tr.paragraphs)
    assert tr.note.startswith("Traducción para entender") and not tr.failed
    body = fake.bodies[0]
    assert body["model"] == "reader" and body["store"] is False and "Spanish" in body["input"][0]["content"][0]["text"]


def test_a_paragraph_whose_numbers_change_stays_in_english():
    c = test_appeal.case()
    [letter] = writer.appeal_letters(c)
    target = next(i for i, p in enumerate(letter.paragraphs) if "$6,840.00" in p.text)
    cl, _ = client(lambda i, t: t.replace("6,840.00", "684.00") if i == f"p{target}" else "[RU] " + t)
    tr = i18n.letter(cl, c, letter, "ru")
    assert tr.paragraphs[target] == letter.paragraphs[target].text  # kept in English
    assert tr.failed == [f"p{target}: numbers changed"]
    assert "перевод не прошёл проверку" in tr.note
    assert all(p.startswith("[RU]") for i, p in enumerate(tr.paragraphs) if i != target)


def test_missing_item_stays_in_english():
    c = test_appeal.case()
    [letter] = writer.appeal_letters(c)

    class Drops(FakeTranslator):
        def events(self, body):
            evs = super().events(body)
            data = json.loads(evs[0]["delta"])
            data["items"] = [x for x in data["items"] if x["id"] != "subject"]
            evs[0]["delta"] = json.dumps(data)
            return evs

    tr = i18n.letter(FakeClient(Drops(faithful)), c, letter, "es")
    assert tr.subject == letter.subject and "subject: missing" in tr.failed


def test_english_needs_no_model_call():
    c = test_appeal.case()
    [letter] = writer.appeal_letters(c)
    cl, fake = client(faithful)
    tr = i18n.letter(cl, c, letter, "en")
    assert fake.bodies == [] and tr.paragraphs == [p.text for p in letter.paragraphs]


def test_live_translation_only_for_a_fictional_case():
    c = test_appeal.case()
    c.demo = False
    live = FakeClient(None, live=True)
    with pytest.raises(RealDataGate):
        i18n.translate(live, c, {"x": "Hello"}, "es")


def test_unknown_language_is_refused():
    with pytest.raises(ValueError):
        i18n.translate(client(faithful)[0], test_appeal.case(), {"x": "Hello"}, "fr")


# --- findings, copy, PDF -----------------------------------------------------------------------

def test_findings_are_keyed_like_the_checker():
    c = test_appeal.case()
    r = checks.run(c)
    cl, _ = client(faithful)
    tr = i18n.findings(cl, c, r, "es")
    assert "F1 #1:title" in tr.texts and tr.texts["F1 #1:title"].startswith("[ES] ")
    assert len(tr.texts) == 2 * (len(r.findings) + len(r.no_money))


def test_copy_of_a_translation_starts_with_its_note():
    c = test_appeal.case()
    [letter] = writer.appeal_letters(c)
    tr = i18n.letter(client(faithful)[0], c, letter, "ru")
    text = i18n.plain_text(tr)
    assert text.startswith("Перевод для понимания. Отправляется письмо на английском.") and text.endswith("\n")


def test_letter_pdf_with_its_translation():
    c = test_appeal.case()
    [letter] = writer.appeal_letters(c)
    tr = i18n.letter(client(lambda i, t: "[RU] Перевод: " + t)[0], c, letter, "ru")
    pages = [p.extract_text() for p in PdfReader(io.BytesIO(report_pdf.letter_pdf(c, letter, tr))).pages]
    first = re.sub(r"\s+", " ", " ".join(pages[:-1]))
    assert "Subject: Appeal of denied claim CLM-7765301" in first and "Maria Sample" in first
    assert "Sincerely," in first and "12 Example Lane" in first and "SAMPLE - FICTIONAL DATA" in first
    assert "Перевод для понимания" in pages[-1] and "Перевод:" in pages[-1]


def test_a_translation_with_a_stray_script_is_refused():
    """Lightning wrote "от Федеральных指南 2026 года" twice on two runs: such a paragraph is not taken."""
    from billradar.i18n import check_translation
    src = "212.3% of the 2026 Federal Poverty Guidelines"
    assert check_translation(src, "212.3% от Федеральных指南 2026 года").startswith("stray script")
    assert check_translation(src, "212.3% от федеральной черты бедности 2026 года") is None  # must differ
    assert check_translation("Total ⟦V1⟧ €5", "Итого ⟦V1⟧ €5") is None
