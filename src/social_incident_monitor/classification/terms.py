"""Topic terms for Turkish posts.

Turkish attaches many suffixes to one stem ("çekemiyorum", "çektim", "çekme"). A light,
well-known approximation for search in Turkish is to keep only the first five letters
of a word (F5 stemming), which groups most suffixed forms without a dictionary. Terms
are single stems and pairs of neighbouring stems; function words, the bank's name and
generic complaint words (stopwords_tr.json) are left out so topics name the specific
thing people talk about ("qr kod", "emekli maaşı").
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from importlib import resources

from ..application.errors import ConfigurationError
from .text_normalizer import display_words, fold

STEM_LENGTH = 5
MIN_WORD_LENGTH = 3


def stem(word: str) -> str:
    return word[:STEM_LENGTH]


@dataclass
class TermExtractor:
    stop_stems: frozenset[str]
    surface_forms: dict[str, Counter[str]] = field(default_factory=dict)

    def terms(self, text: str) -> set[str]:
        """Stems and neighbouring stem pairs of one post (each counted once per post)."""
        stems: list[str] = []
        folded = fold(text).split()
        shown = display_words(text)
        if len(shown) != len(folded):
            shown = folded
        for word, original in zip(folded, shown, strict=True):
            if len(word) < MIN_WORD_LENGTH or word.isdigit() or not word.isalnum():
                continue
            key = stem(word)
            if key in self.stop_stems:
                continue
            self.surface_forms.setdefault(key, Counter())[original] += 1
            stems.append(key)
        terms = set(stems)
        terms.update(f"{first} {second}" for first, second in zip(stems, stems[1:], strict=False) if first != second)
        return terms

    def display(self, term: str) -> str:
        """Most frequent full word (with Turkish letters) behind each stem of the term."""
        return " ".join(self._surface(part) for part in term.split(" "))

    def _surface(self, key: str) -> str:
        forms = self.surface_forms.get(key)
        return forms.most_common(1)[0][0] if forms else key

    @classmethod
    def load(cls) -> TermExtractor:
        try:
            data = json.loads(resources.files(__package__).joinpath("stopwords_tr.json").read_text(encoding="utf-8"))
            words = [str(word) for word in data["function_words"] + data["domain_words"]]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ConfigurationError(f"invalid stopwords file: {exc}") from exc
        return cls(frozenset(stem(fold(word)) for word in words if word))
