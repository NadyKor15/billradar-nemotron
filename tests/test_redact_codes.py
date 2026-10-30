"""Private mode covers what a mail room and a scanner print on a bill, not only what follows a label: the postal
IMb under the address, DataMatrix, QR and 1D codes, the mailing line over the name, the apartment number (Apt,
Unit, #), the ZIP+4, a line up the margin, an account number in the cell under its header, a personal statement
code. A SYNTHETIC bill printed by reportlab carries all of them; each test checks both sides: the leak gate finds
them on the page as printed (so it is not blind) and nothing on the covered copy. With a rule taken out, the gate
must refuse to send."""

import io
import re
import numpy as np
import shutil

import pytest
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from billradar import pdfpages, pipeline, redact
from billradar.redact import Vault, compact

pytestmark = [pytest.mark.filterwarnings("ignore::DeprecationWarning"),
              pytest.mark.skipif(not shutil.which("pdftoppm"), reason="no poppler")]

HOME = "4089 Coral Reef Way Apt 400"  # what the family typed in; the bill prints "# 400"
SEEN = ["h2px375m007494", "4788416", "pxvevrdx", "292006143", "330674632", "oakstapt4b", "palmaveunit9",
        "409798605", "847673833", "samplea", "annas", "418153782", "mrssample", "4106685", "237851g7", "77953",
        "480004440", "4744500976"]
KEEP = ["03212025", "99213", "123456", "67680", "179525"]  # date, service code, $1,234.56, $676.80, $1,795.25


def _code(c, kind, value, x, y, w, h, **kw):
    d = createBarcodeDrawing(kind, value=value, **kw)
    c.saveState()
    c.translate(x, y)
    c.scale(w / d.width, h / d.height)
    renderPDF.draw(d, c, 0, 0)
    c.restoreState()


def _bill() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _code(c, "ECC200DataMatrix", "H2PX375M007494001", 40, 720, 30, 30)
    c.setFont("Helvetica", 9)
    c.drawString(70, 680, "H2PX375M007494 HLNX")
    c.drawString(70, 664, "ANNA SAMPLE")
    c.drawString(70, 652, "4089 CORAL REEF WAY # 400")
    c.drawString(70, 640, "BAYSIDE FL 33067-4632")
    c.setFont("Helvetica", 6)
    c.drawString(20, 641, "4744 500976")  # the sort line printed small, left of the address, as on the Little Harbor coupon
    c.setFont("Helvetica", 9)
    _code(c, "USPS_4State", "01234567094987654321", 70, 622, 200, 12, routing="33067463240")
    c.drawString(330, 664, "Guarantor on file: 12 Oak St Apt 4B")
    c.drawString(330, 652, "Previous address: 77 Palm Ave Unit 9")
    c.drawString(330, 700, "Patient Account Num")
    c.drawString(330, 688, "4788416")
    c.drawString(70, 590, "Pay online with your personal statement code - PxvEVrdx")
    c.drawString(70, 520, "PATIENT NAME  Sample , Anna")
    c.drawString(70, 505, "Anna's visit on February 11, 2025 insurance applied")
    c.drawString(70, 490, "Guarantor # 409798605")
    c.drawString(70, 475, "Guarantor ID: 409798605")
    c.drawString(70, 440, "Statement Date      Due Date      Guarantor #      Amount Due")
    c.drawString(70, 426, "04/09/25            05/09/25      847673833        $1,795.25")
    c.drawString(70, 375, "Dear Mrs. Sample, thank you for your payment.")
    c.drawString(70, 360, "11/25/2024   Claim:4106685, Provider: Marit Halversen, MD")
    c.drawString(70, 345, "STMT 237851G7")
    c.drawString(40, 708, "77953")  # the mail piece number under the DataMatrix, as on the Cradleview letter
    c.drawString(276, 624, "480004 - 440")  # sort numbers right after the IMb, as on the Little Harbor coupon
    c.drawString(70, 400, "Account Number:")
    c.drawString(300, 400, "CVX 418153782")
    c.drawString(70, 560, "03/21/2025   99213   Office visit, established patient   $1,234.56")
    c.drawString(70, 545, "Balance due $676.80")
    c.rect(440, 455, 160, 95)  # the payment box around the QR, its label joined to the code
    _code(c, "QR", "https://pay.example/b/PxvEVrdx", 450, 480, 60, 60)
    c.rect(450, 468, 60, 11, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.drawString(456, 471, "SCAN TO PAY")
    c.setFillColorRGB(0, 0, 0)
    _code(c, "Code128", "292006-143-69524025", 70, 90, 120, 22)
    c.drawString(400, 95, "292006-143-69524025")
    c.saveState()
    c.translate(24, 300)
    c.rotate(90)
    c.setFont("Helvetica", 7)
    c.drawString(0, 0, "HLNX - H2PX375M - 007494 - 001 OF 001")
    c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="module")
def page():
    return pdfpages.render(_bill())[0]


def _vault() -> Vault:
    return Vault(known=[("ADDR", HOME), *[("ADDR", v) for v in redact.city_line("Bayside", "FL", "33067")]],
                 seed={("PATIENT", "Anna Sample"): "[PATIENT_1]"})


def _text(img) -> str:
    return " ".join(compact(line.text) for line in redact.ocr_turned(img))


def test_the_gate_sees_every_kind_on_the_page_as_printed(page):
    """Must light: before covering, the codes, the mailing lines and the unit numbers are all found."""
    boxes = redact.codes(page)
    assert len(boxes) >= 4, boxes  # DataMatrix, QR, IMb, Code 128
    assert any(x1 - x0 > 8 * (y1 - y0) for x0, y0, x1, y1 in boxes), "the IMb under the address"
    v = _vault()
    assert v.units == {"400"}
    kinds = {s.kind for s in redact.leaks(page, [], v.units)}
    assert {"CODE", "MAIL", "ADDR"} <= kinds, kinds
    text = _text(page)
    assert all(x in text for x in SEEN + KEEP), [x for x in SEEN + KEEP if x not in text]


def test_nothing_of_it_is_readable_on_the_covered_copy(page):
    v = _vault()
    covered = redact.redact(page, v).image
    learned = {(s.kind, compact(s.value)) for s in v.secrets}
    assert {("ACCT", "4788416"), ("ACCESS", "pxvevrdx"), ("ACCT", "409798605"), ("ACCT", "847673833")} <= learned
    assert redact.codes(covered) == []
    assert redact.leaks(covered, v.secrets, v.units, v.name_words) == []
    text = _text(covered)
    assert [x for x in SEEN if x in text] == []
    assert "400" not in text.replace("4089", "")  # the apartment number, wherever it was printed
    assert all(x in text for x in KEEP), [x for x in KEEP if x not in text]  # the checks still read these
    assert pipeline._no_leaks(covered, v, "T1") is covered  # the gate sends it as is


def _without_codes(monkeypatch):
    monkeypatch.setattr(redact, "_cover_box", lambda *a, **k: None)


def _without_mail_lines(monkeypatch):
    spans = redact._spans
    monkeypatch.setattr(redact, "_spans", lambda t, v, learn=True: [x for x in spans(t, v, learn) if x[2].kind != "MAIL"])


def _without_units(monkeypatch):
    spans = redact._spans
    monkeypatch.setattr(redact, "_with_units", lambda address: [])
    monkeypatch.setattr(redact, "_units_left", lambda lines, units, anywhere=False: [])
    monkeypatch.setattr(redact, "_spans", lambda t, v, learn=True: [
        x for x in spans(t, v, learn) if not redact.STREET_UNIT.fullmatch(x[2].value)])


@pytest.mark.parametrize("take_out", [_without_codes, _without_mail_lines, _without_units],
                         ids=["codes", "mailing-lines", "apartment-numbers"])
def test_with_a_rule_taken_out_the_gate_refuses_to_send(page, monkeypatch, take_out):
    """The mutation check inside the suite: the covering loses one rule, the leak gate does not."""
    units = _vault().units  # what the family typed in stays known to the gate
    take_out(monkeypatch)
    v = _vault()
    covered = redact.redact(page, v).image
    v.units = units
    with pytest.raises(pipeline.RealDataGate):
        pipeline._no_leaks(covered, v, "T1")


def test_the_gate_learns_a_labelled_value_the_photo_pass_missed():
    """A cut sheet can print a value the whole-photo pass could not read: the gate learns it from the sheet itself
    and covers it before sending, it does not wave the sheet through because the value was not known yet."""
    img = pdfpages.render(_bill())[0]
    v = Vault()  # nothing known: as if the photo pass had read none of it
    sent = pipeline._no_leaks(img, v, "S1")
    assert sent is not img
    text = _text(sent)
    assert [x for x in ("4788416", "409798605", "847673833", "pxvevrdx", "h2px375m007494") if x in text] == []


def test_a_name_read_as_two_boxes_on_one_line_is_covered_whole():
    """OCR reads "Castillo , Daniela" on a payment coupon as two boxes; the last name must not stay readable."""
    import numpy as np
    from billradar.redact import Line
    v = _vault()
    box = lambda x0, x1: np.array([[x0, 100], [x1, 100], [x1, 120], [x0, 120]], dtype=np.float32)
    a, b = Line(box(100, 190), "Sample ,", 0.99), Line(box(200, 260), "Anna", 0.99)
    spans = redact._split_spans([a, b], v)
    assert {(line.text, start, end) for line, start, end, _ in spans} == {("Sample ,", 0, 6), ("Anna", 0, 4)}
    assert {s.token for *_, s in spans} == {"[PATIENT_1]"}


def test_a_number_half_under_a_cover_still_counts_as_readable():
    """Six of seven digits left beside a neighbouring cover are the account number all the same."""
    from PIL import Image, ImageDraw
    from billradar.redact import Secret
    img = Image.new("RGB", (900, 160), "white")
    d = ImageDraw.Draw(img)
    d.text((40, 50), "Patient 4788416", font=redact._font(40), fill="black")
    full = d.textlength("Patient 4788416", font=redact._font(40))
    d.rectangle((40 + full - 24, 40, 40 + full + 10, 110), fill="white")  # the last digit under a cover
    assert "478841" in _text(img) and "4788416" not in _text(img)  # must light: the instrument sees the part
    secret = Secret("ACCT", "4788416", "[ACCT_1]")
    assert redact.leaks(img, [secret]) == [secret]
    assert redact.leaks(img, [Secret("ACCT", "5550199", "[ACCT_2]")]) == []  # must differ: another number is not


def test_a_token_stays_inside_its_cover():
    """A long token over a short word must not spill onto the words beside or under it."""
    from PIL import Image, ImageDraw
    import numpy as np
    img = Image.new("RGB", (600, 120), "white")
    line = redact.Line(np.array([[100, 40], [160, 40], [160, 80], [100, 80]], dtype=np.float32), "Anna", 0.99)
    redact._cover(ImageDraw.Draw(img), line, 0, 4, "[PATIENT_12]")
    ink = np.asarray(img.convert("L")) < 200
    xs = np.where(ink.any(axis=0))[0]
    assert xs.max() <= 160 + 12, xs.max()


def test_a_number_labelled_on_a_later_sheet_is_covered_on_an_earlier_one():
    """Sheet 1 prints the account without a label, sheet 2 labels it: the copy of sheet 1 that leaves must not
    show it either (every paper is learned before any is checked)."""
    import io
    import json
    from pathlib import Path
    from PIL import Image, ImageDraw
    from billradar.case import Profile

    def jpeg(text):
        img = Image.new("RGB", (1200, 300), "white")
        ImageDraw.Draw(img).text((60, 120), text, font=redact._font(40), fill="black")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        return buf.getvalue()

    assert "44932556" in _text(Image.open(io.BytesIO(jpeg("Your balance on 44932556 is due"))))  # must light
    info = json.loads((Path(__file__).resolve().parents[1] / "eval/packs/work/brs1-birth-all-errors/profile.json").read_text())
    out = pipeline.cover_only([("1.jpg", jpeg("Your balance on 44932556 is due")), ("2.jpg", jpeg("Account Number: 44932556"))],
                              [], Profile.model_validate(info["profile"]))
    assert [c.blocked for c in out] == ["", ""]
    assert all("44932556" not in _text(c.image) for c in out)


def test_a_cover_stops_halfway_to_the_line_between():
    """S14 sets account number, balance and name tight: OCR boxes 26 px high on a 21 px pitch. Covering the
    account and the name must leave the "$275.00" between them readable (it had been wiped, so the reader lost
    the baby's bill). Boxes as OCR gave them on S14."""
    import numpy as np
    from PIL import Image, ImageDraw
    box = lambda x0, y0, x1, y1: np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)
    top = redact.Line(box(386, 556, 560, 582), "CVX418153782", 0.99)
    mid = redact.Line(box(412, 577, 509, 603), "$275.00", 0.99)
    low = redact.Line(box(437, 596, 651, 623), "CASTILLO,ADRIAN", 0.99)
    img = Image.new("RGB", (900, 700), "white")
    d = ImageDraw.Draw(img)
    d.text((414, 581), "$275.00", font=redact._font(18), fill="black")  # glyphs inside the middle box
    lines = [top, mid, low]
    crop = lambda im: im.crop((380, 560, 700, 620)).resize((960, 180))
    assert "27500" in _text(crop(img))  # must light
    redact._cover(d, top, 0, len(top.text), "[ACCT_1]", lines)
    redact._cover(d, low, 0, len(low.text), "[PATIENT_2]", lines)
    assert "27500" in _text(crop(img))


def test_an_id_glued_to_a_cover_that_clipped_its_label_is_covered():
    """A QR cover took the "S" of "STMT 237851G7" and OCR read "[CODETMT237851G7": the label is gone, the number
    is not. A tail of six digits or more glued to one of our tokens is covered; dates and amounts are not."""
    v = Vault()
    cut = lambda t: [t[a:b] for a, b, _ in redact._spans(t, v)]
    assert "237851G7" in cut("[CODETMT237851G7")
    assert cut("[CODE] $393.64") == [] and cut("[CODE] 02/11/2025") == [] and cut("[CODE] 1,490.25") == []


def test_a_faint_number_left_of_the_address_is_covered_without_ocr():
    """S8 printed a pale 6-point sort line "4744 500976" right before the city line; OCR could not read it there,
    so no text rule saw it. The cover reaches it by its ink, and the gate counts ink left of an address token."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (900, 160), "white")
    d = ImageDraw.Draw(img)
    d.text((70, 72), "4744 500976", font=redact._font(7), fill=(150, 150, 150))  # pale and tiny
    d.text((130, 60), "BAYSIDE FL 33067", font=redact._font(26), fill="black")
    v = _vault()
    covered = np.asarray(redact.redact(img, v).image.convert("RGB"), dtype=int)[55:100, 60:128]
    ink = ((covered.mean(axis=2) < 170) & ~((covered[..., 2] - covered[..., 0]) > 40)).sum()
    assert ink == 0, ink  # nothing printed left of the address survives (our blue tokens aside)
    left = img.copy()  # must light: the address covered by hand, the faint number left
    d2 = ImageDraw.Draw(left)
    d2.rectangle((125, 55, 520, 95), fill=(250, 250, 248))
    d2.text((130, 62), "[ADDR_2]", font=redact._font(18), fill=(40, 80, 160))
    assert {"ADDR", "MAIL"} & {s.kind for s in redact.leaks(left, v.secrets, v.units)}


def test_the_faint_number_is_caught_by_its_ink_when_ocr_reads_nothing(monkeypatch):
    """As on S8: OCR returns no line for the sort number at all. Only its pixels are left to go by."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (900, 160), "white")
    d = ImageDraw.Draw(img)
    d.text((70, 72), "4744 500976", font=redact._font(7), fill=(150, 150, 150))
    d.text((130, 60), "BAYSIDE FL 33067", font=redact._font(26), fill="black")
    read = redact.ocr
    monkeypatch.setattr(redact, "ocr", lambda im: [ln for ln in read(im) if not re.search(r"\\d{4} \\d{6}|4744", ln.text)])
    v = _vault()
    out = np.asarray(redact.redact(img, v).image.convert("RGB"), dtype=int)[55:100, 60:128]
    assert ((out.mean(axis=2) < 170) & ~((out[..., 2] - out[..., 0]) > 40)).sum() == 0
    left = img.copy()
    d2 = ImageDraw.Draw(left)
    d2.rectangle((125, 55, 520, 95), fill=(250, 250, 248))
    d2.text((130, 62), "[ADDR_2]", font=redact._font(18), fill=(40, 80, 160))
    assert "ADDR" in {s.kind for s in redact.leaks(left, v.secrets, v.units)}  # the gate sees ink, not text


def _small_code_page():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    c.drawString(90, 700, "Statement of services")
    c.drawString(90, 680, "03/21/2025   99213   Office visit   $1,234.56")
    _code(c, "ECC200DataMatrix", "SAMPLE0001", 30, 400, 22, 22)  # a small code on the left margin, as on S8
    c.showPage()
    c.save()
    return pdfpages.render(buf.getvalue())[0]


def test_a_code_that_shows_only_after_covering_is_covered_by_the_gate(monkeypatch):
    """On the clean pack's S8 the covering pass did not see a small DataMatrix that the leak check saw on the covered
    sheet (the covers moved the text height the finder sizes by). The gate covers it when only codes are left,
    instead of refusing a sheet it can fix."""
    page = _small_code_page()
    real_codes, real_redact = redact.codes, redact.redact
    assert real_codes(page)  # must light: the finder sees the small code on the page
    inside = {"on": False}

    def codes(img, lines=None):  # blind inside the covering pass, as on S8; the leak check sees it
        return [] if inside["on"] else real_codes(img, lines)

    def covering(img, vault, learn=True):
        inside["on"] = True
        try:
            return real_redact(img, vault, learn)
        finally:
            inside["on"] = False

    monkeypatch.setattr(redact, "codes", codes)
    monkeypatch.setattr(redact, "redact", covering)
    v = Vault()
    covered = redact.redact(page, v).image
    assert real_codes(covered)  # the covering pass left it
    sent = pipeline._no_leaks(covered, v, "S8")
    assert real_codes(sent) == []
    assert "99213" in _text(sent) and "123456" in _text(sent)  # the gate's covers touch nothing else


def test_a_service_code_in_the_legend_is_not_a_mail_number():
    """"76815" printed in a code legend near a covered barcode is the code billed on a claim line of the same sheet."""
    import numpy as np
    box = lambda x0, y0, x1, y1: np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)
    code = redact.Line(box(120, 140, 170, 160), "76815", 0.99)
    anchor = (40, 120, 100, 180)  # a covered DataMatrix beside it
    claim = [redact.Line(box(120, 400, 170, 420), "76815", 0.99), redact.Line(box(300, 400, 380, 420), "$332.00", 0.99)]
    assert redact._mail_numbers([code, *claim], [anchor], 20.0) == []
    assert redact._mail_numbers([code], [anchor], 20.0) == [code]  # must differ: alone, it is a mail number
