"""Deterministic, explainable keyword/pattern classifier for Turkish posts.

The ``confidence`` value produced here is a heuristic rule score in [0, 1] that grows
with the amount of corroborating evidence (explicit issue phrase, service keyword,
bank name). It is NOT a calibrated probability and is labelled with
``confidence_method = "heuristic_rule_score"``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from ..application.errors import ConfigurationError
from ..domain.enums import REPUTATION_ISSUES, ComplaintSeverity, IssueCategory, ServiceCategory
from ..domain.models import AnalysisResult, Post
from .locations import LocationMatcher
from .text_normalizer import fold

CLASSIFIER_TYPE = "rule_based"
CONFIDENCE_METHOD = "heuristic_rule_score"
_WORD_START = r"(?<![a-z0-9])"
_HASHTAG = re.compile(r"#\w+")
FUND_TOPIC = "fund"
_CHANNELS = frozenset({
    ServiceCategory.MOBILE_BANKING, ServiceCategory.INTERNET_BANKING, ServiceCategory.ATM,
    ServiceCategory.CARD, ServiceCategory.POS, ServiceCategory.TRANSFER,
})


def _compile(patterns: list[str]) -> re.Pattern[str]:
    if not patterns:
        return re.compile(r"(?!x)x")  # never matches
    return re.compile("|".join(f"{_WORD_START}(?:{pattern})" for pattern in patterns))


@dataclass(frozen=True)
class _ServiceRule:
    category: ServiceCategory
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class _IssueRule:
    category: IssueCategory
    severity: ComplaintSeverity
    implies_service: ServiceCategory | None
    yields_to_service_default: bool
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class RuleSet:
    version: str
    brand: re.Pattern[str]
    bank_entity: re.Pattern[str]
    exclusions: tuple[tuple[str, re.Pattern[str]], ...]
    services: tuple[_ServiceRule, ...]
    issues: tuple[_IssueRule, ...]
    service_default_issues: dict[ServiceCategory, IssueCategory]
    generic_problem: re.Pattern[str]
    question: re.Pattern[str]
    resolution: re.Pattern[str]
    reputation: tuple[tuple[str, re.Pattern[str]], ...] = ()
    other_institutions: re.Pattern[str] = re.compile(r"(?!x)x")  # e.g. "ziraat katilim": a separate institution
    other_banks: re.Pattern[str] = re.compile(r"(?!x)x")  # recorded only, as a hint for the text model
    official_accounts: frozenset[str] = frozenset()  # the bank's own handles: never complaints
    all_channels: re.Pattern[str] = re.compile(r"(?!x)x")  # "tüm sistem", "hiçbir işlem": a general outage
    topics: tuple[tuple[str, re.Pattern[str]], ...] = ()  # e.g. ("fund", …): never counted as complaints

    def issue_severity(self, issue: IssueCategory) -> ComplaintSeverity:
        for rule in self.issues:
            if rule.category == issue:
                return rule.severity
        return ComplaintSeverity.LOW

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> RuleSet:
        try:
            return cls(
                version=str(data["rules_version"]),
                brand=_compile(data["brand_patterns"]),
                bank_entity=_compile(data["bank_entity_patterns"]),
                exclusions=tuple((item["name"], _compile(item["patterns"])) for item in data["exclusion_contexts"]),
                services=tuple(
                    _ServiceRule(ServiceCategory(item["category"]), _compile(item["patterns"])) for item in data["services"]
                ),
                issues=tuple(
                    _IssueRule(
                        category=IssueCategory(item["category"]),
                        severity=ComplaintSeverity(item["severity"]),
                        implies_service=ServiceCategory(item["implies_service"]) if item.get("implies_service") else None,
                        yields_to_service_default=bool(item.get("yields_to_service_default", False)),
                        pattern=_compile(item["patterns"]),
                    )
                    for item in data["issues"]
                ),
                service_default_issues={
                    ServiceCategory(service): IssueCategory(issue)
                    for service, issue in data["service_default_issues"].items()
                },
                generic_problem=_compile(data["generic_problem_patterns"]),
                question=_compile(data["question_patterns"]),
                resolution=_compile(data["resolution_patterns"]),
                reputation=tuple(
                    (_reputation_name(item["name"]), _compile(item["patterns"])) for item in data.get("reputation_signals", [])
                ),
                other_institutions=_compile(data.get("other_institution_patterns", [])),
                other_banks=_compile(data.get("other_bank_patterns", [])),
                official_accounts=frozenset(str(handle).lower().lstrip("@") for handle in data.get("official_accounts", [])),
                all_channels=_compile(data.get("all_channels_patterns", [])),
                topics=tuple((str(name), _compile(patterns)) for name, patterns in data.get("topics", {}).items()),
            )
        except (KeyError, TypeError, ValueError, re.error) as exc:
            raise ConfigurationError(f"invalid classification rules: {exc}") from exc

    @classmethod
    def load(cls, path: Path | None = None) -> RuleSet:
        try:
            if path is None:
                text = resources.files(__package__).joinpath("rules_tr.json").read_text(encoding="utf-8")
            else:
                text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"cannot load classification rules from {path or 'package default'}: {exc}") from exc
        return cls.from_mapping(data)


def _reputation_name(name: object) -> str:
    allowed = {issue.value for issue in REPUTATION_ISSUES}
    if name not in allowed:
        raise ValueError(f"reputation signal name must be one of {sorted(allowed)}, got {name!r}")
    return str(name)


@dataclass
class _Evidence:
    brand: bool = False
    entity: bool = False
    exclusion: str | None = None
    other_institution: bool = False
    other_bank: bool = False
    official_account: bool = False
    all_channels: bool = False
    topics: tuple[str, ...] = ()
    services: tuple[ServiceCategory, ...] = ()
    issue: _IssueRule | None = None
    generic_problem: bool = False
    question: bool = False
    resolution: bool = False
    risk: tuple[str, ...] = ()


class RuleBasedClassifier:
    classifier_type = CLASSIFIER_TYPE

    def __init__(self, rules: RuleSet, locations: LocationMatcher | None = None) -> None:
        self._rules = rules
        self._locations = locations

    def classify(self, post: Post, analyzed_at: datetime) -> AnalysisResult:
        folded = fold(post.text)
        evidence = self._collect_evidence(folded, fold(_HASHTAG.sub(" ", post.text)))
        evidence.official_account = (post.author_handle or "").lower().lstrip("@") in self._rules.official_accounts
        if evidence.official_account:
            evidence.risk = ()
        is_bank_related = self._is_bank_related(evidence) or bool(evidence.risk) or evidence.official_account
        is_complaint = is_bank_related and not evidence.official_account and self._is_complaint(evidence)
        service = self._service(evidence) if is_bank_related else ServiceCategory.UNKNOWN
        issue = self._issue(evidence, service) if is_complaint else IssueCategory.NONE
        severity = self._rules.issue_severity(issue) if is_complaint else ComplaintSeverity.NONE
        return AnalysisResult(
            post_id=post.post_id,
            is_bank_related=is_bank_related,
            is_complaint=is_complaint,
            service_category=service,
            issue_category=issue,
            severity=severity,
            confidence=self._score(evidence, is_bank_related, is_complaint),
            confidence_method=CONFIDENCE_METHOD,
            classifier_type=CLASSIFIER_TYPE,
            classifier_version=self._rules.version,
            matched_rules=self._matched_rules(evidence),
            analyzed_at=analyzed_at,
            risk_signals=evidence.risk,
            locations=self._find_locations(folded, evidence, is_bank_related),
        )

    def _find_locations(self, folded: str, evidence: _Evidence, is_bank_related: bool) -> tuple[str, ...]:
        """Places in posts that name the bank (also ones the rules could not place), never in sports/agriculture posts."""
        if self._locations is None or not evidence.brand or (evidence.exclusion and not is_bank_related):
            return ()
        return self._locations.find(folded)

    def _collect_evidence(self, text: str, body: str) -> _Evidence:
        """``text``: the folded post; ``body``: the same without hashtags (for problem phrases)."""
        rules = self._rules
        without_others = rules.other_institutions.sub(" ", text)
        evidence = _Evidence(
            brand=bool(rules.brand.search(without_others)),
            other_institution=without_others != text,
            other_bank=bool(rules.other_banks.search(text)),
        )
        if not evidence.brand:
            return evidence
        evidence.entity = bool(rules.bank_entity.search(text))
        evidence.exclusion = next((name for name, pattern in rules.exclusions if pattern.search(text)), None)
        evidence.services = tuple(rule.category for rule in rules.services if rule.pattern.search(text))
        evidence.issue = next((rule for rule in rules.issues if rule.pattern.search(body)), None)
        evidence.generic_problem = bool(rules.generic_problem.search(body))
        evidence.question = bool(rules.question.search(body))
        evidence.resolution = bool(rules.resolution.search(body))
        evidence.all_channels = bool(rules.all_channels.search(body))
        evidence.topics = tuple(name for name, pattern in rules.topics if pattern.search(text))
        excluded = evidence.exclusion and not evidence.entity and not evidence.services and evidence.issue is None
        if not excluded:
            evidence.risk = tuple(name for name, pattern in rules.reputation if pattern.search(body))
        return evidence

    @staticmethod
    def _is_bank_related(evidence: _Evidence) -> bool:
        if not evidence.brand:
            return False
        has_banking_detail = bool(evidence.services) or evidence.issue is not None
        if evidence.exclusion and not has_banking_detail:
            return False
        return evidence.entity or has_banking_detail

    @staticmethod
    def _is_complaint(evidence: _Evidence) -> bool:
        if FUND_TOPIC in evidence.topics:
            return False  # fund liquidation and investment funds: its own topic, never an alarm
        if evidence.resolution and evidence.issue is None:
            return False
        if evidence.issue is not None:
            return True
        # A question without an explicit problem phrase is an inquiry, not a complaint.
        return evidence.generic_problem and not evidence.question

    @staticmethod
    def _service(evidence: _Evidence) -> ServiceCategory:
        outage = evidence.issue is not None and evidence.issue.category is IssueCategory.APPLICATION_UNAVAILABLE
        channels = set(evidence.services) & _CHANNELS
        if outage and (evidence.all_channels or len(channels) >= 2 or not evidence.services):
            return ServiceCategory.ALL_CHANNELS  # "Ziraat çöktü", "mobil, ATM ve POS çalışmıyor"
        if evidence.services:
            return evidence.services[0]
        if evidence.issue is not None and evidence.issue.implies_service is not None:
            return evidence.issue.implies_service
        return ServiceCategory.UNKNOWN

    def _issue(self, evidence: _Evidence, service: ServiceCategory) -> IssueCategory:
        service_default = self._rules.service_default_issues.get(service, IssueCategory.OTHER)
        if evidence.issue is None:
            return service_default
        if evidence.issue.yields_to_service_default and service_default not in (IssueCategory.OTHER,):
            return service_default
        return evidence.issue.category

    @staticmethod
    def _score(evidence: _Evidence, is_bank_related: bool, is_complaint: bool) -> float:
        if not evidence.brand:
            score = 0.9
        elif not is_bank_related:
            score = 0.75 if evidence.exclusion else 0.6
        elif not is_complaint:
            score = 0.7 if (evidence.question or evidence.resolution) else 0.6
        else:
            score = 0.5
            score += 0.25 if evidence.issue is not None else 0.0
            score += 0.1 if evidence.services else 0.0
            score += 0.05 if evidence.entity else 0.0
            score -= 0.15 if evidence.question else 0.0
        return round(min(max(score, 0.05), 0.95), 2)

    @staticmethod
    def _matched_rules(evidence: _Evidence) -> tuple[str, ...]:
        matched: list[str] = []
        if evidence.official_account:
            matched.append("official_account")
        if evidence.other_institution:
            matched.append("other_institution")
        if evidence.other_bank:
            matched.append("other_bank")
        matched.extend(f"topic:{name}" for name in evidence.topics)
        if evidence.all_channels:
            matched.append("all_channels")
        if evidence.brand:
            matched.append("brand")
        if evidence.entity:
            matched.append("bank_entity")
        if evidence.exclusion:
            matched.append(f"exclusion:{evidence.exclusion}")
        matched.extend(f"service:{service.value}" for service in evidence.services)
        if evidence.issue is not None:
            matched.append(f"issue:{evidence.issue.category.value}")
        if evidence.generic_problem:
            matched.append("generic_problem")
        if evidence.question:
            matched.append("question")
        if evidence.resolution:
            matched.append("resolution")
        matched.extend(f"risk:{name}" for name in evidence.risk)
        return tuple(matched)
