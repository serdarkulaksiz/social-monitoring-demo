"""Read-only views used by the API and the dashboard. Contains no detection logic."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain.enums import AlertStatus, AlertType, IncidentStatus, ServiceCategory
from ..domain.models import Alert, AnalysisResult, CollectorRun, EngagementSnapshot, Incident, Post
from .collector_health_service import CollectorHealth, CollectorHealthService
from .detection.baseline import BaselineCalculator, BaselineStatus
from .detection.window_aggregator import ComplaintWindowAggregator, GroupKey
from .ports import (
    AlertRepository,
    AnalysisRepository,
    Clock,
    CollectorRunRepository,
    EngagementRepository,
    IncidentRepository,
    PostRepository,
    StorageDiagnostics,
)

_LIST_ALL = 10_000


@dataclass(frozen=True)
class GroupDensity:
    service: str
    issue: str
    complaints_count: int
    unique_users_count: int


@dataclass(frozen=True)
class WindowDensity:
    window_minutes: int
    complaints_count: int
    unique_users_count: int
    groups: list[GroupDensity]


@dataclass(frozen=True)
class StatsSnapshot:
    generated_at: datetime
    synthetic_mode: bool
    total_posts: int
    analyzed_posts: int
    bank_related_posts: int
    complaints: int
    complaints_by_service: dict[str, int]
    complaints_by_issue: dict[str, int]
    windows: list[WindowDensity]
    open_incidents: int
    active_alerts: int
    collector_health: list[CollectorHealth]
    storage_corrupt_lines: dict[str, int] = field(default_factory=dict)
    baseline: BaselineStatus | None = None  # None when the baseline rule is disabled


@dataclass(frozen=True)
class LocationCount:
    key: str
    name: str
    unique_users: int
    posts: int


@dataclass(frozen=True)
class DashboardSnapshot:
    stats: StatsSnapshot
    active_incidents: list[Incident]
    recent_alerts: list[Alert]


class QueryService:
    def __init__(
        self,
        *,
        clock: Clock,
        posts: PostRepository,
        engagement: EngagementRepository,
        analyses: AnalysisRepository,
        incidents: IncidentRepository,
        alerts: AlertRepository,
        runs: CollectorRunRepository,
        aggregator: ComplaintWindowAggregator,
        collector_health: CollectorHealthService,
        baseline: BaselineCalculator | None = None,
        location_names: Callable[[str], str] = str,
        diagnostics: Sequence[StorageDiagnostics],
        windows: Sequence[int],
        synthetic_mode: bool,
    ) -> None:
        self._clock = clock
        self._posts = posts
        self._engagement = engagement
        self._analyses = analyses
        self._incidents = incidents
        self._alerts = alerts
        self._runs = runs
        self._aggregator = aggregator
        self._baseline = baseline
        self._location_names = location_names
        self._collector_health = collector_health
        self._diagnostics = list(diagnostics)
        self._windows = sorted(set(windows))
        self._synthetic_mode = synthetic_mode

    def posts(self, *, source: str | None, created_since: datetime | None, limit: int, offset: int) -> list[Post]:
        return self._posts.find(source=source, created_since=created_since, limit=limit, offset=offset)

    def engagement(self, post_id: str) -> EngagementSnapshot | None:
        return self._engagement.get(post_id)

    def analyses(
        self, *, is_complaint: bool | None, service: ServiceCategory | None, limit: int, offset: int
    ) -> list[AnalysisResult]:
        return self._analyses.find(is_complaint=is_complaint, service_category=service, limit=limit, offset=offset)

    def incidents(self, *, status: IncidentStatus | None, limit: int, offset: int) -> list[Incident]:
        return self._incidents.find(status=status, limit=limit, offset=offset)

    def incident(self, incident_id: str) -> Incident | None:
        return self._incidents.get(incident_id)

    def alerts(
        self, *, alert_type: AlertType | None, status: AlertStatus | None, incident_id: str | None, limit: int, offset: int
    ) -> list[Alert]:
        return self._alerts.find(alert_type=alert_type, status=status, incident_id=incident_id, limit=limit, offset=offset)

    def collector_runs(self, *, source: str | None, limit: int, offset: int) -> list[CollectorRun]:
        return self._runs.find(source=source, limit=limit, offset=offset)

    def stats(self) -> StatsSnapshot:
        now = self._clock.now()
        results = list(self._analyses.iter_all())
        complaints = [result for result in results if result.is_complaint]
        corrupt: dict[str, int] = {}
        for repository in self._diagnostics:
            corrupt.update(repository.diagnostics())
        return StatsSnapshot(
            generated_at=now,
            synthetic_mode=self._synthetic_mode,
            total_posts=self._posts.count(),
            analyzed_posts=len(results),
            bank_related_posts=sum(1 for result in results if result.is_bank_related),
            complaints=len(complaints),
            complaints_by_service=dict(Counter(result.service_category.value for result in complaints).most_common()),
            complaints_by_issue=dict(Counter(result.issue_category.value for result in complaints).most_common()),
            windows=self._window_density(now),
            open_incidents=len(self._incidents.list_active()),
            active_alerts=len(self._alerts.find(status=AlertStatus.ACTIVE, limit=_LIST_ALL)),
            collector_health=self._collector_health.evaluate(now),
            storage_corrupt_lines=corrupt,
            baseline=self._baseline.status(now) if self._baseline is not None else None,
        )

    def locations(self, *, hours: int, complaints_only: bool = True) -> list[LocationCount]:
        """Provinces mentioned in recent bank-related posts, by distinct people."""
        now = self._clock.now()
        people: dict[str, set[str]] = defaultdict(set)
        posts: Counter[str] = Counter()
        for post in self._posts.find_created_between(now - timedelta(hours=hours), now):
            analysis = self._analyses.get(post.post_id)
            if analysis is None or not analysis.locations or (complaints_only and not analysis.is_complaint):
                continue
            for key in analysis.locations:
                people[key].add(post.author_id)
                posts[key] += 1
        counts = [LocationCount(key, self._location_names(key), len(users), posts[key]) for key, users in people.items()]
        return sorted(counts, key=lambda item: (-item.unique_users, item.name))

    def people_series(
        self, key: GroupKey | None, start: datetime, end: datetime, bucket_minutes: int = 5
    ) -> list[tuple[datetime, int]]:
        """Distinct people per time bucket: complaints of one group (all complaints when ``key`` is None),
        or posts carrying a reputation signal when ``key`` is a reputation group."""
        bucket = timedelta(minutes=bucket_minutes)
        first = _floor(start, bucket_minutes)
        people: dict[datetime, set[str]] = defaultdict(set)
        for post in self._posts.find_created_between(first - timedelta(microseconds=1), end):
            analysis = self._analyses.get(post.post_id)
            if analysis is None or not _in_group(analysis, key):
                continue
            people[_floor(post.created_at, bucket_minutes)].add(post.author_id)
        series = []
        moment = first
        while moment <= end:
            series.append((moment, len(people.get(moment, ()))))
            moment += bucket
        return series

    def expected_series(self, key: GroupKey, buckets: Sequence[datetime], bucket_minutes: int = 5) -> list[float] | None:
        """Baseline expectation for each bucket of a complaint group; None until the baseline is ready."""
        if self._baseline is None or key.service is ServiceCategory.REPUTATION:
            return None
        now = self._clock.now()
        rates = [self._baseline.rate_per_bucket(key, moment, now) for moment in buckets]
        if any(rate is None for rate in rates):
            return None
        return [rate * bucket_minutes / 15 for rate in rates if rate is not None]

    def risk_posts(self, *, hours: int, limit: int) -> list[tuple[Post, AnalysisResult, EngagementSnapshot | None]]:
        """Recent posts with a reputation signal, the most widely shared first."""
        now = self._clock.now()
        found = []
        for post in self._posts.find_created_between(now - timedelta(hours=hours), now):
            analysis = self._analyses.get(post.post_id)
            if analysis is not None and analysis.risk_signals:
                found.append((post, analysis, self._engagement.get(post.post_id)))
        found.sort(key=lambda item: (item[2].amplification if item[2] else 0, item[0].created_at), reverse=True)
        return found[:limit]

    def dashboard(self, recent_alerts: int = 20) -> DashboardSnapshot:
        return DashboardSnapshot(
            stats=self.stats(),
            active_incidents=sorted(self._incidents.list_active(), key=lambda incident: incident.updated_at, reverse=True),
            recent_alerts=self._alerts.find(limit=recent_alerts),
        )

    def _window_density(self, now: datetime) -> list[WindowDensity]:
        grouped = self._aggregator.aggregate(now, self._windows)
        totals = self._aggregator.totals(now, self._windows)
        densities = []
        for minutes in self._windows:
            groups = [
                GroupDensity(key.service.value, key.issue.value, stats[minutes].complaints_count, stats[minutes].unique_users_count)
                for key, stats in sorted(grouped.items())
                if stats[minutes].complaints_count > 0
            ]
            groups.sort(key=lambda group: group.unique_users_count, reverse=True)
            total = totals[minutes]
            densities.append(WindowDensity(minutes, total.complaints_count, total.unique_users_count, groups))
        return densities


def _floor(moment: datetime, minutes: int) -> datetime:
    epoch_minutes = int(moment.timestamp() // 60)
    return datetime.fromtimestamp((epoch_minutes - epoch_minutes % minutes) * 60, tz=moment.tzinfo)


def _in_group(analysis: AnalysisResult, key: GroupKey | None) -> bool:
    if key is None:
        return analysis.is_complaint
    if key.service is ServiceCategory.REPUTATION:
        return key.issue.value in analysis.risk_signals
    return analysis.is_complaint and analysis.service_category == key.service and analysis.issue_category == key.issue
