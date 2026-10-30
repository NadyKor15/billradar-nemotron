"""Voice note after the call. All model answers here are SYNTHETIC: a transcript as a speech model would
return it, and Responses events for the note. Token Factory has no speech model, so NemotronClient refuses
the transcription step and the screen asks the family to type the note."""

import io
import json
import wave

import pytest

from billradar import nemotron_client as nc
from billradar import voice, writer
from billradar.nemotron_client import NemotronClient, ReplayTransport
from billradar.pipeline import RealDataGate
from tests import test_appeal
from tests.fakes import FakeClient

ES_NOTE = ("Hablé con Morgan en facturación. El número de referencia es 48213. "
           "Me dijeron que van a revisar la cuenta en diez días hábiles.")


def wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeSpeech:
    """A speech model that returns one transcript, and remembers what it was asked."""

    def __init__(self, transcript: str):
        self.transcript, self.requests = transcript, []

    def transcribe(self, request: dict, audio: bytes) -> dict:
        self.requests.append((request, len(audio)))
        return {"sessionId": "SYNTHETIC", "transcript": f"  {self.transcript} ", "audioDurationMs": 9000, "turns": []}


class FakeNote:
    def __init__(self, answer: dict):
        self.answer = answer
        self.bodies = []

    def events(self, body):
        self.bodies.append(body)
        return [{"type": "response.output_text.delta", "delta": json.dumps(self.answer)},
                {"type": "response.completed", "response": {"id": "resp_SYNTHETIC", "usage": {}}}]


def note_model(answer: dict) -> tuple[FakeClient, FakeNote]:
    fake = FakeNote(answer)
    return FakeClient(fake), fake


# --- transcription -------------------------------------------------------------------------

def test_request_follows_the_documented_shape():
    req = voice.transcribe_request("es", ["Lakeview Demo Hospital"])
    assert req == {"model": "speech-to-text", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK",
                   "languageBias": ["Spanish"], "keywords": ["Lakeview Demo Hospital"]}


def test_russian_is_refused_with_a_reason():
    with pytest.raises(voice.LanguageNotSupported, match="no 'ru'"):
        voice.transcribe_request("ru", [])


def test_keywords_never_carry_a_family_name():
    c = test_appeal.case()
    words = " ".join(voice.keywords(c))
    assert "Lakeview Demo Hospital" in words and "Sample Health Plan" in words
    assert "Maria" not in words and "Leo" not in words and "SHP-000" not in words


def test_nemotron_has_no_speech_model_and_says_so():
    client = NemotronClient(transport=ReplayTransport(), record_to=None, journal=None)
    with pytest.raises(nc.NotOnNemotron, match="type the note"):
        voice.transcribe(client, test_appeal.case(), wav(), "en")


def test_transcript_from_a_speech_model_never_carries_the_audio():
    audio = wav()
    speech = FakeSpeech(ES_NOTE)
    t = voice.transcribe(FakeClient(speech), test_appeal.case(), audio, "es")
    assert t.text == ES_NOTE and t.replayed and t.duration_ms == 9000
    (req, size), = speech.requests
    assert size == len(audio) and req["languageBias"] == ["Spanish"] and "Maria" not in json.dumps(req)


def test_live_voice_only_for_a_fictional_case():
    c = test_appeal.case()
    c.demo = False
    live = FakeClient(None, live=True)
    with pytest.raises(RealDataGate):
        voice.transcribe(live, c, wav(), "en")
    with pytest.raises(RealDataGate):
        voice.read_note(live, c, voice.Transcript(ES_NOTE, "es"), "Lakeview Demo Hospital")


# --- what was said -------------------------------------------------------------------------

GOOD = {"rep_name": {"value": "Morgan", "quote": "Hablé con Morgan en facturación."},
        "reference": {"value": "48213", "quote": "El número de referencia es 48213."},
        "promises": [{"quote": "van a revisar la cuenta en diez días hábiles",
                      "english": "they will review the account within ten business days"}]}


def test_note_keeps_the_transcripts_own_words():
    client, fake = note_model(GOOD)
    c = test_appeal.case()
    note = voice.read_note(client, c, voice.Transcript(ES_NOTE, "es"), "Lakeview Demo Hospital")
    assert (note.rep_name, note.reference, len(note.promises), note.dropped) == ("Morgan", "48213", 1, [])
    body = fake.bodies[0]
    assert body["model"] == "reader" and body["store"] is False
    p = note.paragraph(c, "es").text
    assert p.startswith("On October 27, 2026 I called Lakeview Demo Hospital and spoke with Morgan "
                        "(reference number 48213).")
    assert "“van a revisar la cuenta en diez días hábiles”" in p and "my translation" in p


def test_words_not_in_the_note_are_dropped():
    bad = {"rep_name": {"value": "Taylor", "quote": "I spoke with Taylor."},
           "reference": {"value": "99999", "quote": "El número de referencia es 48213."},
           "promises": [{"quote": "they will waive the whole bill", "english": "they will waive the whole bill"},
                        {"quote": "van a revisar la cuenta en diez días hábiles",
                         "english": "they will review the account within 10 business days and refund $500"}]}
    client, _ = note_model(bad)
    note = voice.read_note(client, test_appeal.case(), voice.Transcript(ES_NOTE, "es"), "Lakeview Demo Hospital")
    assert note.rep_name == "" and note.reference == "" and note.promises == []
    assert len(note.dropped) == 4
    assert note.paragraph(test_appeal.case(), "es") is None


def test_english_note_has_no_translation_line():
    en = "I talked to Morgan. Reference number 48213. They said they will send the itemized bill this week."
    client, _ = note_model({"rep_name": {"value": "Morgan", "quote": "I talked to Morgan."},
                       "reference": {"value": "", "quote": ""},
                       "promises": [{"quote": "they will send the itemized bill this week",
                                     "english": "they will send the itemized bill this week"}]})
    note = voice.read_note(client, test_appeal.case(), voice.Transcript(en, "en"), "Lakeview Demo Hospital")
    p = note.paragraph(test_appeal.case(), "en").text
    assert "my translation" not in p and "reference number" not in p


def test_call_paragraph_goes_before_the_closing_line():
    c = test_appeal.case()
    [letter] = writer.appeal_letters(c)
    client, _ = note_model(GOOD)
    para = voice.read_note(client, c, voice.Transcript(ES_NOTE, "es"), "Sample Health Plan").paragraph(c, "es")
    out = voice.with_call_note(letter, para)
    assert out.paragraphs[-2] is para and out.paragraphs[-1].text.startswith("Please send your decision")
    assert len(out.paragraphs) == len(letter.paragraphs) + 1 and out.kind == letter.kind
    assert letter.paragraphs[-2] is not para  # the original letter is unchanged
