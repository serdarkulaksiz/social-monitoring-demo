"""Composition root: the only place that knows which concrete adapters are used."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .application.alert_service import AlertService
from .application.analysis_service import AnalysisService
from .application.audit_service import AuditService
from .application.collector_health_service import CollectorHealthService
from .application.detection.baseline import BaselineCalculator, BaselineRule, BaselineSettings, BaselineThresholds
from .application.detection.engine import DetectionEngine, DetectionPipeline
from .application.detection.reputation import ReputationAggregator, ReputationRule, ReputationThresholds
from .application.detection.rules import AbsoluteThresholdRule, DetectionRule
from .application.detection.window_aggregator import ComplaintWindowAggregator
from .application.engagement_service import EngagementService
from .application.errors import ConfigurationError
from .application.feedback_service import FeedbackAwareAnalyses, FeedbackService
from .application.incident_service import IncidentService
from .application.incident_views import IncidentViews
from .application.ingestion_service import IngestionService
from .application.monitoring_service import MonitoringService
from .application.ports import Classifier, Clock, Notifier
from .application.quality_service import QualityService
from .application.query_service import QueryService
from .application.topics_service import TopicSettings, TopicsService
from .classification.locations import LocationMatcher
from .classification.hybrid import HybridClassifier, HybridSettings
from .classification.model.text_model import ModelError, TextModel
from .classification.rule_based import RuleBasedClassifier, RuleSet
from .classification.terms import TermExtractor
from .config import Settings
from .infrastructure.clock import SystemClock
from .infrastructure.file_lock import ProcessLock
from .infrastructure.jsonl.repositories import (
    JsonlAlertRepository,
    JsonlAnalysisRepository,
    JsonlAuditLog,
    JsonlCollectorRunRepository,
    JsonlEngagementRepository,
    JsonlFeedbackRepository,
    JsonlIncidentRepository,
    JsonlPostRepository,
)
from .infrastructure.notifiers import LogNotifier

LOCK_FILE_NAME = "monitor.lock"


logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    clock: Clock
    lock: ProcessLock
    monitoring: MonitoringService
    queries: QueryService
    incident_views: IncidentViews
    quality: QualityService
    incident_repository: JsonlIncidentRepository
    alert_repository: JsonlAlertRepository
    audit_log: JsonlAuditLog
    analysis_service: AnalysisService
    post_repository: JsonlPostRepository
    feedback_repository: JsonlFeedbackRepository
    topics: TopicsService


def build_classifier(settings: Settings) -> Classifier:
    if settings.classifier == "llm":
        raise ConfigurationError(
            "MONITOR_CLASSIFIER=llm selected, but no LLM provider adapter is installed. "
            "Use MONITOR_CLASSIFIER=rule_based."
        )
    rule_set = RuleSet.load(settings.classification_rules_path)
    rules = RuleBasedClassifier(rule_set, LocationMatcher.load())
    if settings.classifier != "hybrid":
        return rules
    return HybridClassifier(rules, rule_set, load_text_model(settings.resolved_text_model_path), hybrid_settings(settings))


def hybrid_settings(settings: Settings) -> HybridSettings:
    return HybridSettings(mode=settings.text_model_mode, rescue_p=settings.text_model_rescue_p, veto_p=settings.text_model_veto_p)


def load_text_model(path: Path) -> TextModel | None:
    """The model, or None (rules only) when it is missing or damaged — logged, never fatal."""
    try:
        model = TextModel.load(path)
    except ModelError as exc:
        logger.warning("text model not used, classifying with rules only: %s", exc)
        return None
    logger.info("text model %s loaded (%s)", model.version, model.algorithm)
    return model


def build_notifiers(settings: Settings) -> list[Notifier]:
    return [LogNotifier()]


def build_container(settings: Settings, clock: Clock | None = None) -> Container:
    """Acquire the data-directory lock, load storage and wire all services."""
    clock = clock or SystemClock()
    data_dir = settings.data_dir.resolve()
    lock = ProcessLock(data_dir / LOCK_FILE_NAME)
    lock.acquire()
    try:
        return _wire(settings, clock, lock, data_dir)
    except BaseException:
        lock.release()
        raise


def _wire(settings: Settings, clock: Clock, lock: ProcessLock, data_dir: Path) -> Container:
    classifier = build_classifier(settings)
    posts = JsonlPostRepository(data_dir)
    engagement = JsonlEngagementRepository(data_dir)
    analyses = JsonlAnalysisRepository(data_dir)  # classifier output as produced
    feedback = JsonlFeedbackRepository(data_dir)
    effective = FeedbackAwareAnalyses(analyses, feedback)  # what every view and rule reads
    incidents = JsonlIncidentRepository(data_dir)
    alerts = JsonlAlertRepository(data_dir)
    runs = JsonlCollectorRunRepository(data_dir)
    audit_log = JsonlAuditLog(data_dir)

    windows = settings.detection_windows_minutes
    aggregator = ComplaintWindowAggregator(posts, effective)
    baseline = BaselineCalculator(
        posts,
        effective,
        BaselineSettings(
            history_days=settings.baseline_history_days,
            min_history_days=settings.baseline_min_history_days,
            min_expected_users=settings.baseline_min_expected_users,
        ),
    )
    rules: list[DetectionRule] = [AbsoluteThresholdRule(settings.detection_thresholds)]
    operational_windows = list(windows)
    if settings.baseline_enabled:
        rules.append(
            BaselineRule(
                baseline,
                settings.baseline_windows_minutes,
                BaselineThresholds(*settings.baseline_min_users, *settings.baseline_p_values),
            )
        )
        operational_windows += settings.baseline_windows_minutes
    pipelines = [DetectionPipeline(aggregator, rules, operational_windows)]
    if settings.reputation_enabled:
        reputation_rule = ReputationRule(
            settings.reputation_window_minutes,
            ReputationThresholds(*settings.reputation_min_users, *settings.reputation_amplification),
        )
        pipelines.append(
            DetectionPipeline(ReputationAggregator(posts, effective, engagement), [reputation_rule], reputation_rule.windows)
        )
    detection = DetectionEngine(pipelines)
    stale_after = (
        timedelta(minutes=settings.collector_stale_after_minutes) if settings.collector_stale_after_minutes else None
    )
    health = CollectorHealthService(runs, stale_after, settings.collector_failure_threshold)

    max_clock_skew = timedelta(seconds=settings.max_clock_skew_seconds)
    analysis = AnalysisService(classifier, analyses)
    monitoring = MonitoringService(
        clock=clock,
        posts=posts,
        runs=runs,
        ingestion=IngestionService(
            posts, accept_synthetic=settings.accept_synthetic, max_clock_skew=max_clock_skew, engagement=engagement
        ),
        engagement=EngagementService(posts, engagement, max_clock_skew=max_clock_skew),
        analysis=analysis,
        detection=detection,
        incidents=IncidentService(incidents, timedelta(minutes=settings.incident_resolve_after_minutes)),
        alerts=AlertService(alerts, build_notifiers(settings)),
        audit=AuditService(audit_log),
        feedback=FeedbackService(posts, analyses, feedback),
        collector_health=health,
    )
    queries = QueryService(
        clock=clock,
        posts=posts,
        engagement=engagement,
        analyses=effective,
        incidents=incidents,
        alerts=alerts,
        runs=runs,
        aggregator=aggregator,
        baseline=baseline if settings.baseline_enabled else None,
        location_names=LocationMatcher.load().name,
        collector_health=health,
        diagnostics=[posts, engagement, analyses, feedback, incidents, alerts, runs, audit_log],
        windows=windows,
        synthetic_mode=settings.accept_synthetic,
    )
    incident_views = IncidentViews(
        clock=clock, incidents=incidents, posts=posts, analyses=effective, engagement=engagement, audit=audit_log
    )
    quality = QualityService(
        clock=clock,
        posts=posts,
        classifier_results=analyses,
        effective_results=effective,
        feedback=feedback,
        incidents=incidents,
        engagement=engagement,
    )
    stop_stems = TermExtractor.load().stop_stems
    topics = TopicsService(
        clock=clock,
        posts=posts,
        analyses=effective,
        extractor_factory=lambda: TermExtractor(stop_stems),
        settings=TopicSettings(
            window_minutes=settings.topics_window_minutes,
            history_days=settings.topics_history_days,
            min_users=settings.topics_min_users,
        ),
    )
    return Container(
        settings=settings,
        clock=clock,
        lock=lock,
        monitoring=monitoring,
        queries=queries,
        incident_views=incident_views,
        quality=quality,
        incident_repository=incidents,
        alert_repository=alerts,
        audit_log=audit_log,
        analysis_service=analysis,
        post_repository=posts,
        feedback_repository=feedback,
        topics=topics,
    )
