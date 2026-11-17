"""TF-IDF + multinomial logistic regression (plain Python).

* TF-IDF: a feature that appears in few posts says more than one that appears in all
  of them. Each post vector is (1 + log count) × idf, scaled to length 1.
* Logistic regression: one weight per (class, feature) plus a bias per class; the
  softmax of the weighted sums is the class probability. Weights are learnt with
  stochastic gradient descent and L2 regularisation, in a fixed (seeded) order, so the
  same data always gives the same model.
* Classes are weighted by their inverse frequency so rare classes (complaints are a
  minority) are not ignored.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .math_utils import softmax

ALGORITHM = "logreg"


@dataclass
class LogisticRegression:
    classes: tuple[str, ...]
    idf: dict[str, float]
    weights: dict[str, dict[str, float]]  # class → feature → weight
    bias: dict[str, float]

    @classmethod
    def train(
        cls,
        examples: Sequence[tuple[Mapping[str, float], str]],
        *,
        epochs: int = 25,
        learning_rate: float = 0.5,
        l2: float = 1e-4,
        min_df: int = 2,
        seed: int = 13,
        prune_below: float = 1e-3,
    ) -> LogisticRegression:
        if not examples:
            raise ValueError("no training examples")
        document_frequency: Counter[str] = Counter()
        for features, _label in examples:
            document_frequency.update(features.keys())
        total_documents = len(examples)
        idf = {
            feature: math.log((1 + total_documents) / (1 + count)) + 1.0
            for feature, count in document_frequency.items()
            if count >= min_df
        }
        class_counts = Counter(label for _features, label in examples)
        classes = tuple(sorted(class_counts))
        class_weight = {label: total_documents / (len(classes) * count) for label, count in class_counts.items()}
        model = cls(classes, idf, {label: {} for label in classes}, {label: 0.0 for label in classes})
        vectors = [(model.vectorize(features), label) for features, label in examples]
        order = list(range(len(vectors)))
        rng = random.Random(seed)
        for epoch in range(epochs):
            rng.shuffle(order)
            rate = learning_rate / (1.0 + epoch * 0.2)
            for index in order:
                vector, label = vectors[index]
                probabilities = model._probabilities(vector)
                weight = class_weight[label]
                for name in classes:
                    gradient = (probabilities[name] - (1.0 if name == label else 0.0)) * weight
                    if gradient == 0.0:
                        continue
                    row = model.weights[name]
                    for feature, value in vector.items():
                        current = row.get(feature, 0.0)
                        row[feature] = current - rate * (gradient * value + l2 * current)
                    model.bias[name] -= rate * gradient
        for name in classes:
            model.weights[name] = {f: round(w, 6) for f, w in model.weights[name].items() if abs(w) >= prune_below}
        return model

    def vectorize(self, features: Mapping[str, float]) -> dict[str, float]:
        vector = {
            feature: (1.0 + math.log(count)) * self.idf[feature]
            for feature, count in features.items()
            if count > 0 and feature in self.idf
        }
        norm = math.sqrt(sum(value * value for value in vector.values()))
        return {feature: value / norm for feature, value in vector.items()} if norm else {}

    def _probabilities(self, vector: Mapping[str, float]) -> dict[str, float]:
        scores = {
            name: self.bias[name] + sum(self.weights[name].get(feature, 0.0) * value for feature, value in vector.items())
            for name in self.classes
        }
        return softmax(scores)

    def predict_proba(self, features: Mapping[str, float]) -> dict[str, float]:
        return self._probabilities(self.vectorize(features))

    def contributions(self, features: Mapping[str, float], label: str) -> dict[str, float]:
        """How much each feature pushed the post towards ``label`` rather than the other classes."""
        others = [other for other in self.classes if other != label]
        if not others:
            return {}
        vector = self.vectorize(features)
        return {
            feature: value
            * (self.weights[label].get(feature, 0.0) - sum(self.weights[o].get(feature, 0.0) for o in others) / len(others))
            for feature, value in vector.items()
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "classes": list(self.classes),
            "bias": {k: round(v, 6) for k, v in self.bias.items()},
            "idf": {k: round(v, 6) for k, v in self.idf.items()},  # all of them: they set each post's vector length
            "weights": self.weights,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> LogisticRegression:
        return cls(
            classes=tuple(data["classes"]),  # type: ignore[arg-type]
            idf=dict(data["idf"]),  # type: ignore[arg-type]
            weights={k: dict(v) for k, v in data["weights"].items()},  # type: ignore[union-attr]
            bias=dict(data["bias"]),  # type: ignore[arg-type]
        )
