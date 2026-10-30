"""Checks on the look-alike sample generator: numbers agree across documents, planted errors are
really on the pages that stay in the pack, and every sheet says it is fictional."""
import json
import pathlib
import re
import sys

import fitz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from gen import build, claims  # noqa: E402
from gen.scenario import build_case  # noqa: E402


def _text(pdf):
    return "\n".join(pg.get_text() for pg in fitz.open(pdf))


def test_pack_with_all_errors(tmp_path):
    out, truth = build.build_pack("S1", build.SCENARIOS["brs1-birth-all-errors"], tmp_path)
    planted = {p["class"]: p for p in truth["planted"]}
    assert all(planted[c]["planted"] for c in ["F1", "E2", "B1", "E3", "E4", "P1"])
    # P1: the dropped EOB page exists in print/ but not as a photo, and carries no planted claim
    miss = planted["P1"]["missing_pages"][0]
    assert not (out / f"06-eob-june-p{miss}.jpg").exists()
    dropped = fitz.open(out / "print" / "06-eob-june.pdf")[miss - 1].get_text()
    for cn in planted["B1"]["claims"] + [planted["E2"]["claim_no"]]:
        assert cn not in dropped
    # E2: the anesthesia bill asks for twice the co-insurance of one line
    d1 = _text(out / "print" / "08-anesthesia-bill.pdf")
    assert f"Your total is ${2 * planted['E2']['extra_patient_cost']:,.2f}" in d1
    assert d1.count("CPT/HCPCS code: 01960") == 2
    # E4: same sheet shows $0.00 balance and the full PAY THIS AMOUNT
    sheet2 = fitz.open(out / "print" / "09-obgyn-statement.pdf")[1].get_text()
    assert "$0.00" in sheet2 and f"${planted['E4']['pay_this_amount']:,.2f}" in sheet2
    # E3: the credit is printed in brackets
    sheet1 = fitz.open(out / "print" / "09-obgyn-statement.pdf")[0].get_text()
    assert f"(${planted['E3']['credit']:,.2f})" in sheet1
    key = json.loads((out / "key.json").read_text())
    assert sorted(i["cls"] for i in key["items"]) == ["B1", "E2", "E3", "E4", "F1", "F2", "P1"]
    b1 = next(i for i in key["items"] if i["cls"] == "B1")
    assert b1["amount"] is None and b1["deadline"] is None
    assert next(i for i in key["items"] if i["cls"] == "F1")["sheets"] == ["D1", "D2"]
    assert (out / "table-1.jpg").exists() and len(truth["sheets"]) >= 12
    assert all(i["sheets"] for i in key["items"])


def test_control_pack_has_nothing_planted_and_balances_agree(tmp_path):
    spec = build.SCENARIOS["brs1-birth-clean-control"]
    out, truth = build.build_pack("S2", spec, tmp_path)
    assert [p["class"] for p in truth["planted"] if p["planted"]] == []
    assert json.loads((out / "key.json").read_text())["items"] == []
    case = build_case(spec["seed"], spec["errors"])
    cl = claims.build(case)
    # hospital statement patient balance == co-insurance on the paid hospital claim
    stmt = _text(out / "01-hospital-statement.pdf")
    assert f"${cl['hospital_claim'].patient_resp:,.2f}" in stmt
    assert round(case.mother_acct.due_from_patient, 2) == cl["hospital_claim"].patient_resp
    d1 = _text(out / "print" / "08-anesthesia-bill.pdf")
    assert d1.count("CPT/HCPCS code: 01960") == 1


def test_every_sheet_is_marked_fictional_and_uses_invented_names(tmp_path):
    out, _ = build.build_pack("S3", build.SCENARIOS["brs1-birth-newborn-duplicate"], tmp_path)
    for pdf in sorted((out / "print").glob("*.pdf")):
        for pg in fitz.open(pdf):
            assert "FICTIONAL TEST DOCUMENT" in pg.get_text(), pdf.name
        text = _text(pdf)
        for host in re.findall(r"(?:www\.|https?://|@)([a-z0-9.-]+\.[a-z]+)", text, re.I):
            assert host.endswith(".example"), (pdf.name, host)
    profile = json.loads((out / "profile.json").read_text())
    assert profile["fictional"] is True


def test_answers_fit_the_reader_schema_and_the_checks_find_what_was_planted(tmp_path):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    import jsonschema
    from datetime import date
    from billradar import checks, reader
    from billradar.case import Case, Profile, Source

    out, truth = build.build_pack("S1", build.SCENARIOS["brs1-birth-all-errors"], tmp_path)
    payload = json.loads((out / "answers" / "reading.json").read_text())
    jsonschema.validate(payload, reader.SCHEMA)
    ids = {d["doc_id"] for d in payload["documents"]}
    assert ids == set(truth["sheets"]) | {"D1", "D2", "D3"}
    corners = json.loads((out / "answers" / "sheets.json").read_text())
    assert sum(len(v) for v in corners.values()) == len(truth["sheets"])
    assert all(len(q) == 4 for quads in corners.values() for q in quads)
    expected = {i: Source.PAPER if i.startswith("S") else Source.PDF for i in ids}
    docs, problems = reader.to_documents(payload, expected)
    assert problems == []
    prof = json.loads((out / "profile.json").read_text())
    case = Case(case_id="S1", policy_id=prof["policy_id"], today=date.fromisoformat(prof["today"]),
                profile=Profile.model_validate(prof["profile"]), documents=docs)
    r = checks.run(case)
    kinds = {f.kind.value for f in r.findings + r.held_back}
    assert {"E2", "E3", "E4", "B1", "P1", "F1"} <= kinds
    f1 = [f for f in r.findings + r.held_back if f.kind.value == "F1" and f.evidence[0].doc_id == "D1"]
    key = {i["cls"]: i for i in json.loads((out / "key.json").read_text())["items"]}
    assert f1 and f"{f1[0].amount:.2f}" == key["F1"]["amount"]
