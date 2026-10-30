"""Whose paper is this? The reader copies the patient as printed; code links it to a person of the profile.

Printed forms vary even inside one pile: "DANIELA CASTILLO", "Castillo , Daniela", "CASTILLO,DANIELA",
"Castillo,Adrian James". A person matches when both their first and last name are printed, in any order or
case. Two people who both match (a shared surname, no first name) leave the paper as read: a guess here
would move a bill between mom and baby. In private mode the reader already returns tokens (PATIENT_1).
"""

from __future__ import annotations

import re

from billradar.case import Document, Profile

TOKEN = re.compile(r"\[?\s*(PATIENT_\d+)\s*\]?", re.I)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 1}


def token_for(printed: str, profile: Profile) -> str | None:
    m = TOKEN.fullmatch(printed.strip())
    if m:
        token = m.group(1).upper()
        return token if any(p.token == token for p in profile.people) else None
    words = _words(printed)
    if not words:
        return None
    hits = []
    for p in profile.people:
        names = re.findall(r"[a-z']+", p.name.lower())
        if len(names) >= 2 and names[0] in words and names[-1] in words:
            hits.append(p.token)
    return hits[0] if len(hits) == 1 else None


def link(documents: list[Document], profile: Profile) -> list[Document]:
    """The same documents with `patient` set to the person's token wherever the name is certain."""
    out = []
    for d in documents:
        token = token_for(d.patient, profile) if d.patient else None
        out.append(d.model_copy(update={"patient": token}) if token and token != d.patient else d)
    return out
