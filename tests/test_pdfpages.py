"""PDF pages read as images and joined back. SYNTHETIC documents."""

from decimal import Decimal

from billradar import pdfpages
from billradar.case import DocKind, Document, Line, Source


def _page(doc_id, kind, lines, **kw):
    return Document(doc_id=doc_id, source=Source.PAPER, kind=kind, lines=lines, **kw)


def test_pages_of_one_file_join_back_into_one_document():
    docs = [
        _page("S1", DocKind.EOB, []),
        _page("D1.2", DocKind.OTHER, [Line(line_no=1, code="ZZ102", charges=Decimal("5"))], page_no=2, page_count=2),
        _page("D1.1", DocKind.ITEMIZED_BILL, [Line(line_no=1, code="ZZ101", charges=Decimal("9"))],
              account_ref="ACCT_1", patient="PATIENT_1", page_no=1, page_count=2),
        _page("D2.1", DocKind.HOSPITAL_STATEMENT, [], patient_balance=Decimal("12.00")),
    ]
    out = pdfpages.merge_pages(docs)
    assert [d.doc_id for d in out] == ["S1", "D1", "D2"]
    d1 = out[1]
    assert d1.source == Source.PDF and d1.kind == DocKind.ITEMIZED_BILL and d1.account_ref == "ACCT_1"
    assert [ln.code for ln in d1.lines] == ["ZZ101", "ZZ102"] and [ln.line_no for ln in d1.lines] == [1, 2]
    assert (d1.page_no, d1.page_count) == (1, 2)
    assert out[2].patient_balance == Decimal("12.00") and out[0].source == Source.PAPER


def test_printed_line_numbers_stay_when_they_do_not_repeat():
    docs = [_page("D1.1", DocKind.ITEMIZED_BILL, [Line(line_no=1), Line(line_no=2)]),
            _page("D1.2", DocKind.ITEMIZED_BILL, [Line(line_no=3)])]
    assert [ln.line_no for ln in pdfpages.merge_pages(docs)[0].lines] == [1, 2, 3]


def test_missing_renderer_is_an_error_not_a_silent_skip(monkeypatch):
    import pytest
    monkeypatch.setenv("BILLRADAR_PDFTOPPM", "")
    monkeypatch.setattr(pdfpages.shutil, "which", lambda _: None)
    with pytest.raises(pdfpages.RenderError):
        pdfpages.render(b"%PDF-1.4")
