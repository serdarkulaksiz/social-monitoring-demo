"""The trained text model: four decisions ("heads") and the file it is stored in.

Heads:
* ``bank``      – is the post about the bank (``yes``/``no``), trained on every post;
* ``complaint`` – is it a complaint (``yes``/``no``), trained on posts about the bank;
* ``service``   – which service, trained on complaints;
* ``issue``     – which problem, trained on complaints.

A head is missing when the training data had only one class for it (or too few
examples); the hybrid classifier then keeps the rules' answer for that decision.

The model is one JSON file: readable, no code inside, no pickle. The file carries a
SHA-256 of its own content (``sha256``); a file whose content does not match is
refused, so a damaged or hand-edited model never runs silently.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ...domain.models import AnalysisResult
from ..text_normalizer import display_words, fold
from .features import FeatureSettings, extract, mask_people, readable
from .logistic import LogisticRegression
from .naive_bayes import NaiveBayes

FORMAT = "social-incident-monitor/text-model"
FORMAT_VERSION = 1
HEADS = ("bank", "complaint", "service", "issue")
YES, NO = "yes", "no"


class ModelError(Exception):
    """A model file is missing, damaged or not a model file."""


class Head(Protocol):
    classes: tuple[str, ...]

    def predict_proba(self, features: Mapping[str, float]) -> dict[str, float]: ...

    def contributions(self, features: Mapping[str, float], label: str) -> dict[str, float]: ...

    def to_dict(self) -> dict[str, object]: ...


_HEAD_TYPES: dict[str, Any] = {"nb": NaiveBayes, "logreg": LogisticRegression}


@dataclass(frozen=True)
class Prediction:
    p_bank: float | None
    p_complaint: float | None
    service: tuple[str, float] | None
    issue: tuple[str, float] | None
    explanation: tuple[tuple[str, float], ...]  # words that pushed the complaint decision most


@dataclass
class TextModel:
    version: str
    algorithm: str
    features: FeatureSettings
    heads: dict[str, Head]
    metadata: dict[str, Any] = field(default_factory=dict)

    def predict(self, text: str, rules: AnalysisResult | None) -> Prediction:
        features = extract(text, rules, self.features)
        p_bank = self._probability(features, "bank")
        p_complaint = self._probability(features, "complaint")
        explanation: tuple[tuple[str, float], ...] = ()
        complaint_head = self.heads.get("complaint")
        if complaint_head is not None and p_complaint is not None:
            towards = YES if p_complaint >= 0.5 else NO
            explanation = explain(text, complaint_head.contributions(features, towards))
        return Prediction(
            p_bank=p_bank,
            p_complaint=p_complaint,
            service=self._best(features, "service"),
            issue=self._best(features, "issue"),
            explanation=explanation,
        )

    def _probability(self, features: Mapping[str, float], head: str) -> float | None:
        model = self.heads.get(head)
        return None if model is None else model.predict_proba(features).get(YES, 0.0)

    def _best(self, features: Mapping[str, float], head: str) -> tuple[str, float] | None:
        model = self.heads.get(head)
        if model is None:
            return None
        probabilities = model.predict_proba(features)
        label = max(probabilities, key=lambda name: (probabilities[name], name))
        return label, probabilities[label]

    # --- storage -------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "model_version": self.version,
            "algorithm": self.algorithm,
            "features": self.features.to_dict(),
            "metadata": self.metadata,
            "heads": {name: head.to_dict() for name, head in self.heads.items()},
        }

    def save(self, path: Path) -> str:
        """Write the model (with its own checksum) and return the checksum."""
        data = self.to_dict()
        checksum = content_hash(data)
        data["sha256"] = checksum
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(path)
        return checksum

    @classmethod
    def load(cls, path: Path) -> TextModel:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ModelError(f"model file not found: {path}") from exc
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelError(f"cannot read model file {path}: {exc}") from exc
        return cls.from_dict(data, source=str(path))

    @classmethod
    def from_dict(cls, data: Any, source: str = "model") -> TextModel:
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ModelError(f"{source} is not a text model file")
        if data.get("format_version") != FORMAT_VERSION:
            raise ModelError(f"{source}: unsupported model format version {data.get('format_version')!r}")
        stored = data.pop("sha256", None)
        if stored != content_hash(data):
            raise ModelError(f"{source}: checksum does not match the content (file damaged or edited)")
        algorithm = data.get("algorithm")
        head_type = _HEAD_TYPES.get(str(algorithm))
        if head_type is None:
            raise ModelError(f"{source}: unknown algorithm {algorithm!r}")
        try:
            heads = {name: head_type.from_dict(value) for name, value in data["heads"].items() if name in HEADS}
            return cls(
                version=str(data["model_version"]),
                algorithm=str(algorithm),
                features=FeatureSettings.from_dict(data.get("features", {})),
                heads=heads,
                metadata=dict(data.get("metadata", {})),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ModelError(f"{source}: invalid model content ({exc})") from exc


def content_hash(data: Mapping[str, Any]) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def explain(text: str, contributions: Mapping[str, float], top: int = 5) -> tuple[tuple[str, float], ...]:
    """The words (and cues) that pushed the decision most, with letter pieces credited to their words."""
    masked = mask_people(text)
    folded_words = fold(masked).split()
    shown_words = display_words(masked)
    shown = dict(zip(folded_words, shown_words, strict=True)) if len(shown_words) == len(folded_words) else {}
    words = [word for word in folded_words if word != "?"]
    totals: dict[str, float] = defaultdict(float)
    for feature, value in contributions.items():
        if feature.startswith("c:"):
            piece = feature[2:]
            owners = [word for word in set(words) if piece in f"<{word}>"]
            for owner in owners:
                totals[owner] += value / len(owners)
            continue
        name = readable(feature)
        if name:
            totals[name] += value
    ranked = sorted(((shown.get(name, name), value) for name, value in totals.items() if value > 0), key=lambda item: (-item[1], item[0]))
    return tuple((name, round(value, 3)) for name, value in ranked[:top])
