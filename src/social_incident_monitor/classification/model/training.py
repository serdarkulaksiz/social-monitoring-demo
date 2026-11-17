"""Training the text model from label rows, and measuring rules / model / hybrid on a test set."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from ...application.quality_service import ConfusionMatrix, compare
from ...domain.enums import IssueCategory, ServiceCategory
from ...domain.models import AnalysisResult, Post
from ..hybrid import HybridClassifier, HybridSettings
from ..rule_based import RuleBasedClassifier, RuleSet
from .features import FeatureSettings, extract
from .logistic import LogisticRegression
from .naive_bayes import NaiveBayes
from .text_model import NO, YES, Head, TextModel

Algorithm = Literal["nb", "logreg"]
Method = Literal["rules", "model", "hybrid"]
MIN_CLASS_EXAMPLES = 3  # a service or issue needs this many complaints to be learnt


def as_post(row: Mapping[str, Any], now: datetime) -> Post:
    return Post(
        post_id=str(row["post_id"]), source="evaluation", source_post_id=str(row["post_id"]), author_id="evaluation",
        text=str(row["text"]), created_at=now, collected_at=now, ingested_at=now,
    )


def _train_head(examples: Sequence[tuple[Counter[str], str]], algorithm: Algorithm) -> Head | None:
    counts = Counter(label for _features, label in examples)
    kept = [(features, label) for features, label in examples if counts[label] >= MIN_CLASS_EXAMPLES]
    if len({label for _features, label in kept}) < 2:
        return None
    if algorithm == "nb":
        return NaiveBayes.train(kept)
    return LogisticRegression.train(kept)


def train(
    rows: Sequence[Mapping[str, Any]],
    rule_set: RuleSet,
    algorithm: Algorithm,
    *,
    features: FeatureSettings = FeatureSettings(),
    now: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> TextModel:
    now = now or datetime.now(UTC)
    rules = RuleBasedClassifier(rule_set)
    bank: list[tuple[Counter[str], str]] = []
    complaint: list[tuple[Counter[str], str]] = []
    service: list[tuple[Counter[str], str]] = []
    issue: list[tuple[Counter[str], str]] = []
    for row in rows:
        label = row["label"]
        vector = extract(str(row["text"]), rules.classify(as_post(row, now), now), features)
        bank.append((vector, YES if label["is_bank_related"] else NO))
        if not label["is_bank_related"]:
            continue
        complaint.append((vector, YES if label["is_complaint"] else NO))
        if label["is_complaint"]:
            service.append((vector, str(label["service_category"])))
            issue.append((vector, str(label["issue_category"])))
    heads = {name: head for name, head in (
        ("bank", _train_head(bank, algorithm)),
        ("complaint", _train_head(complaint, algorithm)),
        ("service", _train_head(service, algorithm)),
        ("issue", _train_head(issue, algorithm)),
    ) if head is not None}
    if "complaint" not in heads:
        raise ValueError("the training data needs complaints and non-complaints about the bank (at least 3 of each)")
    info = dict(metadata or {})
    info.update({
        "rules_version": rule_set.version,
        "created_at": now.isoformat(),
        "training_rows": len(rows),
        "label_counts": {
            "bank_related": sum(1 for _v, label in bank if label == YES),
            "not_bank_related": sum(1 for _v, label in bank if label == NO),
            "complaints": len(service),
            "services": dict(Counter(label for _v, label in service)),
            "issues": dict(Counter(label for _v, label in issue)),
        },
    })
    return TextModel(version=f"{algorithm}-{now:%Y%m%dT%H%M%SZ}", algorithm=algorithm, features=features, heads=heads, metadata=info)


@dataclass
class Scores:
    method: str
    rows: int
    bank: ConfusionMatrix
    complaint: ConfusionMatrix
    category_accuracy: float | None
    by_sample: dict[str, ConfusionMatrix] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "rows": self.rows,
            "bank": _matrix(self.bank),
            "complaint": _matrix(self.complaint),
            "category_accuracy": _round(self.category_accuracy),
            "by_sample": {name: _matrix(matrix) for name, matrix in self.by_sample.items()},
        }


def _matrix(matrix: ConfusionMatrix) -> dict[str, Any]:
    return {
        "tp": matrix.true_positive, "fp": matrix.false_positive, "fn": matrix.false_negative, "tn": matrix.true_negative,
        "precision": _round(matrix.precision), "recall": _round(matrix.recall), "f1": _round(matrix.f1),
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _model_only(model: TextModel, ruled: AnalysisResult, text: str) -> tuple[bool, bool, str, str]:
    prediction = model.predict(text, ruled)
    is_bank = ruled.is_bank_related if prediction.p_bank is None else prediction.p_bank >= 0.5
    is_complaint = is_bank and prediction.p_complaint is not None and prediction.p_complaint >= 0.5
    service = prediction.service[0] if prediction.service else ServiceCategory.UNKNOWN.value
    issue = prediction.issue[0] if prediction.issue else IssueCategory.OTHER.value
    if not is_complaint:
        issue = IssueCategory.NONE.value
    return is_bank, is_complaint, service, issue


def evaluate(
    rows: Sequence[Mapping[str, Any]],
    rule_set: RuleSet,
    method: Method,
    model: TextModel | None = None,
    hybrid: HybridSettings | None = None,
) -> Scores:
    """Decisions of one method on labelled rows, against the labels."""
    if method != "rules" and model is None:
        raise ValueError(f"method {method} needs a model")
    now = datetime.now(UTC)
    rules = RuleBasedClassifier(rule_set)
    combined = HybridClassifier(rules, rule_set, model, hybrid or HybridSettings(mode="active")) if model else None
    bank_pairs: list[tuple[bool, bool]] = []
    complaint_pairs: list[tuple[bool, str, str, bool, str, str]] = []
    by_sample: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for row in rows:
        label = row["label"]
        post = as_post(row, now)
        if method == "rules":
            result = rules.classify(post, now)
            predicted = (result.is_bank_related, result.is_complaint, result.service_category.value, result.issue_category.value)
        elif method == "model":
            assert model is not None
            predicted = _model_only(model, rules.classify(post, now), post.text)
        else:
            assert combined is not None
            result = combined.classify(post, now)
            predicted = (result.is_bank_related, result.is_complaint, result.service_category.value, result.issue_category.value)
        bank_pairs.append((predicted[0], bool(label["is_bank_related"])))
        complaint_pairs.append((predicted[1], predicted[2], predicted[3], bool(label["is_complaint"]),
                                str(label["service_category"]), str(label["issue_category"])))
        by_sample[str(row.get("sample", "other"))].append((predicted[1], bool(label["is_complaint"])))
    complaint, category_accuracy, _by_service = compare(complaint_pairs)
    return Scores(
        method=method,
        rows=len(rows),
        bank=ConfusionMatrix.of(bank_pairs),
        complaint=complaint,
        category_accuracy=category_accuracy,
        by_sample={name: ConfusionMatrix.of(pairs) for name, pairs in sorted(by_sample.items())},
    )


def gate(candidate: Scores, current: Scores, tolerance: float = 0.0) -> tuple[bool, str]:
    """A new model may replace the current one only if complaint F1 and recall do not drop."""
    reasons = []
    for name in ("f1", "recall"):
        new = getattr(candidate.complaint, name) or 0.0
        old = getattr(current.complaint, name) or 0.0
        if new + tolerance < old:
            reasons.append(f"complaint {name} {new:.3f} < current {old:.3f}")
    if reasons:
        return False, "; ".join(reasons)
    return True, (
        f"complaint f1 {candidate.complaint.f1 or 0:.3f} (current {current.complaint.f1 or 0:.3f}), "
        f"recall {candidate.complaint.recall or 0:.3f} (current {current.complaint.recall or 0:.3f})"
    )
