"""Findings in Russian and Spanish for the PDF report, written by code from the finding's own numbers.

The checks write every finding in English (that text is what the second read checks). For the family's language
the same facts are put into fixed sentences here, one per kind of finding; numbers, codes, dates and sheets come
from the finding, never from a model. A kind without sentences here stays in English.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from billradar import i18n
from billradar.case import Case
from billradar.checks import Finding, Kind


def _paper(doc_id: str, lang: str, case: str = "nom") -> str:
    n = doc_id[1:]
    if lang == "ru":
        sheet = {"nom": "лист", "prep": "листе"}[case]
        file_ = {"nom": "файл", "prep": "файле"}[case]
        return f"{sheet} {n}" if doc_id.startswith("S") else f"{file_} {n}"
    return f"la hoja {n}" if doc_id.startswith("S") else f"el archivo {n}"


def _id_ending(case: Case, lang: str) -> str:
    value = (case.profile.local.tokens or {}).get("MEMBER_1", "")
    digits = re.sub(r"\D", "", value)[-4:]
    if lang == "ru":
        return f"ID полиса, оканчивается на {digits}" if digits else "ID полиса"
    return f"ID que termina en {digits}" if digits else "su ID de miembro"


SUFFIX = re.compile(r"^(?:LLC|PA|P\.A\.|Inc\.?|MD|M\.D\.|PLLC|LLP)$", re.I)


def _offices(detail: str) -> list[str]:
    """'300_Women First OB/GYN Care, LLC, Little Harbor Pediatrics - PA, Women First OB/GYN Care, LLC bill separately'
    -> ['Women First OB/GYN Care, LLC', 'Little Harbor Pediatrics - PA']: an office once, its "LLC" kept with it."""
    from billradar.callprep import provider_name
    parts, names = detail.split(" bill separately")[0].split(", "), []
    for x in parts:
        if names and SUFFIX.match(x.strip()):
            names[-1] += ", " + x.strip().upper().replace("INC", "Inc")
        else:
            names.append(x.strip())
    seen, out = set(), []
    for n in names:
        name = provider_name(n)
        key = re.sub(r"[^a-z]", "", name.lower())
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def text(f: Finding, case: Case, lang: str) -> tuple[str, str]:
    """(title, detail) in `lang`; English and unknown kinds come back as the checks wrote them."""
    plain = (i18n.plain(f.title, case), i18n.plain(f.detail, case))
    if lang == "en":
        if f.kind == Kind.NM and " bill separately" in f.detail:  # the office list as people say it, once each
            return plain[0], (f"{', '.join(_offices(f.detail))} bill separately."
                              + plain[1].split(" bill separately.", 1)[-1])
        if f.kind == Kind.P1 and f.math.get("missing"):
            missing = [x for x in f.math["missing"].split(",") if x]
            word = "page" if len(missing) == 1 else "pages"
            return plain[0], re.sub(r"Find or ask for pages [\d, ]+\.", f"Find or ask for {word} {', '.join(missing)}.", plain[1])
        return plain
    m, ev = f.math, [e for e in f.evidence]
    first = ev[0].doc_id if ev else ""
    money = lambda x: i18n.money(Decimal(x), lang)
    day = lambda s: i18n.date_text(date.fromisoformat(s), lang)
    ru = lang == "ru"
    p = case.profile
    has = lambda *keys: all(m.get(k) not in (None, "") for k in keys)  # a variant without these numbers stays English
    if f.kind == Kind.E2 and len(ev) >= 2 and has("code", "service_date", "charges"):
        a, b = ev[0].line_no, ev[1].line_no
        return ((f"Одна услуга выставлена дважды: {_paper(first, lang)}" if ru else
                 f"El mismo servicio cobrado dos veces en {_paper(first, lang)}"),
                (f"Строки {a} и {b}: один и тот же код {m['code']} от {day(m['service_date'])}, по {money(m['charges'])} каждая."
                 if ru else f"Las líneas {a} y {b} tienen el mismo código {m['code']} del {day(m['service_date'])}, "
                            f"por {money(m['charges'])} cada una."))
    if f.kind == Kind.E3 and has("patient_balance", "applied_on"):
        credit, on = money(abs(Decimal(m["patient_balance"]))), m["applied_on"]
        return ((f"Проверьте, что кредит {credit} остаётся вычтенным" if ru else f"Compruebe que el crédito de {credit} siga descontado"),
                (f"На {_paper(first, lang, 'prep')} кредит {credit}. На {_paper(on, lang, 'prep')} того же счёта сумма к оплате, "
                 "в которую он, похоже, уже вошёл. Проверьте, что так и осталось; как возврат денег он не считается." if ru else
                 f"{_paper(first, lang).capitalize()} muestra un crédito de {credit}. {_paper(on, lang).capitalize()} de la misma cuenta "
                 "pide un monto que parece incluirlo ya. Compruebe que siga así; no se cuenta como dinero devuelto."))
    if f.kind == Kind.E3 and has("line_patient_responsibility"):
        credit = money(abs(Decimal(m["line_patient_responsibility"])))
        return ((f"Переплата {credit} всё ещё на {_paper(first, lang, 'prep')}" if ru else
                 f"Su pago de más de {credit} sigue en {_paper(first, lang)}"),
                (f"Там стоит кредит {credit} в вашу пользу. Попросите офис вернуть деньги или показать, что они вычтены "
                 "из суммы к оплате." if ru else f"Muestra un crédito de {credit} a su favor. Pida a la oficina que se lo "
                 "devuelva o que muestre que lo descontó de lo que debe."))
    if f.kind == Kind.E3 and has("patient_balance"):
        credit = money(abs(Decimal(m["patient_balance"])))
        return ((f"У вас кредит {credit}: {_paper(first, lang)}" if ru else f"Tiene un crédito de {credit} en {_paper(first, lang)}"),
                ("Баланс в вашу пользу. Попросите вернуть деньги, а не оставлять их на счёте." if ru else
                 "El saldo está a su favor. Pida el reembolso en lugar de dejarlo en la cuenta."))
    if f.kind == Kind.E4 and has("patient_balance", "amount_due_coupon"):
        bal, due = money(m["patient_balance"]), money(m["amount_due_coupon"])
        return ((f"На {_paper(first, lang, 'prep')} две разные суммы" if ru else f"{_paper(first, lang).capitalize()} pide dos montos distintos"),
                (f"Там сказано, что ваш баланс {bal}, и при этом просят оплатить {due}. Не платите, пока офис не объяснит, "
                 "какая сумма верна." if ru else f"Dice que su saldo es {bal} y también le pide pagar {due}. No pague hasta que "
                                               "la oficina explique cuál es el correcto."))
    if f.kind == Kind.B1 and has("codes"):
        k = int(m.get("denied_lines", "0") or 0)
        tot = f" на {money(m['denied_total'])}" if m.get("denied_total") else ""
        tot_es = f" por {money(m['denied_total'])}" if m.get("denied_total") else ""
        denied_ru = f" Отклонено строк: {k}{tot}." if k else ""
        denied_es = f" Se rechazaron {k} {'línea' if k == 1 else 'líneas'}{tot_es}." if k else ""
        if m.get("baby_bill"):
            baby_ru = (f" Больница уже выставила счёт малышу ({_paper(m['baby_bill'], lang)}) на {money(m['baby_balance'])} и "
                       f"показывает кредит страховой {money(m['baby_insurance_credit'])}, которого нет ни в одной выписке "
                       "страховой у вас: запросите выписку, на которой он стоит.")
            baby_es = (f" El hospital ya factura a la cuenta del bebé ({_paper(m['baby_bill'], lang)}) {money(m['baby_balance'])} y "
                       f"muestra un crédito del seguro de {money(m['baby_insurance_credit'])} que ningún estado del seguro en sus "
                       "papeles explica: pida el estado que lo respalda.")
        else:
            baby_ru = baby_es = ""
        return (("Уход за малышом выставлен по страховке мамы" if ru else "La atención del bebé se facturó al seguro de la mamá"),
                (f"На {_paper(first, lang, 'prep')} уход за новорождённым ({m['codes']}) записан на маму ({_id_ending(case, lang)})."
                 f"{denied_ru} Попросите страховую и поставщика выставить счёт на ребёнка или добавить ребёнка в план, "
                 f"пока долг не перешёл на вас.{baby_ru}" if ru else
                 f"{_paper(first, lang).capitalize()} registra la atención del recién nacido ({m['codes']}) a nombre de la mamá "
                 f"({_id_ending(case, lang)}).{denied_es} Pida a la aseguradora y al proveedor que la facturen a nombre del bebé, "
                 f"o agregue al bebé al plan, antes de que el saldo pase a usted.{baby_es}"))
    if f.kind == Kind.B2 and has("patient_balance", "statement_date", "days_since"):
        who, bal, when, age = f.title.split(" to ", 1)[-1], money(m["patient_balance"]), day(m["statement_date"]), int(m["days_since"])
        late_ru = ("Прошло {0} дней: срок, если он был, скорее всего прошёл, поэтому отправьте данные страховки малыша сейчас.".format(age)
                   if age > 30 else "Отправьте данные страховки малыша, пока долг не перешёл на вас.")
        late_es = ("Han pasado {0} días: cualquier plazo que diera probablemente ya pasó, así que envíe ahora los datos del "
                   "seguro del bebé.".format(age) if age > 30 else "Envíe los datos del seguro del bebé antes de que la deuda pase a usted.")
        return ((f"Отправьте данные страховки малыша в {who}" if ru else f"Envíe los datos del seguro del bebé a {who}"),
                (f"{_paper(first, lang).capitalize()} от {who} от {when} просит {bal} за уход за малышом, и ни одна выписка "
                 f"страховой у вас не покрывает малыша. {late_ru}" if ru else
                 f"{_paper(first, lang).capitalize()} de {who}, del {when}, pide {bal} por la atención del bebé, y ningún estado "
                 f"del seguro en sus papeles cubre al bebé. {late_es}"))
    if f.kind == Kind.P1 and has("missing", "page_count"):
        missing = [x for x in m["missing"].split(",") if x]
        total = int(m["page_count"])
        pages = ", ".join(missing)
        return ((f"Не хватает страницы {pages} из {total}" if ru else f"Falta la página {pages} de {total}"),
                (f"В этой выписке страховой {total} страницы, у нас {total - len(missing)}. Итоги могут быть на недостающей. "
                 f"Найдите или запросите страницу {pages}." if ru else
                 f"Este estado del seguro tiene {total} páginas; tenemos {total - len(missing)}. Los totales pueden estar en la "
                 f"que falta. Busque o pida la página {pages}."))
    if f.kind == Kind.F1 and has("fpl_pct", "discount_pct"):
        inc, pct, d = i18n.money(p.annual_income, lang).replace(",00 $", " $").replace(".00", ""), m["fpl_pct"], m["discount_pct"]
        pct = pct.replace(".", ",") if ru else pct
        return (("Похоже, вам положена финансовая помощь" if ru else "Probablemente califica para ayuda financiera"),
                (f"Семья из {p.household_size} человек, доход {inc}: {pct}% от федеральной черты бедности. По политике больницы "
                 f"на этом уровне скидка {d}%. На {_paper(first, lang, 'prep')} скидки пока нет." if ru else
                 f"Hogar de {p.household_size}, ingresos de {inc}: {pct}% del umbral federal de pobreza. La política del hospital "
                 f"da {d}% de descuento a este nivel. {_paper(first, lang).capitalize()} aún no tiene descuento."))
    if f.kind == Kind.I1:
        return (("Запросите детализированный счёт" if ru else "Pida la factura detallada"),
                (f"На {_paper(first, lang, 'prep')} только итог, без всех позиций. Ошибки по строкам могут прятаться там. "
                 "Больница во Флориде обязана прислать детализированный счёт в течение 7 дней после вашего запроса." if ru else
                 f"{_paper(first, lang).capitalize()} muestra un total sin cada cargo. Ahí pueden esconderse errores por línea. "
                 "Un hospital de Florida debe enviar la factura detallada dentro de los 7 días posteriores a su solicitud."))
    if f.kind == Kind.F2 and f.deadline:
        dates = {k: v for k, v in m.items() if k.startswith(("policy:", "federal:"))}
        federal = dates.get("federal: 240 days after first bill")
        later = (f" Федеральный минимум — {day(federal)}; политика больницы даёт срок до {i18n.date_text(f.deadline, lang)}, "
                 "считается более поздняя дата." if ru else
                 f" El mínimo federal es el {day(federal)}; la política del hospital da plazo hasta el "
                 f"{i18n.date_text(f.deadline, lang)}, y cuenta la fecha más tardía.") if federal and federal < f.deadline.isoformat() else ""
        return ((f"Подайте заявку до {i18n.date_text(f.deadline, lang)}" if ru else
                 f"Presente la solicitud antes del {i18n.date_text(f.deadline, lang)}"),
                (f"Последний день подать заявку на финансовую помощь больницы.{later}" if ru else
                 f"El último día para pedir la ayuda financiera del hospital.{later}"))
    if f.kind == Kind.NM and " bill separately" in f.detail:
        who = "; ".join(_offices(f.detail))
        return (("Счета врачей не входят в политику помощи больницы" if ru else
                 "Las facturas de los médicos no entran en la política de ayuda del hospital"),
                (f"{who} выставляют счета отдельно. Эта политика покрывает только больницу и её собственных врачей; "
                 "спросите в каждом офисе, есть ли у них своя программа помощи." if ru else
                 f"{who} facturan por separado. Esta política cubre solo al hospital y a sus propios médicos; pregunte en cada "
                 "consultorio si tiene su propio programa de ayuda."))
    return plain
