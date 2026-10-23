"""Which programme(s) the visitor is asking about.

Decided by matching names, never by a model call: it runs on every message.
The result, the conversation's focus, is kept on the conversation, so a
follow-up like "how much does it cost?" stays on the programme named before.

Rules, in order:
  1. Programmes named in this message become the focus. If the message
     compares ("vs", "or", "compare", "difference", "which"), the earlier
     focus is kept alongside them.
  2. A family word ("retreat", "teacher training") keeps the earlier focus
     if it is within that family; otherwise the focus becomes the family,
     or nothing if the family is too large to be a focus.
  3. Otherwise the earlier focus carries over.

A focus of more than MAX_FOCUS programmes is no focus: retrieval then
searches everything, as it did before focus existed.
"""

import re
import unicodedata

from app.programmes.models import ProgrammeCatalogue

MAX_FOCUS = 3

COMPARING = re.compile(
    r"\b(vs|versus|or|compare|compared|comparison|difference|different|"
    r"which|better|between)\b"
)


def is_comparing(message: str) -> bool:
    """Whether the message compares programmes ("vs", "or", "which")."""
    return bool(COMPARING.search(normalise(message)))


# Spanish/Portuguese, French, German, Italian, Dutch, Polish, Russian,
# Hindi (Devanagari and Latin), Turkish, Indonesian, Chinese, Japanese,
# Korean. Longest first, so "horas" is not read as "hora" plus "s".
OTHER_HOURS = "|".join(sorted([
    "horas", "hora", "heures", "heure", "stunden", "stunde", "std", "ore",
    "ora", "uur", "godzin", "godziny", "часов", "часа", "час", "घंटे",
    "घंटा", "ghante", "ghanta", "saat", "jam", "小时", "時間", "시간",
], key=len, reverse=True))
OTHER_DAYS = "|".join(sorted([
    "dias", "dia", "jours", "jour", "tage", "tagen", "tag", "giorni",
    "giorno", "dagen", "dag", "dni", "dzien", "дней", "дня", "день",
    "दिन", "din", "gun", "hari", "天", "日", "일",
], key=len, reverse=True))


def normalise(text: str) -> str:
    """Lower case, hyphens to spaces, units spelled one way.

    "300-hr", "300 hrs" and "300 Hours" all become "300 hour"; "7-days"
    becomes "7 day".
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("-", " ").replace("_", " ")
    text = re.sub(r"(\d+)\s*(h|hr|hrs|hour|hours)\b", r"\1 hour", text)
    text = re.sub(r"(\d+)\s*(d|day|days)\b", r"\1 day", text)
    # The same units in other languages ("300 horas", "100-Stunden-Kurs",
    # "14 दिन"), so a programme is found whatever language the visitor
    # writes in. Accents are already stripped ("días" is "dias").
    text = re.sub(rf"(\d+)\s*({OTHER_HOURS})(?![a-z])", r"\1 hour", text)
    text = re.sub(rf"(\d+)\s*({OTHER_DAYS})(?![a-z])", r"\1 day", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def _find(text: str, phrases: list[str]) -> tuple[bool, str]:
    """Whether any phrase occurs as whole words; the text with it removed."""
    found = False
    for phrase in sorted((normalise(p) for p in phrases), key=len, reverse=True):
        pattern = f" {phrase} "
        if phrase and pattern in text:
            found = True
            text = text.replace(pattern, " ")
    return found, text


def resolve_focus(
    message: str, previous: list[str], catalogue: ProgrammeCatalogue
) -> list[str]:
    """The programme ids this message is about, given the earlier focus."""
    text = f" {normalise(message)} "

    by_longest_alias = sorted(
        catalogue.programmes,
        key=lambda p: max((len(a) for a in p.aliases), default=0),
        reverse=True,
    )
    named: list[str] = []
    for programme in by_longest_alias:
        hit, text = _find(text, programme.aliases)
        if hit:
            named.append(programme.programme_id)

    # Short names ("7 day") count only next to a programme of the same
    # family, named earlier or in this message.
    context = previous + named
    for programme in by_longest_alias:
        if programme.programme_id in named or not context:
            continue
        if _same_family(programme.programme_id, context, catalogue):
            hit, text = _find(text, programme.context_aliases)
            if hit:
                named.append(programme.programme_id)

    comparing = is_comparing(message)

    if named:
        focus = named
        if comparing:
            focus = list(dict.fromkeys(previous + named))
        return focus if len(focus) <= MAX_FOCUS else []

    hits: list[list[str]] = []
    for word, ids in catalogue.families.items():
        hit, text = _find(text, [word])
        if hit:
            hits.append(ids)
    # Two family words narrow each other: "teacher training on Havelock"
    # is the Havelock teacher training, not every course plus every
    # Havelock programme.
    family_hit = [p for p in hits[0] if all(p in ids for ids in hits)] if hits else []
    if hits and not family_hit:
        family_hit = list(dict.fromkeys(p for ids in hits for p in ids))
    if family_hit:
        if previous and set(previous) <= set(family_hit):
            return previous
        return family_hit if len(family_hit) <= MAX_FOCUS else []

    return previous


def wants_overview(
    message: str, focus: list[str], catalogue: ProgrammeCatalogue | None
) -> bool:
    """Whether to add the overview section to this message's retrieval.

    When the message names a group too large to be a focus ("what about
    the 200-hour courses?", "what retreats do you have?"), or compares
    within a group, the overview shows every member side by side, so the
    bot does not give one course's length or place to all of them.
    """
    if catalogue is None or not catalogue.overview_sections:
        return False
    text = f" {normalise(message)} "
    names_group = any(_find(text, [word])[0] for word in catalogue.families)
    # A comparison that names a group ("the 200-hour in Rishikesh or on
    # Havelock?") also gets it: the focus covers one side at best.
    return names_group and (not focus or is_comparing(message))


def _same_family(
    programme_id: str, previous: list[str], catalogue: ProgrammeCatalogue
) -> bool:
    for ids in catalogue.families.values():
        if programme_id in ids and any(p in ids for p in previous):
            return True
    return False
