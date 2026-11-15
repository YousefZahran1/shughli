"""Small text helpers shared by scoring and tailoring."""
from __future__ import annotations

import re

_REQ_HEADING = re.compile(
    r"^(?:requirements?|qualifications?|your profile|what you bring|what we(?:'|’)re looking for|"
    r"what you(?:'|’)ll need|you have|must[- ]haves?|skills|experience|about you|"
    r"dein profil|ihr profil|anforderungen|was du mitbringst|was sie mitbringen|"
    r"dein background|deine qualifikationen)\b",
    re.I)
_ANY_HEADING = re.compile(
    r"^(?:responsibilit|what you(?:'|’)ll do|your role|your tasks|about (?:us|the)|benefits|"
    r"what we offer|we offer|why join|our offer|deine aufgaben|ihre aufgaben|aufgaben|"
    r"das bieten wir|wir bieten|über uns|ueber uns|nice to have|bonus)",
    re.I)
_HINT = re.compile(
    r"\b(\d{1,2}\s*\+?\s*(?:years?|yrs?|jahre?n?)|years? of|erfahrung|fluent|native|german|english|"
    r"arabic|deutsch|englisch|visa|work permit|work authori[sz]ation|sponsor|right to work|"
    r"arbeitserlaubnis|aufenthalt|eu citizen|citizenship)\b",
    re.I)


def _is_heading(line: str) -> bool:
    s = line.strip().strip("*#: ").strip()
    return 0 < len(s) <= 60 and not s.endswith(".") and not line.lstrip().startswith("- ")


def excerpt(desc: str, limit: int = 900) -> str:
    """Pick the useful part of a job description: text under requirement-style
    headings, plus lines about experience years, languages, visa or sponsorship.
    Falls back to the first `limit` characters."""
    desc = (desc or "").strip()
    if not desc:
        return ""
    lines = [l.strip() for l in desc.splitlines() if l.strip()]
    picked: list[str] = []
    seen: set[str] = set()

    def add(l: str) -> None:
        if l not in seen:
            seen.add(l)
            picked.append(l)

    i = 0
    while i < len(lines):
        if _is_heading(lines[i]) and _REQ_HEADING.match(lines[i].strip().strip("*#: ")):
            j = i + 1
            while j < len(lines) and j - i <= 25:
                if _is_heading(lines[j]) and (_ANY_HEADING.match(lines[j].strip().strip("*#: "))
                                              or lines[j].rstrip().endswith(":")):
                    break
                add(lines[j])
                j += 1
            i = j
        else:
            i += 1
    for l in lines:
        if _HINT.search(l):
            add(l)
    if not picked:
        return desc[:limit]
    out, used = [], 0
    for l in picked:
        if used + len(l) + 1 > limit:
            if not out:
                out.append(l[:limit])
            break
        out.append(l)
        used += len(l) + 1
    return "\n".join(out)
