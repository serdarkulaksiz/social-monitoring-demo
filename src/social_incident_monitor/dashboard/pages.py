"""Data for each dashboard page. Reads only from application services; no detection logic."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from ..application.detection.window_aggregator import GroupKey
from ..application.incident_views import IncidentTimings, IncidentViews
from ..application.quality_service import QualityService, ReviewMode
from ..application.query_service import QueryService
from ..application.topics_service import TopicsService
from ..domain.enums import AlertType, IncidentStatus, IssueCategory, ServiceCategory
from ..domain.models import Incident
from . import charts

SPARK_WINDOW = timedelta(hours=2)
TREND_WINDOW = timedelta(hours=6)
BUCKET_MINUTES = 5
SERVICE_CHOICES = [service for service in ServiceCategory if service not in (ServiceCategory.UNKNOWN, ServiceCategory.REPUTATION)]
ISSUE_CHOICES = [
    issue
    for issue in IssueCategory
    if issue not in (IssueCategory.NONE, IssueCategory.SOLVENCY_RUMOR, IssueCategory.DEPOSIT_FLIGHT, IssueCategory.RUN_CALL)
]


def is_reputation(incident: Incident) -> bool:
    return incident.service_category is ServiceCategory.REPUTATION


class DashboardPages:
    def __init__(
        self,
        *,
        queries: QueryService,
        incidents: IncidentViews,
        quality: QualityService,
        topics: TopicsService,
        actions_mode: str,
    ) -> None:
        self._queries = queries
        self._incidents = incidents
        self._quality = quality
        self._topics = topics
        self._actions_mode = actions_mode

    # ------------------------------------------------------------------ shared

    def _common(self, page: str, *, refresh: int | None = None) -> dict[str, Any]:
        snapshot = self._queries.dashboard()
        return {
            "page": page,
            "refresh": refresh,
            "generated_at": snapshot.stats.generated_at,
            "synthetic_mode": snapshot.stats.synthetic_mode,
            "actions_mode": self._actions_mode,
            "reputation_active": sum(1 for incident in snapshot.active_incidents if is_reputation(incident)),
            "service_choices": SERVICE_CHOICES,
            "issue_choices": ISSUE_CHOICES,
        }

    def _chart(self, key: GroupKey | None, start: datetime, end: datetime, label: str, markers: list[tuple[datetime, str]]) -> Any:
        points = self._queries.people_series(key, start, end, BUCKET_MINUTES)
        expected = self._queries.expected_series(key, [moment for moment, _ in points], BUCKET_MINUTES) if key else None
        return charts.time_chart(points, expected=expected, markers=markers, label=label)

    # ------------------------------------------------------------------ pages

    def overview(self) -> dict[str, Any]:
        context = self._common("overview", refresh=30)
        snapshot = self._queries.dashboard(recent_alerts=40)
        now = snapshot.stats.generated_at
        operational = [incident for incident in snapshot.active_incidents if not is_reputation(incident)]
        rows = []
        for incident in operational:
            key = GroupKey(incident.service_category, incident.issue_category)
            values = [value for _, value in self._queries.people_series(key, now - SPARK_WINDOW, now, BUCKET_MINUTES)]
            rows.append({"incident": incident, "spark": charts.sparkline(values, label="Son 2 saat, 5 dakikalık dilimlerde kişi sayısı")})
        stats = snapshot.stats
        by_service = sorted(stats.complaints_by_service.items(), key=lambda item: -item[1])
        by_issue = sorted(stats.complaints_by_issue.items(), key=lambda item: -item[1])
        locations = self._queries.locations(hours=24)[:10]
        metrics = self._incidents.metrics(30)
        context.update(
            stats=stats,
            incidents=rows,
            alerts=[alert for alert in snapshot.recent_alerts if alert.alert_type is not AlertType.REPUTATION_SIGNAL][:15],
            trend=self._chart(None, now - TREND_WINDOW, now, "Son 6 saatte şikâyet eden kişi sayısı (5 dakikalık dilimler)", []),
            by_service=[(name, count, charts.bar(count, by_service[0][1])) for name, count in by_service],
            by_issue=[(name, count, charts.bar(count, by_issue[0][1])) for name, count in by_issue],
            locations=[(item, charts.bar(item.unique_users, locations[0].unique_users)) for item in locations],
            topics=self._topics.emerging()[:3],
            metrics=metrics,
        )
        return context

    def incident(self, incident_id: str) -> dict[str, Any]:
        incident = self._incidents.get(incident_id)
        context = self._common("reputation" if is_reputation(incident) else "overview")
        now = context["generated_at"]
        evidence = self._incidents.evidence(incident_id, limit=200)
        timeline = self._incidents.timeline(incident_id)
        start = min(incident.opened_at - timedelta(hours=1), (incident.first_complaint_at or incident.opened_at) - timedelta(minutes=15))
        end = min(now, (incident.resolved_at or now) + timedelta(minutes=30))
        markers = [(incident.opened_at, "Olay açıldı")]
        markers += [(moment, text) for moment, text in ((incident.acknowledged_at, "Onaylandı"), (incident.resolved_at, "Çözüldü")) if moment]
        key = GroupKey(incident.service_category, incident.issue_category)
        cities: dict[str, set[str]] = defaultdict(set)
        for item in evidence.posts:
            for location in item.analysis.locations:
                cities[location].add(item.post.author_id)
        city_counts = sorted(((key_, len(users)) for key_, users in cities.items()), key=lambda item: -item[1])
        context.update(
            incident=incident,
            reputation=is_reputation(incident),
            timings=IncidentTimings.of(incident),
            timeline=timeline,
            audit_ok=self._incidents.audit_verified(),
            evidence=evidence,
            cities=[(name, count, charts.bar(count, city_counts[0][1])) for name, count in city_counts],
            chart=self._chart(key, start, end, "Bu olaydaki kişi sayısı (5 dakikalık dilimler)", markers),
            can_acknowledge=incident.status is IncidentStatus.OPEN,
            can_resolve=incident.status.is_active,
        )
        return context

    def report(self, incident_id: str) -> dict[str, Any]:
        context = self.incident(incident_id)
        context["report"] = self._incidents.report(incident_id)
        return context

    def review(self, mode: ReviewMode) -> dict[str, Any]:
        context = self._common("review")
        context.update(mode=mode, modes=list(ReviewMode), items=self._quality.queue(mode, 50))
        return context

    def quality(self) -> dict[str, Any]:
        context = self._common("quality")
        context.update(
            overall=self._quality.report(),
            unbiased=self._quality.report(sample="review_random"),
            model=self._quality.model_activity(),
        )
        return context

    def topics(self) -> dict[str, Any]:
        context = self._common("topics", refresh=60)
        context.update(topics=self._topics.emerging())
        return context

    def reputation(self) -> dict[str, Any]:
        context = self._common("reputation", refresh=60)
        incidents = [incident for incident in self._queries.incidents(status=None, limit=200, offset=0) if is_reputation(incident)]
        active = [incident for incident in incidents if incident.status.is_active]
        recent = [incident for incident in incidents if not incident.status.is_active][:10]
        posts = self._queries.risk_posts(hours=24, limit=50)
        signals = Counter(signal for _, analysis, _ in posts for signal in analysis.risk_signals)
        context.update(active=active, recent=recent, posts=posts, signal_counts=dict(signals.most_common()))
        return context
