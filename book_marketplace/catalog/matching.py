"""Matching rule for books (stage 1). Pure functions: no Frappe imports, so it can be tested anywhere.

See ref/STAGE1-MATCHING-DESIGN.md. Thresholds and points are the approved values.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

AUTO_MATCH_SCORE = 70
QUEUE_MIN_SCORE = 40

POINTS = {
    "title": 40,
    "author": 30,
    "same_publisher": 15,
    "publisher_unknown": 5,
    "same_volumes": 10,
    "same_language": 5,
}

_CHAR_MAP = {
    "ي": "ی",  # Arabic yeh -> Farsi yeh
    "ك": "ک",  # Arabic kaf -> Urdu kaf
    "ه": "ہ",  # heh -> heh goal
    "ة": "ہ",  # teh marbuta -> heh goal
    "ۃ": "ہ",  # teh marbuta goal -> heh goal
}
_DIACRITICS = re.compile("[ً-ٰٟ]")
_ZERO_WIDTH = re.compile("[​‌‍]")
_TATWEEL = "ـ"
_PUNCT = re.compile(r"[.,;:!?()\[\]\"'\-–—،۔؛؟]")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹"
                        "٠١٢٣٤٥٦٧٨٩",
                        "01234567890123456789")


def normalize(text: Optional[str]) -> str:
    """Spaced key: unified Urdu letters, no diacritics, no punctuation, single spaces, lower case."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", text)
    s = s.translate(str.maketrans(_CHAR_MAP))
    s = _DIACRITICS.sub("", s)
    s = _ZERO_WIDTH.sub("", s)
    s = s.replace(_TATWEEL, "")
    s = s.translate(_DIGITS)
    s = _PUNCT.sub(" ", s)
    s = s.lower()
    return " ".join(s.split())


def compact(text: Optional[str]) -> str:
    """Compact key: spaced key with all spaces removed (so 'عبداللہ' equals 'عبد اللہ')."""
    return normalize(text).replace(" ", "")


EDITION_WORDS = ("مکمل", "جلد", "ایڈیشن", "طبع")


def has_edition_word(text: Optional[str]) -> bool:
    n = normalize(text)
    return any(w in n for w in EDITION_WORDS)


@dataclass
class Entry:
    title: str
    author: str = ""
    publisher: Optional[str] = None          # publisher profile id, or None if unknown
    isbn: Optional[str] = None
    volumes: Optional[int] = None
    language: Optional[str] = None


@dataclass
class Candidate:
    """An existing Work (and its Edition, if the publisher is known) to compare with."""
    work_id: str
    title: str
    author: str
    edition_id: Optional[str] = None
    publisher: Optional[str] = None
    isbn: Optional[str] = None
    volumes: Optional[int] = None
    language: Optional[str] = None


@dataclass
class Scored:
    candidate: Candidate
    score: int
    reasons: List[str] = field(default_factory=list)


@dataclass
class Decision:
    action: str            # "attach_edition", "new_edition_same_work", "new_work", "queue", "new"
    rule: str              # short name of the rule that fired
    work_id: Optional[str] = None
    edition_id: Optional[str] = None
    candidates: List[Scored] = field(default_factory=list)


def score(entry: Entry, cand: Candidate) -> Scored:
    reasons = []
    total = 0
    if compact(entry.title) and compact(entry.title) == compact(cand.title):
        total += POINTS["title"]
        reasons.append("title")
    if compact(entry.author) and compact(entry.author) == compact(cand.author):
        total += POINTS["author"]
        reasons.append("author")
    if entry.publisher and cand.publisher:
        if entry.publisher == cand.publisher:
            total += POINTS["same_publisher"]
            reasons.append("publisher")
    elif not entry.publisher or not cand.publisher:
        total += POINTS["publisher_unknown"]
        reasons.append("publisher_unknown")
    if entry.volumes and cand.volumes and entry.volumes == cand.volumes:
        total += POINTS["same_volumes"]
        reasons.append("volumes")
    if entry.language and cand.language and normalize(entry.language) == normalize(cand.language):
        total += POINTS["same_language"]
        reasons.append("language")
    return Scored(candidate=cand, score=total, reasons=reasons)


def decide(entry: Entry, existing: List[Candidate]) -> Decision:
    """Apply the stage 1 rule. Nothing is merged here; the caller writes the MatchDecision row."""
    # Step A: ISBN is unique per edition.
    if entry.isbn:
        isbn = re.sub(r"[\s-]", "", entry.isbn)
        for c in existing:
            if c.isbn and re.sub(r"[\s-]", "", c.isbn) == isbn:
                return Decision("attach_edition", "isbn", c.work_id, c.edition_id)

    # Step B/C: candidates with the same compact title.
    same_title = [c for c in existing if compact(c.title) == compact(entry.title) and compact(entry.title)]
    scored = sorted((score(entry, c) for c in same_title), key=lambda s: -s.score)

    if not scored:
        # Medium check: title is not equal but close, or carries an edition word.
        near = [c for c in existing if _near_title(entry.title, c.title)]
        if near:
            scored_near = sorted((score(entry, c) for c in near), key=lambda s: -s.score)
            return Decision("queue", "near_title", candidates=scored_near)
        return Decision("new", "no_candidate")

    best = scored[0]
    author_equal = "author" in best.reasons
    same_pub = "publisher" in best.reasons or "publisher_unknown" in best.reasons

    if author_equal and best.score >= AUTO_MATCH_SCORE and same_pub:
        return Decision("attach_edition", "title_author_publisher", best.candidate.work_id,
                        best.candidate.edition_id, scored)

    if author_equal and not same_pub:
        # Same work, different publisher: new edition now, confirmation queued.
        return Decision("new_edition_same_work", "same_work_other_publisher", best.candidate.work_id,
                        None, scored)

    if not author_equal and compact(entry.author) and compact(best.candidate.author):
        # Same title, different author (both known): a different work (automatic).
        return Decision("new_work", "title_other_author", candidates=scored)

    # Anything else in the middle goes to the queue.
    return Decision("queue", "medium", candidates=scored)


def _near_title(a: str, b: str) -> bool:
    """Close but not equal titles: one contains the other (after normalization), or an edition word differs."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb or compact(a) == compact(b):
        return False
    if has_edition_word(a) != has_edition_word(b):
        return compact(a).replace("مکمل", "") == compact(b).replace("مکمل", "") or \
            compact(a) in compact(b) or compact(b) in compact(a)
    return compact(a) in compact(b) or compact(b) in compact(a)
