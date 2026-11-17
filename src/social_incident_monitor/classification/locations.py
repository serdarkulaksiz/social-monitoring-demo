"""Province mentions in Turkish posts ("İstanbul'da ATM çalışmıyor" → istanbul).

Works on folded text (see ``text_normalizer.fold``): apostrophes are already removed, so
"İstanbul'da" arrives as "istanbulda". A province matches with no suffix or with a
common case/derivation suffix (-da, -dan, -daki, -a, -ya, -i, -in, -nin, -li, ...).
Province names that are also everyday words (``strict`` in the data file) only match
with a locative/ablative suffix or before "ili"/"ilinde"/"merkez", so "karşı" (kars+ı),
"müşteri" (muş+...) or "ağrı" (pain) are not mistaken for places. The result is a hint
for dashboards, not a verified location.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ..application.errors import ConfigurationError

_WORD_START = r"(?<![a-z0-9])"
_WORD_END = r"(?![a-z0-9])"
_ANY_SUFFIX = r"(?:d[ae]|t[ae]|d[ae]n|t[ae]n|d[ae]ki|t[ae]ki|y?[ae]|y?[iu]|n?[iu]n|l[iu]|l[iu]l[ae]r)?"
_PLACE_SUFFIX = r"(?:d[ae]|t[ae]|d[ae]n|t[ae]n|d[ae]ki|t[ae]ki)"
_PROVINCE_WORD = r"(?: (?:ili|ilinde|ilindeki|merkez|merkezde|merkezinde))"


@dataclass(frozen=True)
class Province:
    key: str
    name: str


class LocationMatcher:
    def __init__(self, provinces: list[tuple[Province, list[str], bool]]) -> None:
        self._provinces = {province.key: province for province, _, _ in provinces}
        alternatives = []
        for province, aliases, strict in provinces:
            for spelling in [province.key, *aliases]:
                body = re.escape(spelling)
                if strict:
                    pattern = f"{body}(?:{_PLACE_SUFFIX}|(?={_PROVINCE_WORD}))"
                else:
                    pattern = f"{body}{_ANY_SUFFIX}"
                alternatives.append((province.key, re.compile(f"{_WORD_START}{pattern}{_WORD_END}")))
        self._patterns = alternatives

    def find(self, folded_text: str) -> tuple[str, ...]:
        """Province keys mentioned in the text, in order of first appearance."""
        hits: list[tuple[int, str]] = []
        for key, pattern in self._patterns:
            match = pattern.search(folded_text)
            if match is not None:
                hits.append((match.start(), key))
        ordered: list[str] = []
        for _, key in sorted(hits):
            if key not in ordered:
                ordered.append(key)
        return tuple(ordered)

    def name(self, key: str) -> str:
        province = self._provinces.get(key)
        return province.name if province else key

    @classmethod
    def load(cls, path: Path | None = None) -> LocationMatcher:
        try:
            if path is None:
                text = resources.files(__package__).joinpath("locations_tr.json").read_text(encoding="utf-8")
            else:
                text = path.read_text(encoding="utf-8")
            data = json.loads(text)
            provinces = [
                (Province(str(item["key"]), str(item["name"])), [str(alias) for alias in item.get("aliases", [])], bool(item.get("strict", False)))
                for item in data["provinces"]
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ConfigurationError(f"invalid locations file: {exc}") from exc
        return cls(provinces)
