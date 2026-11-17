"""Small numeric helpers shared by the models."""

from __future__ import annotations

import math
from collections.abc import Mapping


def softmax(scores: Mapping[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values())
    exps = {label: math.exp(score - top) for label, score in scores.items()}
    total = sum(exps.values())
    return {label: value / total for label, value in exps.items()}
