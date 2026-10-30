"""Languages: English, Russian, Spanish.

Our own fixed wording (headings, labels, buttons we print) is translated here by hand, so no model
writes it and nothing in it can drift. Letters are sent in English; a translation of a letter is
for the family to understand it and is labelled that way.
"""

from __future__ import annotations

LANGS = {"en": "English", "ru": "Русский", "es": "Español"}
NAMES_IN_ENGLISH = {"en": "English", "ru": "Russian", "es": "Spanish"}  # for model instructions
DEFAULT = "en"

STRINGS: dict[str, dict[str, str]] = {
    "report.title": {"en": "BillRadar report", "ru": "Отчёт BillRadar", "es": "Informe de BillRadar"},
    "report.case": {"en": "Case", "ru": "Дело", "es": "Caso"},
    "report.made": {"en": "Made on", "ru": "Составлен", "es": "Preparado el"},
    "report.sample": {"en": "SAMPLE - FICTIONAL DATA", "ru": "ОБРАЗЕЦ — ВЫМЫШЛЕННЫЕ ДАННЫЕ",
                      "es": "MUESTRA - DATOS FICTICIOS"},
    "report.up_to": {"en": "Up to {amount} may come off your bills",
                     "ru": "До {amount} могут снять с ваших счетов",
                     "es": "Hasta {amount} podrían descontarse de sus facturas"},
    "report.nothing": {"en": "We found no money to take off these bills",
                       "ru": "Мы не нашли сумм, которые можно снять с этих счетов",
                       "es": "No encontramos dinero que se pueda descontar de estas facturas"},
    "report.apply_by": {"en": "Apply for financial assistance by {date}",
                        "ru": "Подать на финансовую помощь до {date}",
                        "es": "Solicite la asistencia financiera antes del {date}"},
    "report.findings": {"en": "What we found", "ru": "Что мы нашли", "es": "Lo que encontramos"},
    "report.borderline": {"en": "Worth checking", "ru": "Стоит уточнить", "es": "Vale la pena revisar"},
    "report.borderline_note": {
        "en": "Not counted in the total. Ask for an itemized bill to confirm.",
        "ru": "Не входит в итог. Чтобы подтвердить, запросите подробный счёт.",
        "es": "No se suma al total. Pida una factura detallada para confirmarlo."},
    "report.no_money": {"en": "Checked, no money here", "ru": "Проверено, здесь денег нет",
                        "es": "Revisado, aquí no hay dinero"},
    "report.papers": {"en": "Your papers", "ru": "Ваши документы", "es": "Sus documentos"},
    "report.appeals": {"en": "Insurance denials you can appeal", "ru": "Отказы страховой, которые можно обжаловать",
                       "es": "Rechazos del seguro que puede apelar"},
    "report.appeal_by": {"en": "Appeal by {date}", "ru": "Обжаловать до {date}", "es": "Apele antes del {date}"},
    "report.letters": {"en": "Letters ready to sign", "ru": "Письма, готовые к подписи",
                       "es": "Cartas listas para firmar"},
    "report.held": {"en": "{n} item(s) held back because a quote could not be confirmed.",
                    "ru": "Пунктов отложено: {n} — цитату не удалось подтвердить.",
                    "es": "{n} punto(s) retenido(s) porque no se pudo confirmar la cita."},
    "report.sources": {"en": "Where this comes from", "ru": "Откуда это", "es": "De dónde viene"},
    "report.page": {"en": "page", "ru": "стр.", "es": "pág."},
    "report.sheet": {"en": "sheet", "ru": "лист", "es": "hoja"},
    "report.line": {"en": "line", "ru": "строка", "es": "línea"},
    "report.to": {"en": "To", "ru": "Кому", "es": "Para"},
    "report.footer": {
        "en": "BillRadar helps with hospital bills and insurance. It does not give medical advice. "
              "You review and sign every letter.",
        "ru": "BillRadar помогает со счетами больниц и страховкой. Медицинских советов он не даёт. "
              "Каждое письмо вы проверяете и подписываете сами.",
        "es": "BillRadar ayuda con facturas de hospital y seguros. No da consejos médicos. "
              "Usted revisa y firma cada carta."},
    "report.models": {
        "en": "Papers read by Nemotron 3.5 Super VL on Nebius Token Factory, second read by Nemotron 3 Ultra, "
              "amounts and dates computed by code.",
        "ru": "Документы прочитала Nemotron 3.5 Super VL в Nebius Token Factory, второе чтение — Nemotron 3 Ultra, "
              "суммы и даты посчитал код.",
        "es": "Documentos leídos por Nemotron 3.5 Super VL en Nebius Token Factory, segunda lectura de Nemotron 3 Ultra, "
              "importes y fechas calculados por código."},
    "report.replayed": {
        "en": "Model answers in this report were replayed from recordings, not a live run. "
              "Amounts and dates are computed by code.",
        "ru": "Ответы моделей в этом отчёте воспроизведены из записей, это не живой прогон. "
              "Суммы и даты посчитал код.",
        "es": "Las respuestas de los modelos en este informe se reprodujeron de grabaciones, no de una ejecución en vivo. "
              "Los importes y las fechas los calcula el código."},
    "kind.hospital_statement": {"en": "Statement", "ru": "Счёт", "es": "Estado de cuenta"},  # also doctors' offices
    "kind.itemized_bill": {"en": "Itemized bill", "ru": "Детализированный счёт", "es": "Factura detallada"},
    "kind.eob": {"en": "Insurance statement (EOB)", "ru": "Выписка страховой (EOB)",
                 "es": "Explicación de beneficios (EOB)"},
    "kind.fa_letter": {"en": "Financial assistance letter", "ru": "Письмо о финансовой помощи",
                       "es": "Carta de asistencia financiera"},
    "kind.payment_receipt": {"en": "Payment receipt", "ru": "Квитанция об оплате", "es": "Recibo de pago"},
    "kind.other": {"en": "Other", "ru": "Другое", "es": "Otro"},
    "report.page_of": {"en": "Page {n} of {m}", "ru": "Стр. {n} из {m}", "es": "Pág. {n} de {m}"},
}

MONTHS = {
    "ru": ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"],
}


def check(lang: str) -> str:
    if lang not in LANGS:
        raise ValueError(f"language {lang!r}: choose one of {', '.join(LANGS)}")
    return lang


def t(key: str, lang: str = DEFAULT, **values) -> str:
    text = STRINGS[key][check(lang)]
    return text.format(**values) if values else text


def date_text(d, lang: str = DEFAULT) -> str:
    if lang == "en":
        return f"{d:%B %-d, %Y}"
    if lang == "ru":
        return f"{d.day} {MONTHS['ru'][d.month - 1]} {d.year} г."
    return f"{d.day} de {MONTHS['es'][d.month - 1]} de {d.year}"


def money(x, lang: str = DEFAULT) -> str:
    """$1,234.56 in English and Spanish (US bills), 1 234,56 $ in Russian; a credit is -$40.00, not $-40.00."""
    if x < 0:
        return "-" + money(-x, lang)
    s = f"{x:,.2f}"
    if lang == "ru":
        return s.replace(",", " ").replace(".", ",") + " $"
    return "$" + s


# --- translations by the text model, for the family to understand ---------------------------
# A translation is checked in code before it is shown: every number and every quoted rule
# (text inside “ ”) must come back exactly as written, and every hidden value must come back.
# A segment that fails stays in English and is reported. Names, account numbers and the address
# never leave the device: they are swapped for markers before the request and back after it.

import json  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

MODEL = "reader"  # the role; nemotron_client routes a text-only body to the text model
MARK = "⟦V{}⟧"  # ⟦V1⟧

TRANSLATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "text"],
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}}},
}

TRANSLATE_INSTRUCTIONS = """You translate short texts from BillRadar, a helper for hospital bills and insurance statements, for a family that reads {language} better than English. You never give medical advice and you never add anything.

Rules:
- Translate each item into {language}, plain and short. Return every item with its id.
- Copy exactly, character for character: every number, amount (keep "$1,240.00" as written), date digits, code, claim or sheet number, and every marker like ⟦V1⟧.
- Text inside “ ” is a quote from a policy, a plan or a law: keep it in English, unchanged, with the same quote marks.
- Do not add, drop or soften anything. Do not explain.
- Write in the {language} alphabet only (Latin letters only for names, codes and the English quotes); never use Chinese, Japanese or any other script.
- Use these words:{glossary}"""

GLOSSARY = {  # terms a family must read one way in every letter (the checker caught them going three ways)
    "ru": ("\n  itemized statement / itemized bill = детализированный счёт; billing dispute = спор по счёту; "
           "federal poverty guidelines = федеральная черта бедности (Federal Poverty Guidelines); "
           "household = семья; discharge = выписка из больницы; account = счёт (plural: счетов); "
           "Financial Assistance Policy = Политика финансовой помощи; Financial Evaluation Form = форма финансовой оценки; "
           "service = услуга; code = код; business days = рабочих дней; Florida law requires = закон Флориды требует; "
           "the same service billed twice = одна и та же услуга выставлена дважды; "
           "Florida Statutes = Florida Statutes (не переводить); "
           "Billing dispute: (subject) = Спор по счёту:; my statement for account N = в выписке по счёту N; "
           "statement (a bill from an office) = выписка; insurance statement = выписка страховой; "
           "completed form = заполненная форма; discount (is shown) = скидка указана; "
           "remove the duplicate and my $N share of it = удалите дубль и мою долю $N за него (сумму $N перенести как есть); "
           "translate every English word except names, quotes and Florida Statutes; "
           "always put a space between words, never join a Russian word to an English one."),
    "es": ("\n  itemized statement / itemized bill = factura detallada; billing dispute = disputa de factura; "
           "federal poverty guidelines = pautas federales de pobreza (Federal Poverty Guidelines); account = cuenta; "
           "Financial Assistance Policy = Política de Ayuda Financiera; business days = días hábiles; "
           "Florida law requires = la ley de Florida exige; "
           "Florida Statutes = Florida Statutes (no traducir); "
           "Financial Evaluation Form = formulario de evaluación financiera; insurance statement = estado del seguro; "
           "remove the duplicate and my $N share of it = elimine el cargo duplicado y mi parte de $N (copie $N tal cual)."),
}


@dataclass
class Translated:
    lang: str
    texts: dict[str, str]  # id -> translated text (or the English original when it failed a check)
    failed: list[str] = field(default_factory=list)  # ids kept in English, with the reason
    replayed: bool = True


def _numbers(s: str) -> list[str]:
    return sorted(re.findall(r"\d+", s.replace(",", "")))


def _quotes(s: str) -> list[str]:
    return re.findall(r"“[^”]*”", s)


def private_values(case) -> list[str]:
    """What only lives on the device: names, real account and member numbers, the address, the phone."""
    loc = case.profile.local
    vals = [p.name for p in case.profile.people] + list(loc.tokens.values())
    vals += [loc.street, loc.city, loc.zip, loc.phone]
    # longest first so a name inside a longer value is not cut; ties by text, so the masks (and the request,
    # and its recording) are the same in every process, not in set order
    return sorted({v.strip() for v in vals if v and len(v.strip()) > 1}, key=lambda v: (-len(v), v))


TOKEN = re.compile(r"\[([A-Z]+)_(\d+)\]|\b(?=(?:MEMBER|ACCT|CLAIM|PATIENT|ADDR|PHONE)_\d)([A-Z]+)_(\d+)\b")  # "member MEMBER_1" too


def plain(text: str, case) -> str:
    """A token the model saw ([MEMBER_1], [ACCT_2]) in words for the family, on the device: the last four digits
    of an ID or account, the person's name. The finding itself keeps the token (it is what was checked)."""
    loc, people = case.profile.local, {p.token: p.name for p in case.profile.people if p.name}
    def word(m):
        kind, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        key = f"{kind}_{num}"
        value = (loc.tokens or {}).get(key, "")
        if kind == "PATIENT" and people.get(key):
            return people[key]
        if kind in ("MEMBER", "ACCT", "CLAIM") and value:
            digits = re.sub(r"\D", "", value)
            return {"MEMBER": "ID", "ACCT": "account", "CLAIM": "claim"}[kind] + (f" ending {digits[-4:]}" if digits else "")
        return {"MEMBER": "member ID", "ACCT": "account", "CLAIM": "claim", "PATIENT": "the patient",
                "ADDR": "the address", "PHONE": "the phone number"}.get(kind, m.group(0))
    return TOKEN.sub(word, text or "")


def _mask(text: str, values: list[str]) -> str:
    for i, v in enumerate(values, 1):
        text = text.replace(v, MARK.format(i))
    return text


def _unmask(text: str, values: list[str]) -> str:
    for i, v in enumerate(values, 1):
        text = text.replace(MARK.format(i), v)
    return text


mask, unmask = _mask, _unmask


def check_translation(source: str, translation: str) -> str | None:
    """None when the translation keeps every number, quote and marker of the source; else the reason."""
    if not translation.strip():
        return "empty"
    if _numbers(source) != _numbers(translation):
        return "numbers changed"
    missing = [q for q in _quotes(source) if q not in translation]
    if missing:
        return f"quote changed: {missing[0][:40]}"
    marks = re.findall(r"⟦V\d+⟧", source)
    if sorted(marks) != sorted(re.findall(r"⟦V\d+⟧", translation)):
        return "hidden value changed"
    stray = STRAY_SCRIPT.findall(translation)  # "от Федеральных指南 2026 года": the paragraph stays in English
    if stray:
        return f"stray script: {''.join(stray[:4])}"
    return None


# Latin (with accents), Cyrillic, punctuation, our ⟦V1⟧ markers and the money/number signs a letter may use
STRAY_SCRIPT = re.compile(r"[^\u0000-\u024F\u0400-\u04FF\u2000-\u206F\u20AC\u2116\u27E6\u27E7]")


def translate_body(items: dict[str, str], lang: str) -> dict:
    return {
        "model": MODEL, "store": False, "stream": True, "reasoning": {"effort": "low"}, "max_output_tokens": 8000,
        "text": {"format": {"type": "json_schema", "name": "billradar_translation", "schema": TRANSLATE_SCHEMA,
                            "strict": True}},
        "input": [
            {"role": "developer", "content": [{"type": "input_text",
                                               "text": TRANSLATE_INSTRUCTIONS.format(language=NAMES_IN_ENGLISH[lang],
                                                                                           glossary=GLOSSARY.get(lang, " none"))}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(
                [{"id": k, "text": v} for k, v in items.items()], ensure_ascii=False)}]},
        ],
    }


def translate(client, case, items: dict[str, str], lang: str) -> Translated:
    """Translate our English texts into `lang` with the text model; English needs no call."""
    check(lang)
    if lang == "en" or not items:
        return Translated(lang, dict(items), [], replayed=False)
    if client.live and not case.demo:
        from billradar.pipeline import RealDataGate
        raise RealDataGate("live model calls are allowed only for a fictional case")
    values = private_values(case)
    masked = {k: _mask(v, values) for k, v in items.items()}
    res = client.respond(translate_body(masked, lang), label=f"translate-{lang}")
    got = {it.get("id"): it.get("text", "") for it in json.loads(res.text).get("items", [])}
    out, failed = {}, []
    for k, src in masked.items():
        reason = "missing" if k not in got else check_translation(src, got[k])
        if reason:
            failed.append(f"{k}: {reason}")
            out[k] = items[k]
        else:
            out[k] = _unmask(got[k], values)
    return Translated(lang, out, failed, replayed=res.replayed)


STRINGS.update({
    "report.same_rules": {"en": "Same rules as above.", "ru": "Те же правила, что выше.", "es": "Las mismas reglas de arriba."},
    "report.policy_note": {
        "en": "Rules: the public Financial Assistance Policy and form of Memorial Regional Hospital (FL; MHS = Memorial Healthcare System), applied to the fictional Seaside case.",
        "ru": "Правила: публичная политика финансовой помощи и форма Memorial Regional Hospital (Флорида; MHS = Memorial "
              "Healthcare System), применённые к вымышленному делу Seaside.",
        "es": "Reglas: la política pública de ayuda financiera y el formulario de Memorial Regional Hospital (FL; MHS = Memorial "
              "Healthcare System), aplicados al caso ficticio de Seaside."},
    "report.sample_case": {"en": "Sample case", "ru": "Пример дела", "es": "Caso de ejemplo"},
    "report.col.paper": {"en": "Paper", "ru": "Документ", "es": "Documento"},
    "report.col.kind": {"en": "Kind", "ru": "Тип", "es": "Tipo"},
    "report.col.from": {"en": "From", "ru": "От кого", "es": "De"},
    "report.col.date": {"en": "Date", "ru": "Дата", "es": "Fecha"},
    "report.col.amount": {"en": "Balance", "ru": "Баланс", "es": "Saldo"},
    "letter.itemized": {"en": "Request for an itemized statement", "ru": "Запрос детализированного счёта",
                        "es": "Solicitud de factura detallada"},
    "letter.dispute": {"en": "Billing dispute", "ru": "Спор по счёту: сумма не совпадает с данными страховки",
                       "es": "Disputa de factura: el monto no coincide con el seguro"},
    "letter.apply-discount": {"en": "Apply the financial assistance discount", "ru": "Применить скидку по финансовой помощи",
                              "es": "Aplicar el descuento de ayuda financiera"},
    "letter.application": {"en": "Application for financial assistance", "ru": "Заявление на финансовую помощь",
                           "es": "Solicitud de ayuda financiera"},
})


STRINGS.update({
    "letter.translation_note": {
        "en": "",
        "ru": "Перевод для понимания. Отправляется письмо на английском.",
        "es": "Traducción para entender la carta. Se envía la carta en inglés."},
    "letter.kept_english": {
        "en": "",
        "ru": "Часть текста оставлена на английском: перевод не прошёл проверку чисел и цитат.",
        "es": "Parte del texto quedó en inglés: la traducción no pasó la revisión de números y citas."},
})


@dataclass
class LetterTranslation:
    lang: str
    kind: str
    subject: str
    paragraphs: list[str]
    note: str
    failed: list[str]
    replayed: bool


def letter(client, case, letter_obj, lang: str) -> LetterTranslation:
    """The letter in `lang`, for understanding only. The English letter is the one that is sent."""
    items = {"subject": letter_obj.subject} | {f"p{i}": p.text for i, p in enumerate(letter_obj.paragraphs)}
    tr = translate(client, case, items, lang)
    note = t("letter.translation_note", lang)
    if tr.failed:
        note = (note + " " + t("letter.kept_english", lang)).strip()
    return LetterTranslation(lang, letter_obj.kind, tr.texts["subject"],
                             [tr.texts[f"p{i}"] for i in range(len(letter_obj.paragraphs))],
                             note, tr.failed, tr.replayed)


def findings(client, case, report, lang: str) -> Translated:
    """Titles and details of findings, keyed "<kind> #<n>:title" / ":detail" (n counts per kind, as the checker does)."""
    items, counts = {}, {}
    for f in list(report.findings) + list(report.no_money):
        counts[f.kind.value] = counts.get(f.kind.value, 0) + 1
        key = f"{f.kind.value} #{counts[f.kind.value]}"
        items[f"{key}:title"] = f.title
        items[f"{key}:detail"] = f.detail
    return translate(client, case, items, lang)


def plain_text(tr: LetterTranslation) -> str:
    """What "Copy" gives for a translation: the note first, so it is never mistaken for the letter to send."""
    return "\n".join([tr.note, "", tr.subject, ""] + [p + "\n" for p in tr.paragraphs]).rstrip() + "\n"
