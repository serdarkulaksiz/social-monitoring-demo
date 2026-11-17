"""Multinomial Naive Bayes with Laplace smoothing (plain Python).

For each class it remembers how often every feature appeared in posts of that class.
A new post gets, per class, log P(class) + Σ count × log P(feature | class); the
softmax of those sums is the class probability. Features never seen in training are
ignored. Naive Bayes probabilities tend to be overconfident; compare it with the
logistic regression on the test set before relying on its thresholds.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .math_utils import softmax

ALGORITHM = "nb"


@dataclass
class NaiveBayes:
    classes: tuple[str, ...]
    log_priors: dict[str, float]
    log_likelihoods: dict[str, dict[str, float]]  # class → feature → log P(feature | class)
    log_unseen: dict[str, float]  # class → log P(feature | class) for a known feature never seen in that class

    @classmethod
    def train(cls, examples: Sequence[tuple[Mapping[str, float], str]], *, alpha: float = 1.0, min_df: int = 2) -> NaiveBayes:
        if not examples:
            raise ValueError("no training examples")
        document_frequency: Counter[str] = Counter()
        for features, _label in examples:
            document_frequency.update(features.keys())
        vocabulary = {feature for feature, count in document_frequency.items() if count >= min_df}
        class_counts = Counter(label for _features, label in examples)
        classes = tuple(sorted(class_counts))
        totals: dict[str, Counter[str]] = {label: Counter() for label in classes}
        for features, label in examples:
            totals[label].update({feature: 1.0 for feature in features if feature in vocabulary})
        size = len(vocabulary) or 1
        log_likelihoods: dict[str, dict[str, float]] = {}
        log_unseen: dict[str, float] = {}
        for label in classes:
            denominator = sum(totals[label].values()) + alpha * size
            log_unseen[label] = math.log(alpha / denominator)
            log_likelihoods[label] = {
                feature: math.log((count + alpha) / denominator) for feature, count in totals[label].items()
            }
        total = sum(class_counts.values())
        log_priors = {label: math.log(class_counts[label] / total) for label in classes}
        return cls(classes, log_priors, log_likelihoods, log_unseen)

    def _known(self, features: Mapping[str, float]) -> list[str]:
        return [feature for feature in features if any(feature in table for table in self.log_likelihoods.values())]

    def _scores(self, features: Mapping[str, float]) -> dict[str, float]:
        known = self._known(features)
        return {
            label: self.log_priors[label]
            + sum(self.log_likelihoods[label].get(feature, self.log_unseen[label]) for feature in known)
            for label in self.classes
        }

    def predict_proba(self, features: Mapping[str, float]) -> dict[str, float]:
        return softmax(self._scores(features))

    def contributions(self, features: Mapping[str, float], label: str) -> dict[str, float]:
        """How much each feature pushed the post towards ``label`` rather than the other classes."""
        others = [other for other in self.classes if other != label]
        if not others:
            return {}
        result = {}
        for feature in self._known(features):
            own = self.log_likelihoods[label].get(feature, self.log_unseen[label])
            rest = sum(self.log_likelihoods[o].get(feature, self.log_unseen[o]) for o in others) / len(others)
            result[feature] = own - rest
        return result

    def to_dict(self) -> dict[str, object]:
        return {
            "classes": list(self.classes),
            "log_priors": self.log_priors,
            "log_unseen": self.log_unseen,
            "log_likelihoods": {label: {f: round(v, 6) for f, v in table.items()} for label, table in self.log_likelihoods.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> NaiveBayes:
        return cls(
            classes=tuple(data["classes"]),  # type: ignore[arg-type]
            log_priors=dict(data["log_priors"]),  # type: ignore[arg-type]
            log_likelihoods={k: dict(v) for k, v in data["log_likelihoods"].items()},  # type: ignore[union-attr]
            log_unseen=dict(data["log_unseen"]),  # type: ignore[arg-type]
        )
