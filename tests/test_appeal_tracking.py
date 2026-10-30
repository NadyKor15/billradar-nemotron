"""After "Send" (pack A): tracker, reminders, and the insurer's reply read from a photo.
The reply letter and the model answers are SYNTHETIC; the photo is drawn here and read by the real local OCR."""

import io
import json
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image, ImageDraw, ImageFont

from billradar import appeal, plans, writer
from billradar.pipeline import RealDataGate
from tests import test_appeal
from tests.fakes import FakeClient

PLAN = plans.load("sample-health-plan-2026")
REPLY_LINES = ["SAMPLE - FICTIONAL INSURER LETTER", "Sample Health Plan", "Claim CLM-7765301",
               "We have partially approved your appeal.", "You now owe $412.30 for this claim.",
               "You may ask for an external review within 4 months of this letter."]


def reply_photo() -> bytes:
    img = Image.new("RGB", (1500, 760), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    for i, line in enumerate(REPLY_LINES):
        d.text((60, 50 + i * 110), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


class Fake:
    def __init__(self, answer):
        self.answer, self.bodies = answer, []

    def events(self, body):
        self.bodies.append(body)
        return [{"type": "response.output_text.delta", "delta": json.dumps(self.answer)},
                {"type": "response.completed", "response": {"id": "resp_SYNTHETIC", "usage": {}}}]


def reply_model(answer):
    fake = Fake(answer)
    return FakeClient(fake), fake


ANSWER = {"decision": "partly_approved", "decision_quote": "We have partially approved your appeal.",
          "claim_ref": "CLM-7765301", "amount_you_owe": "$412.30", "date": "2026-11-20",
          "next_level_quote": "You may ask for an external review within 4 months of this letter.",
          "asks_for_quote": ""}


# --- tracker -------------------------------------------------------------------------------------

def tracked(tmp_path):
    c = test_appeal.case()
    [letter] = writer.appeal_letters(c)
    tr = appeal.Tracker()
    tr.prepare(c, letter, date(2026, 10, 27))
    return c, letter, tr


def test_prepared_appeal_carries_its_last_day(tmp_path):
    c, letter, tr = tracked(tmp_path)
    s = tr.status_on(letter.kind, date(2026, 10, 27))
    assert s["status"] == "prepared" and s["appeal_by"] == "2027-04-13" and s["days_left"] == 168
    assert tr.items[letter.kind].appeal_by_refs == [f"plan:{PLAN.plan_id}#appeal-180-days"]


def test_sent_then_waiting_day_n_of_30_and_back_from_disk(tmp_path):
    c, letter, tr = tracked(tmp_path)
    tr.mark_sent(c, letter.kind, date(2026, 10, 28), confirmation="SHP-APL-1234")
    item = tr.items[letter.kind]
    assert item.decide_by == date(2026, 12, 27) and item.decide_by_refs == [f"plan:{PLAN.plan_id}#decide-60-days"]
    assert tr.status_on(letter.kind, date(2026, 10, 28))["status"] == "sent"
    later = tr.status_on(letter.kind, date(2026, 11, 9))
    assert (later["status"], later["day"], later["of"]) == ("waiting", 12, 30)
    tr.save(tmp_path)
    again = appeal.Tracker.load(tmp_path)
    assert again.items[letter.kind].confirmation == "SHP-APL-1234"
    assert [h["status"] for h in again.items[letter.kind].history] == ["prepared", "sent"]


def test_prepare_twice_keeps_one_item(tmp_path):
    c, letter, tr = tracked(tmp_path)
    tr.prepare(c, letter, date(2026, 10, 29))
    assert len(tr.items) == 1 and len(tr.items[letter.kind].history) == 1


def test_reminders_calendar(tmp_path):
    c, letter, tr = tracked(tmp_path)
    before = appeal.reminders_ics(tr, c.case_id)
    assert "DTSTART;VALUE=DATE:20270413" in before and "TRIGGER:-P30D" in before and "TRIGGER:-P7D" in before
    tr.mark_sent(c, letter.kind, date(2026, 10, 28))
    after = appeal.reminders_ics(tr, c.case_id)
    assert "DTSTART;VALUE=DATE:20261127" in after  # check-in 30 days after sending
    assert "DTSTART;VALUE=DATE:20261227" in after  # the plan's own 60 days
    assert "20270413" not in after  # sent: the last day to appeal no longer needs a reminder
    assert "Not a legal deadline" in after and after.startswith("BEGIN:VCALENDAR")


# --- the reply -----------------------------------------------------------------------------------

def test_reply_read_and_checked_against_the_photo_by_local_ocr(tmp_path):
    c, letter, tr = tracked(tmp_path)
    client, fake = reply_model(ANSWER)
    r = appeal.read_reply(client, c, reply_photo(), PLAN)
    assert fake.bodies[0]["model"] == "reader" and fake.bodies[0]["store"] is False
    assert (r.decision, r.amount_you_owe, r.letter_date) == ("partly_approved", Decimal("412.30"), date(2026, 11, 20))
    assert r.decision_quote == ANSWER["decision_quote"] and not r.dropped
    assert r.next_step.startswith("Next level, in the plan's own words:") and "external review" in r.next_step
    tr.mark_sent(c, letter.kind, date(2026, 10, 28))
    tr.record_reply(letter.kind, r, date(2026, 11, 22))
    assert [h["status"] for h in tr.items[letter.kind].history] == ["prepared", "sent", "reply", "next"]
    assert tr.status_on(letter.kind, date(2026, 11, 22))["status"] == "next"


def test_words_not_on_the_photo_are_dropped():
    made_up = dict(ANSWER, decision="approved", decision_quote="Your appeal is fully approved.",
                   amount_you_owe="$0.00", next_level_quote="")
    client, _ = reply_model(made_up)
    r = appeal.read_reply(client, test_appeal.case(), b"jpeg", PLAN, ocr_text=lambda _: " ".join(REPLY_LINES))
    assert r.decision == "unclear" and r.amount_you_owe is None
    assert len(r.dropped) == 3
    assert r.next_step.startswith("We could not read a decision")


def test_denied_without_a_next_level_sentence_quotes_the_plan():
    lines = ["Sample Health Plan", "We have upheld the denial."]
    answer = dict(ANSWER, decision="denied", decision_quote="We have upheld the denial.", amount_you_owe="",
                  next_level_quote="", claim_ref="")
    r = appeal.read_reply(reply_model(answer)[0], test_appeal.case(), b"jpeg", PLAN, ocr_text=lambda _: " ".join(lines))
    frag = PLAN.fragment("external-review")
    assert frag.quote in r.next_step and r.next_refs == [frag.ref]


def test_live_reply_reading_only_for_a_fictional_case():
    c = test_appeal.case()
    c.demo = False
    live = FakeClient(None, live=True)
    with pytest.raises(RealDataGate):
        appeal.read_reply(live, c, b"jpeg", PLAN, ocr_text=lambda _: "")
