# Build log: look-alike sample papers (UTC)

Everything here is invented: families, hospital, insurer, doctors, services, codes, amounts, dates, phone numbers
(555-01xx) and web addresses (.example). Every sheet carries a small grey "SAMPLE - FICTIONAL TEST DOCUMENT" line.
Only the look (layout, fonts, columns, number formats, page mechanics) follows real-world papers of each kind.

## 2026-10-26

- 16:36 Generator `samples/gen/` with one module per kind of paper a family gets after a hospital birth:
  A hospital statement (2026 and 2024 templates), B itemized hospital bill (4 pages), C insurer EOB (13 columns,
  claims flow across pages, code legends), D1 anesthesia group bill (3 pages, OCR line), D2 OB/GYN statement
  (2 sheets), D3 pediatric statement, E "this letter is not a bill" from the newborn doctors' group.
  Services, charge lines, CPT/HCPCS and UB-04 revenue codes come from public code lists and general knowledge;
  reason codes and their wording are written for these samples. Stack: reportlab, pymupdf, Pillow.
- 16:36 Three work packs in the core's format, `eval/packs/work/brs1-*`: PDFs = D1..D3, photos = S1..Sn
  (single-sheet photos for the long EOB, "table" photos of 3-4 sheets lying apart), `profile.json` with
  `"fictional": true`, `key.json`, plus `truth.json` (what was planted, with amounts) and `print/` (every
  document as a PDF for printing).
  - brs1-birth-all-errors: F1, F2, E2, E3, E4, B1, P1.
  - brs1-birth-clean-control: nothing planted, income above every assistance level; the hospital patient
    balance equals deductible + co-insurance on the EOB.
  - brs1-birth-newborn-duplicate: F1, F2, E2, B1, P1.
- P1 guard: the EOB page left out of a pack is chosen automatically and may not carry a planted claim.
- Comparison sheets `samples/compare/*.png` (generated copies only, with the layout features they reproduce).
- Tests: `cd samples && python -m pytest tests/test_gen.py` (3 tests).
- 16:51 Ideal answers per pack in `answers/` (the CLI does not read it), for wiring runs marked REPLAYED:
  `reading.json` in the shape of `billradar/reader.SCHEMA` (S1..Sn sheets, D1..D3 PDFs, text as printed, EOB lines
  placed on the page where they are printed) and `sheets.json` (corners of every sheet on every photo, S order,
  clockwise from top-left). A test parses it with the core's reader and runs the checks: E2, E3, E4, B1, P1 and
  F1 come out on the all-errors pack, F1 with the key's amount. Itemized lines never repeat date + revenue code +
  amount by accident (that reads like a double charge).

Build: `cd samples && python -m gen.build` (deterministic per seed).
