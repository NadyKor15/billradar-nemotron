"""Comparison sheets for review: each document type as generated (page + phone-photo version)
with the list of layout features it reproduces. Only generated, fictional documents are shown.

    python -m gen.compare packs-real/S1-birth-all-errors compare      (run from samples/)
"""
from __future__ import annotations

import os
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

from .photo import page_image

FONT_CANDIDATES = ["/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]

SHEETS = [
    ("02-hospital-itemized-mother", "B. Детальный счёт больницы (4 стр.)", [
        "Шрифт Arial 11, сетка колонок Svc Dt / Rev Code / Px Code / Description / Qty / Amt — по оригиналу",
        "Голубая шапка таблицы, тонкие синие линии строк, перенос «Rev / Code»",
        "Длинный Px Code переносит последнюю цифру на вторую строку",
        "Тёмно-синяя пустая плашка над «Current Account Balance»",
        "Summary by Rev Code: подписи переносятся и «уезжают» от сумм, как в оригинале",
        "Стр. 3 «SUMMARY OF ACCOUNT AS OF…» с форматами $-1,234.00 и $ 0.00; стр. 4 — врачи и подрядчики",
        "Подвал Account Number / Print Date / Page N of 4 и строка про лицензию"]),
    ("06-eob-june", "C. EOB страховой (13 колонок)", [
        "Шапка: адрес слева, знак и «Explanation of Benefits» справа, красное THIS IS NOT A BILL",
        "Карточки Customer Service / Participant Information со скруглёнными углами",
        "Блок claim: Claim # и Patient слева, тёмная плашка Provider / Member ID справа",
        "13 колонок с двухстрочными заголовками, даты диапазоном на двух строках",
        "Claim Totals, Patient Responsibility, тёмная «таблетка» Your Responsibility",
        "Легенды Service Code / Reason Code на последней странице, Page N of M и дата внизу",
        "DataMatrix и вертикальная служебная строка на левом поле"]),
    ("01-hospital-statement", "A. Сводная выписка больницы (шаблон 2026)", [
        "Купон оплаты картой справа сверху с тёмными плашками",
        "Плашки Guarantor / Send Payment To, линия отрыва из подчёркиваний",
        "Список «How can I pay my balance?» с круглыми маркерами",
        "Таблица Hospital Services Detail: зелёная строка страховой, синяя Patient Balance",
        "Таблица Summary с Outstanding / Insurance / Patient Balance",
        "Таблица подрядчиков переходит на вторую страницу",
        "Генератор умеет и шаблон 2024 (две учётные записи, тёмные шапки, Balance Forward)"]),
    ("08-anesthesia-bill", "D1. Счёт анестезиолога (3 стр.)", [
        "Знак из четырёх квадратов, DO NOT MAIL PAYMENTS HERE, строка STMT ▲ над адресом",
        "Крупный засечковый заголовок «Your total is $…», карточка сумм со стрелкой-сгибом",
        "Красная полоса с дополнительной информацией, синяя — «Difficulty paying your bill?»",
        "Линия отрыва с ножницами, блок Ways to Pay с QR, Need Help?",
        "Стр. 2: визит, строки услуг (дубль строки анестезии в пачке с ошибкой), серые панели, купон и OCR-строка",
        "Стр. 3: Your bill summary"]),
    ("09-obgyn-statement", "D2. Выписка акушера (2 листа)", [
        "Серые шапки, рамки PROVIDER(S) / PATIENT NAME / BILL DATE / ACCOUNT NO.",
        "Ledger без линий: Claim, услуга, платежи и корректировки в одной колонке",
        "«Your Balance Due On These Services...» по каждому claim, кредит в скобках ($…)",
        "Купон DATE / PATIENT NAME / ACCOUNT NO. / PAY THIS AMOUNT, префикс 300_ у получателя",
        "Плашка IMPORTANT MESSAGE REGARDING YOUR ACCOUNT и способы оплаты"]),
    ("10-pediatric-statement", "D3. Выписка педиатра", [
        "Бирюзовые плашки и таблица Guarantor Name / Patient Account Num / … / Amount Due",
        "Адреса шрифтом Courier, почтовые штрихкоды IMb, DataMatrix",
        "Баннер STATEMENT, жёлтая строка пациента, строки платежей, предупреждение про Collections",
        "PAYMENT OPTIONS с QR, стрелка PAY THIS AMOUNT, Upon Receipt"]),
    ("11-neonatal-letter", "E. Письмо «this is not a bill»", [
        "Шапка организации с засечками, знак из трёх колец справа",
        "Почтовые штрихкоды и окно конверта с двумя адресами",
        "Текст Courier: Account Number / Balance / Patient Name, сообщение об отказе страховой и срок",
        "Испанская строка и технический код в подвале"]),
]

FIXED = ("Что отличается намеренно: все организации, люди, адреса, телефоны (555-01xx), номера и сайты (.example) — вымышленные; "
         "логотипы нарисованы заново; внизу каждого листа мелкая серая строка «FICTIONAL TEST DOCUMENT».")


def _font(size):
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) > max_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    return lines


def sheet(pack, stem, title, bullets, out_path):
    print_pdf = pack / "print" / f"{stem}.pdf"
    page = page_image(print_pdf, 0, dpi=110)
    photos = sorted(pack.glob(f"{stem}-p*.jpg"))
    W, H = 2400, 1700
    im = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(im)
    d.text((40, 30), title, fill=(20, 20, 20), font=_font(44))
    d.text((40, 90), f"Пачка: {pack.name}   ·   файл: {stem}", fill=(90, 90, 90), font=_font(24))
    pw = 900
    ph = int(page.height * pw / page.width)
    im.paste(page.resize((pw, ph)), (40, 140))
    d.rectangle([40, 140, 40 + pw, 140 + ph], outline=(160, 160, 160))
    d.text((40, 150 + ph), "Копия (PDF для печати)", fill=(90, 90, 90), font=_font(22))
    if photos:
        ph_img = Image.open(photos[0])
        w2 = 520
        h2 = int(ph_img.height * w2 / ph_img.width)
        if h2 > ph:
            h2 = ph
            w2 = int(ph_img.width * h2 / ph_img.height)
        im.paste(ph_img.resize((w2, h2)), (980, 140))
        d.text((980, 150 + h2), "Та же копия «фото с телефона»", fill=(90, 90, 90), font=_font(22))
    x0 = 1560
    y = 150
    d.text((x0, y), "Что повторяет оригинал:", fill=(20, 20, 20), font=_font(28))
    y += 50
    f = _font(23)
    for b in bullets:
        lines = _wrap(d, b, f, W - x0 - 60)
        d.text((x0, y), "•", fill=(20, 20, 20), font=f)
        for ln in lines:
            d.text((x0 + 24, y), ln, fill=(30, 30, 30), font=f)
            y += 32
        y += 12
    y += 20
    for ln in _wrap(d, FIXED, _font(21), W - x0 - 60):
        d.text((x0, y), ln, fill=(110, 60, 20), font=_font(21))
        y += 29
    im.save(out_path, optimize=True)
    return out_path


def main(argv=None):
    argv = argv or sys.argv[1:]
    pack, out = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for i, (stem, title, bullets) in enumerate(SHEETS, 1):
        print(sheet(pack, stem, title, bullets, out / f"{i:02d}-{stem}.png"))


if __name__ == "__main__":
    main()
