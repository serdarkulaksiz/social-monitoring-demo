"""Turning one post into the features the text models learn from.

Everything here is plain Python. A post becomes a bag of named features:

* ``w:``  folded words ("acilmiyor"), ``b:`` neighbouring word pairs ("sorun yok");
* ``c:``  3–5 letter pieces of each word, so misspellings still look alike
  ("acilmiyo" and "açılmıyor" share "<aci", "acil", "cilm", …);
* ``neg:`` the word before "yok"/"değil"/"olmaz" ("sorun yok" → ``neg:sorun``), and
  ``x:neg_verb`` for negative verb forms ("çekemiyorum", "vermedi");
* ``e:``  emoji and ``x:`` punctuation cues ("👏", "!!", "...");
* ``r:``  what the rules found (``r:issue:login_failure``), so the rules' knowledge is
  an input the model can learn on top of;
* ``h:``  hashtags, kept apart from the words ("#çöktü" is a trending political tag far
  more often than a complaint), and spam cues: many hashtags, many tagged accounts, a
  list of capitalised names.

User names (@…) and links are replaced by fixed tokens first: people's names must not
become model features.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from ...domain.models import AnalysisResult
from ..text_normalizer import fold

_MENTION = re.compile(r"@\w+")
_HASHTAG = re.compile(r"#\w+")
_CAPITALISED = re.compile(r"^[A-ZÇĞİÖŞÜ][a-zçğıöşü]+$")
_LINK = re.compile(r"https?://\S+|www\.\S+")
_NEGATORS = frozenset({"yok", "degil", "olmaz", "olmadi"})
# Turkish negative verb forms: -me/-ma + tense/person ("cekemiyorum", "vermedi", "gelmemis", "olmaz").
_NEGATIVE_VERB = re.compile(r"(m(i|u)yo|m(a|e)d(i|im|ik|iniz|iler)$|m(a|e)mis|m(a|e)yac|m(a|e)z(sa|se)?$)")
_EXPLANATION_PREFIXES = ("w:", "b:", "neg:", "e:", "h:", "x:")


@dataclass(frozen=True)
class FeatureSettings:
    char_min: int = 3
    char_max: int = 5
    word_pairs: bool = True
    rule_features: bool = True

    def to_dict(self) -> dict[str, object]:
        return {"char_min": self.char_min, "char_max": self.char_max, "word_pairs": self.word_pairs, "rule_features": self.rule_features}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> FeatureSettings:
        return cls(
            char_min=int(data.get("char_min", 3)),  # type: ignore[arg-type]
            char_max=int(data.get("char_max", 5)),  # type: ignore[arg-type]
            word_pairs=bool(data.get("word_pairs", True)),
            rule_features=bool(data.get("rule_features", True)),
        )


def mask_people(text: str) -> str:
    """Replace user names and links by fixed tokens."""
    return _MENTION.sub(" kullaniciadi ", _LINK.sub(" baglanti ", text))


def extract(text: str, rules: AnalysisResult | None = None, settings: FeatureSettings = FeatureSettings()) -> Counter[str]:
    features: Counter[str] = Counter()
    hashtags = _HASHTAG.findall(text)
    for tag in hashtags:
        features[f"h:{fold(tag)}"] += 1
    if len(hashtags) >= 3:
        features["x:many_hashtags"] += 1
    if len(_MENTION.findall(text)) >= 3:
        features["x:many_mentions"] += 1
    raw_words = text.split()
    if len(raw_words) >= 8 and sum(1 for word in raw_words if _CAPITALISED.match(word)) / len(raw_words) >= 0.5:
        features["x:name_list"] += 1
    masked = mask_people(_HASHTAG.sub(" ", text))
    words = [word for word in fold(masked).split() if word != "?"]
    for index, word in enumerate(words):
        features[f"w:{word}"] += 1
        if settings.word_pairs and index + 1 < len(words):
            features[f"b:{word} {words[index + 1]}"] += 1
        if word in _NEGATORS and index > 0:
            features[f"neg:{words[index - 1]}"] += 1
        elif len(word) > 4 and _NEGATIVE_VERB.search(word):
            features["x:neg_verb"] += 1
        padded = f"<{word}>"
        for size in range(settings.char_min, settings.char_max + 1):
            for start in range(len(padded) - size + 1):
                features[f"c:{padded[start:start + size]}"] += 1
    for char in masked:
        if unicodedata.category(char) == "So":
            features[f"e:{char}"] += 1
    if "!!" in masked:
        features["x:exclaim"] += 1
    if "..." in masked or "…" in masked:
        features["x:ellipsis"] += 1
    if "?" in masked:
        features["x:question"] += 1
    if settings.rule_features and rules is not None:
        features.update(rule_features(rules))
    return features


def rule_features(rules: AnalysisResult) -> list[str]:
    names = [f"r:{rule}" for rule in rules.matched_rules]
    names.append("r:bank" if rules.is_bank_related else "r:not_bank")
    names.append("r:complaint" if rules.is_complaint else "r:not_complaint")
    return names


def readable(feature: str) -> str | None:
    """Feature name shown to analysts; letter pieces (``c:``) and rule results (``r:``) are not shown on their own."""
    if feature.startswith(("c:", "r:")):
        return None
    for prefix in _EXPLANATION_PREFIXES:
        if feature.startswith(prefix):
            body = feature[len(prefix):]
            if prefix == "neg:":
                return f"{body} (olumsuz)"
            if prefix == "h:":
                return f"#{body}"
            if prefix == "x:":
                return {"neg_verb": "olumsuz fiil", "exclaim": "!!", "ellipsis": "...", "question": "soru",
                        "many_hashtags": "çok hashtag", "many_mentions": "çok etiketlenen hesap",
                        "name_list": "isim listesi"}.get(body, body)
            return body
    return feature
