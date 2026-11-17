"""Turkish-aware text folding used for rule matching.

``fold`` lowercases with Turkish dotted/dotless I rules, removes diacritics
(ç→c, ğ→g, ı→i, ö→o, ş→s, ü→u), drops apostrophes so suffixes stay attached
("ATM'den" → "atmden"), and collapses other punctuation to single spaces while
keeping "?" as a separate token for question detection.
"""

from __future__ import annotations

import re
import unicodedata

_TURKISH_UPPER = str.maketrans({"I": "ı", "İ": "i"})
_ASCII_FOLD = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u"})
_APOSTROPHES = re.compile(r"['’`´]")
_URLS = re.compile(r"https?://\S+")
_NON_WORD = re.compile(r"[^a-z0-9?]+")
_QUESTION = re.compile(r"\?+")


_WORDS = re.compile(r"[^\w?]+")


def display_words(text: str) -> list[str]:
    """The words ``fold`` produces, but keeping Turkish letters (for showing terms to people).

    Splits exactly like ``fold`` for Turkish and Latin text; callers compare the word
    counts and fall back to the folded words if they ever differ.
    """
    lowered = text.translate(_TURKISH_UPPER).lower()
    lowered = _URLS.sub(" ", lowered)
    lowered = _APOSTROPHES.sub("", lowered)
    lowered = _QUESTION.sub(" ? ", lowered)
    return _WORDS.sub(" ", lowered).replace("_", " ").split()


def fold(text: str) -> str:
    lowered = text.translate(_TURKISH_UPPER).lower()
    folded = lowered.translate(_ASCII_FOLD)
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = _URLS.sub(" ", folded)
    folded = _APOSTROPHES.sub("", folded)
    folded = _QUESTION.sub(" ? ", folded)
    folded = _NON_WORD.sub(" ", folded)
    return " ".join(folded.split())
