import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from billradar import cli, pipeline, reader
from billradar.case import Profile, Source
from billradar.checks import Kind
from billradar.model_api import ModelError
from billradar.policies import source
from tests.fakes import QUADS, SIZE, FakeClient, FakeTable, reading_from_fixture, sheet_quad, table_photo

FIX = Path(__file__).parent / "fixtures" / "cases" / "anna-birth.json"
needs_text = pytest.mark.skipif(not all(source.is_cached(p) for p in source.SOURCES),
                                reason="run scripts/fetch_policies.py first")


def profile() -> Profile:
    return Profile.model_validate(json.loads(FIX.read_text())["profile"])


@needs_text
def test_photo_to_findings_end_to_end(tmp_path):
    fake = FakeTable(QUADS, SIZE, reading_from_fixture({"S6": "S5"}))
    client = FakeClient(fake, journal=tmp_path / "j.jsonl")
    out = pipeline.run(client, [table_photo(QUADS, SIZE)], [], profile(), "memorial-2026-05",
                       "Lakeview Demo Hospital", date(2026, 6, 15), "anna-birth")

    assert [s.doc_id for s in out.sheets] == ["S1", "S2", "S3", "S4", "S5"]
    assert not [p for p in out.problems if p.startswith("reader")]
    r = out.report
    assert {f.kind for f in r.findings} == {Kind.E1, Kind.F3, Kind.F2}
    assert r.total_may_come_off == Decimal("1100.86")
    assert r.apply_by == date(2026, 9, 2)

    body = fake.read_bodies[0]
    assert body["text"]["format"]["strict"] is True and body["store"] is False
    assert body["input"][0]["role"] == "developer"  # stable instructions first, for the prefix cache
    images = [c for c in body["input"][1]["content"] if c["type"] == "input_image"]
    assert len(images) == 5

    pipeline.save(out, tmp_path / "run")
    assert (tmp_path / "run" / "overlay-1.jpg").stat().st_size > 10_000
    assert len(list((tmp_path / "run" / "sheets").glob("S*.jpg"))) == 5


def test_reader_refuses_to_guess():
    payload = reading_from_fixture({"S6": "S5"})
    payload["documents"][0]["patient_balance"] = "about seven hundred"
    payload["documents"][0]["statement_date"] = "June 20th"
    payload["documents"].append(dict(payload["documents"][1], doc_id="S9"))
    payload["documents"].append(dict(payload["documents"][1]))
    expected = {f"S{i}": Source.PAPER for i in range(1, 7)}
    docs, problems = reader.to_documents(payload, expected)
    s1 = docs[0]
    assert s1.patient_balance is None and s1.statement_date is None
    text = " | ".join(problems)
    assert "not an amount" in text and "not a date" in text
    assert "unknown document 'S9'" in text and "S2 twice" in text and "did not return S6" in text
    assert docs[0].patient == "PATIENT_1"


def test_reader_answer_that_is_not_json_is_an_error(tmp_path):
    fake = FakeTable(QUADS, SIZE, "Sorry, I can't read these.")
    client = FakeClient(fake)
    with pytest.raises(ModelError, match="not JSON"):
        pipeline.run(client, [table_photo(QUADS, SIZE)], [], profile(), "memorial-2026-05")


def test_schema_fits_the_strict_subset():
    def walk(node, path="root"):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, path
            assert set(node["required"]) == set(node["properties"]), path
            for k, v in node["properties"].items():
                walk(v, f"{path}.{k}")
        if node.get("type") == "array":
            walk(node["items"], f"{path}[]")
        assert not {"allOf", "oneOf", "$ref"} & set(node), path
    walk(reader.SCHEMA)


@needs_text
def test_cli_check(capsys):
    assert cli.main(["check", str(FIX)]) == 0
    out = capsys.readouterr().out
    assert "[F3]" in out and "$1,100.86" in out


def test_two_photos_number_sheets_across_both(tmp_path):
    left = [sheet_quad(600, 900, 520, 670, 3), sheet_quad(1500, 900, 520, 670, -2)]
    fake = FakeTable(left, SIZE, {"documents": []})
    client = FakeClient(fake)
    out = pipeline.run(client, [table_photo(left, SIZE), table_photo(left, SIZE)], [], profile(), "memorial-2026-05")
    assert [s.doc_id for s in out.sheets] == ["S1", "S2", "S3", "S4"]
    assert any("did not return S1" in p for p in out.problems)


def test_live_calls_need_a_fictional_pack(monkeypatch):
    from billradar.pipeline import RealDataGate
    fake = FakeTable(QUADS, SIZE, {"documents": []})
    client = FakeClient(fake, live=True)
    with pytest.raises(RealDataGate):
        pipeline.run(client, [table_photo(QUADS, SIZE)], [], profile(), "memorial-2026-05")
    assert fake.read_bodies == [] and fake.sheet_bodies == []  # nothing left the machine
    pipeline.run(client, [table_photo(QUADS, SIZE)], [], profile(), "memorial-2026-05", fictional=True)
    assert fake.sheet_bodies


def test_lines_without_a_date_take_the_one_visit_in_the_sheet_heading():
    """S10 prints "Daniela's visit on February 11, 2025" over two lines with no date of their own: read on the
    device from the image the reader got, the lines get that date. Two visits on a sheet fill nothing."""
    from datetime import date
    from PIL import Image, ImageDraw
    from billradar import pipeline, redact
    from billradar.case import Document, DocKind, Line, Source

    def sheet(*texts):
        img = Image.new("RGB", (1200, 120 + 70 * len(texts)), "white")
        for i, t in enumerate(texts):
            ImageDraw.Draw(img).text((60, 60 + 70 * i), t, font=redact._font(36), fill="black")
        return type("Sheet", (), {"doc_id": "S10", "image": img})()

    doc = Document(doc_id="S10", source=Source.PAPER, kind=DocKind.ITEMIZED_BILL,
                   lines=[Line(line_no=1, code="01960", charges="4944.00"), Line(line_no=2, code="01960", charges="4944.00")])
    problems = []
    [out] = pipeline._visit_dates([doc], [sheet("[PATIENT_1]'s visit on February 11, 2025")], problems)
    assert [ln.service_date for ln in out.lines] == [date(2025, 2, 11)] * 2 and problems
    [same] = pipeline._visit_dates([doc], [sheet("Visit on February 11, 2025", "Visit on March 2, 2025")], [])
    assert [ln.service_date for ln in same.lines] == [None, None]  # must differ: two visits, no guess


def test_a_line_printed_twice_but_read_once_is_read_again_alone():
    """The device sees code 01960 twice on the sheet next to an amount, the reading has it once: that sheet is read
    again on its own and the second reading, which has both, is kept."""
    import json
    from PIL import Image, ImageDraw
    from billradar import pipeline, redact
    from billradar.case import Document, DocKind, Line, Source

    img = Image.new("RGB", (1200, 300), "white")
    for i in range(2):
        ImageDraw.Draw(img).text((60, 60 + 80 * i), "02/11/2025   01960   $4,944.00   $196.82", font=redact._font(36), fill="black")
    sheet = type("Sheet", (), {"doc_id": "S1", "image": img})()
    line = lambda n: {"line_no": n, "code": "01960", "charges": "4944.00", "patient_responsibility": "196.82"}
    asked = []

    class Client:
        def respond(self, body, label=""):
            asked.append(body)
            doc = {"doc_id": "S1", "kind": "eob", "lines": [line(1), line(2)]}
            return type("R", (), {"text": json.dumps({"documents": [doc]}), "usage": {}, "seconds": 0.0, "replayed": True})()

    once = Document(doc_id="S1", source=Source.PAPER, kind=DocKind.EOB,
                    lines=[Line(line_no=1, code="01960", charges="4944.00", patient_responsibility="196.82")])
    problems = []
    [out] = pipeline._second_look(Client(), [once], [sheet], problems)
    assert len(asked) == 1 and len(out.lines) == 2 and "read again" in problems[0]
    twice = once.model_copy(update={"lines": [*once.lines, once.lines[0].model_copy(update={"line_no": 2})]})
    asked.clear()
    pipeline._second_look(Client(), [twice], [sheet], [])
    assert asked == []  # must differ: a reading that has both lines is not sent again


def test_a_doctors_statement_naming_the_hospital_is_billed_by_its_payee():
    """S12 is read as "Seaside Regional Hospital (IP) 300", its coupon says "MAKE CHECK PAYABLE TO: 300_Women
    First OB/GYN Care, LLC": the device reads the payee and the bill is the office's, not the hospital's."""
    from PIL import Image, ImageDraw
    from billradar import pipeline, redact
    from billradar.case import Document, DocKind, Source
    img = Image.new("RGB", (1400, 200), "white")
    ImageDraw.Draw(img).text((40, 80), "MAKE CHECK PAYABLE TO : 300_Women First OB/GYN Care, LLC", font=redact._font(34), fill="black")
    sheet = type("Sheet", (), {"doc_id": "S12", "image": img})()
    doc = Document(doc_id="S12", source=Source.PAPER, kind=DocKind.HOSPITAL_STATEMENT, provider="Seaside Regional Hospital (IP) 300")
    problems = []
    [out] = pipeline._office_from_coupon([doc], [sheet], "Seaside Regional Hospital", problems)
    assert out.provider == "Women First OB/GYN Care, LLC" and problems
    hosp = doc.model_copy(update={"doc_id": "D1"})
    [same] = pipeline._office_from_coupon([hosp], [type("Sheet", (), {"doc_id": "D1", "image": Image.new("RGB", (400, 100), "white")})()],
                                         "Seaside Regional Hospital", [])
    assert same.provider == hosp.provider  # must differ: no payee printed, the reading stands


def test_an_unnamed_bill_takes_the_office_printed_on_it_and_one_debt_is_counted_once():
    """S8 read with no provider is the pediatric office's bill, not the hospital's; D1 (guarantor number) and D2
    (account number) with the same patient and balance are one debt."""
    from datetime import date
    from decimal import Decimal
    from PIL import Image, ImageDraw
    from billradar import checks, pipeline, redact
    from billradar.case import Document, DocKind, Source
    img = Image.new("RGB", (1200, 200), "white")
    ImageDraw.Draw(img).text((40, 80), "Little Harbor Pediatrics - PA", font=redact._font(34), fill="black")
    s8 = Document(doc_id="S8", source=Source.PAPER, kind=DocKind.HOSPITAL_STATEMENT, provider="")
    [out] = pipeline._office_from_coupon([s8], [type("Sheet", (), {"doc_id": "S8", "image": img})()], "Seaside Regional Hospital", [])
    assert out.provider == "Little Harbor Pediatrics - PA"
    d1 = Document(doc_id="D1", source=Source.PDF, kind=DocKind.HOSPITAL_STATEMENT, account_ref="[ACCT_8]", patient="PATIENT_1",
                  patient_balance=Decimal("1555.23"), amount_due_coupon=Decimal("1555.23"), statement_date=date(2025, 4, 5))
    d2 = d1.model_copy(update={"doc_id": "D2", "account_ref": "[ACCT_1]", "amount_due_coupon": None, "statement_date": None})
    assert [b.doc_id for b in checks.one_per_account([d1, d2])] == ["D1"]


def test_an_eob_read_without_the_share_printed_on_the_row_is_read_again():
    """S1 prints "01960 $4,944.00 ... $196.82" twice; one reading left the share out on both lines, so the dispute
    lost its amount. The device sees two amounts on the code's row: the sheet is read again on its own."""
    import json
    from PIL import Image, ImageDraw
    from billradar import pipeline, redact
    from billradar.case import Document, DocKind, Line, Source
    img = Image.new("RGB", (1200, 300), "white")
    for i in range(2):
        ImageDraw.Draw(img).text((60, 60 + 80 * i), "02/11/2025   01960   $4,944.00   $196.82", font=redact._font(36), fill="black")
    sheet = type("Sheet", (), {"doc_id": "S1", "image": img})()
    asked = []

    class Client:
        def respond(self, body, label=""):
            asked.append(label)
            lines = [{"line_no": n, "code": "01960", "charges": "4944.00", "patient_responsibility": "196.82"} for n in (1, 2)]
            return type("R", (), {"text": json.dumps({"documents": [{"doc_id": "S1", "kind": "eob", "lines": lines}]}),
                                  "usage": {}, "seconds": 0.0, "replayed": True})()

    bare = Document(doc_id="S1", source=Source.PAPER, kind=DocKind.EOB,
                    lines=[Line(line_no=n, code="01960", charges="4944.00") for n in (1, 2)])
    problems = []
    [out] = pipeline._second_look(Client(), [bare], [sheet], problems)
    assert asked and all(ln.patient_responsibility for ln in out.lines) and "your share" in problems[0]
    asked.clear()
    pipeline._second_look(Client(), [out], [sheet], [])
    assert asked == []  # must differ: a reading with the share is not sent again


def test_an_insurance_statement_keeps_its_provider():
    """The office-name rule is for bills: on an EOB "A Product of Harborlight Health Florida, Inc." is the insurer,
    not the office that billed."""
    from PIL import Image, ImageDraw
    from billradar import pipeline, redact
    from billradar.case import Document, DocKind, Source
    img = Image.new("RGB", (1200, 200), "white")
    ImageDraw.Draw(img).text((40, 80), "A Product of Harborlight Health Florida, Inc.", font=redact._font(34), fill="black")
    eob = Document(doc_id="S1", source=Source.PAPER, kind=DocKind.EOB, provider="")
    [out] = pipeline._office_from_coupon([eob], [type("Sheet", (), {"doc_id": "S1", "image": img})()], "Seaside Regional Hospital", [])
    assert out.provider == ""
