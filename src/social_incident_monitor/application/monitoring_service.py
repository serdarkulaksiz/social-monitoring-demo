"""Use-case coordinator for every state-changing operation of the monitor.

All writes (batch ingestion, collector run reports, periodic evaluation, incident
acknowledge/resolve) are serialised with one in-process lock, so request threads and
the background evaluator never interleave. Together with the data-directory process
lock this gives the single-writer guarantee the JSONL storage relies on.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ..domain.enums import AuditEventType
from ..domain.models import Alert, CollectorRun, Feedback, Incident, IncidentAuditEvent
from .alert_service import AlertService
from .analysis_service import AnalysisService
from .audit_service import AuditService
from .collector_health_service import CollectorHealthService
from .detection.engine import DetectionEngine
from .engagement_service import EngagementOutcome, EngagementService, IncomingEngagement
from .feedback_service import FeedbackService, Label
from .incident_service import IncidentEventType, IncidentService
from .ingestion_service import IncomingPost, IngestionOutcome, IngestionService, Rejection
from .ports import Clock, CollectorRunRepository, PostRepository

logger = logging.getLogger(__name__)


@dataclass
class EvaluationSummary:
    evaluated_at: datetime
    signals: int = 0
    opened: list[Incident] = field(default_factory=list)
    escalated: list[Incident] = field(default_factory=list)
    resolved: list[Incident] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


@dataclass(frozen=True)
class BatchResult:
    ingestion: IngestionOutcome
    evaluation: EvaluationSummary


@dataclass(frozen=True)
class EngagementBatchResult:
    engagement: EngagementOutcome
    evaluation: EvaluationSummary


class MonitoringService:
    def __init__(
        self,
        *,
        clock: Clock,
        posts: PostRepository,
        runs: CollectorRunRepository,
        ingestion: IngestionService,
        engagement: EngagementService,
        analysis: AnalysisService,
        detection: DetectionEngine,
        incidents: IncidentService,
        alerts: AlertService,
        audit: AuditService,
        feedback: FeedbackService,
        collector_health: CollectorHealthService,
    ) -> None:
        self._clock = clock
        self._posts = posts
        self._runs = runs
        self._ingestion = ingestion
        self._engagement = engagement
        self._analysis = analysis
        self._detection = detection
        self._incidents = incidents
        self._alerts = alerts
        self._audit = audit
        self._feedback = feedback
        self._collector_health = collector_health
        self._lock = threading.RLock()

    def process_batch(
        self, batch_id: str, items: Sequence[IncomingPost], prior_rejections: Sequence[Rejection] = ()
    ) -> BatchResult:
        with self._lock:
            now = self._clock.now()
            outcome = self._ingestion.ingest(batch_id, items, now, prior_rejections)
            self._analysis.analyze(outcome.accepted, now)
            logger.info(
                "batch %s: received=%d accepted=%d duplicates=%d rejected=%d",
                batch_id, outcome.received, len(outcome.accepted), outcome.duplicates, len(outcome.rejected),
            )
            return BatchResult(outcome, self._evaluate_locked(now))

    def process_engagement(
        self, batch_id: str, items: Sequence[IncomingEngagement], prior_rejections: Sequence[Rejection] = ()
    ) -> EngagementBatchResult:
        with self._lock:
            now = self._clock.now()
            outcome = self._engagement.apply(items, now, prior_rejections)
            logger.info(
                "engagement batch %s: received=%d applied=%d stale=%d unknown=%d rejected=%d",
                batch_id, outcome.received, outcome.applied, outcome.stale, outcome.unknown, len(outcome.rejected),
            )
            return EngagementBatchResult(outcome, self._evaluate_locked(now))

    def record_collector_run(self, run: CollectorRun) -> bool:
        """Store a run report; ``received_at`` is always stamped with the monitor's clock."""
        with self._lock:
            now = self._clock.now()
            stored = self._runs.add(run.model_copy(update={"received_at": now}))
            if stored:
                logger.info("collector run %s from %s: status=%s", run.run_id, run.source, run.status.value)
                self._evaluate_locked(now)
            return stored

    def evaluate(self) -> EvaluationSummary:
        with self._lock:
            return self._evaluate_locked(self._clock.now())

    def recover(self) -> int:
        """Classify posts stored before a crash interrupted their analysis."""
        with self._lock:
            return self._analysis.analyze_missing(self._posts, self._clock.now())

    def acknowledge_incident(self, incident_id: str, actor: str, note: str | None = None) -> Incident:
        with self._lock:
            now = self._clock.now()
            event = self._incidents.acknowledge(incident_id, now, actor, note)
            self._audit.record_incident_events([event], now)
            self._alerts.handle_incident_events([event], now)
            return event.incident

    def resolve_incident(self, incident_id: str, actor: str, note: str | None = None) -> Incident:
        with self._lock:
            now = self._clock.now()
            event = self._incidents.resolve(incident_id, now, actor, note)
            self._audit.record_incident_events([event], now)
            self._alerts.handle_incident_events([event], now)
            return event.incident

    def add_incident_note(self, incident_id: str, actor: str, text: str) -> IncidentAuditEvent:
        with self._lock:
            self._incidents.get(incident_id)  # raises NotFoundError for unknown incidents
            return self._audit.record(incident_id, AuditEventType.NOTE_ADDED, self._clock.now(), actor, "note added", {"note": text})

    def submit_feedback(self, post_id: str, label: Label, actor: str, note: str | None = None) -> Feedback:
        """Store an analyst label, note it on affected active incidents and re-evaluate at once."""
        with self._lock:
            now = self._clock.now()
            feedback = self._feedback.submit(post_id, label, actor, note, now)
            groups = {(feedback.service_category, feedback.issue_category), (feedback.predicted_service, feedback.predicted_issue)}
            details = {
                "post_id": post_id,
                "verdict": feedback.verdict,
                "from": _label_summary(feedback.predicted_is_complaint, feedback.predicted_service.value, feedback.predicted_issue.value),
                "to": _label_summary(feedback.is_complaint, feedback.service_category.value, feedback.issue_category.value),
            }
            if note:
                details["note"] = note
            for incident in self._incidents.list_active():
                if (incident.service_category, incident.issue_category) in groups:
                    self._audit.record(
                        incident.incident_id, AuditEventType.FEEDBACK_APPLIED, now, actor, f"post relabelled: {feedback.verdict}", details
                    )
            self._evaluate_locked(now)
            return feedback

    def _evaluate_locked(self, now: datetime) -> EvaluationSummary:
        signals = self._detection.detect(now)
        events = self._incidents.reconcile(signals, now)
        self._audit.record_incident_events(events, now)
        summary = EvaluationSummary(evaluated_at=now, signals=len(signals))
        summary.alerts.extend(self._alerts.handle_incident_events(events, now))
        for event in events:
            if event.type is IncidentEventType.OPENED:
                summary.opened.append(event.incident)
            elif event.type is IncidentEventType.ESCALATED:
                summary.escalated.append(event.incident)
            elif event.type is IncidentEventType.RESOLVED:
                summary.resolved.append(event.incident)
        for health in self._collector_health.evaluate(now):
            alert = self._alerts.sync_collector_health(health.source, health.alert_condition, health.detail, now)
            if alert is not None:
                summary.alerts.append(alert)
        return summary


def _label_summary(is_complaint: bool, service: str, issue: str) -> dict[str, object]:
    return {"is_complaint": is_complaint, "service": service, "issue": issue}
