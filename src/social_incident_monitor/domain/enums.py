"""Domain vocabularies.

Categories are extended by adding a member here and referencing its value in the
classification rules file; no other business logic needs to change.
"""

from __future__ import annotations

from enum import StrEnum


class ServiceCategory(StrEnum):
    MOBILE_BANKING = "mobile_banking"
    INTERNET_BANKING = "internet_banking"
    TRANSFER = "fast_eft_transfer"
    ATM = "atm"
    CARD = "card"
    PAYMENTS = "payments"
    CUSTOMER_SERVICE = "customer_service"
    ALL_CHANNELS = "all_channels"  # the bank as a whole is down (mobile, ATM, cards, POS together)
    POS = "pos"  # merchants' POS terminals and settlement
    OTHER = "other"
    UNKNOWN = "unknown"
    REPUTATION = "reputation"  # not a banking service: reputation / deposit-risk signals

    @property
    def label(self) -> str:
        return _SERVICE_LABELS[self]


_SERVICE_LABELS = {
    ServiceCategory.MOBILE_BANKING: "Mobile Banking",
    ServiceCategory.INTERNET_BANKING: "Internet Banking",
    ServiceCategory.TRANSFER: "FAST / EFT / Transfer",
    ServiceCategory.ATM: "ATM",
    ServiceCategory.CARD: "Credit Card / Debit Card",
    ServiceCategory.PAYMENTS: "Payments",
    ServiceCategory.CUSTOMER_SERVICE: "Customer Service",
    ServiceCategory.ALL_CHANNELS: "All channels / general outage",
    ServiceCategory.POS: "POS / merchants",
    ServiceCategory.OTHER: "Other",
    ServiceCategory.UNKNOWN: "Unknown",
    ServiceCategory.REPUTATION: "Reputation / deposit risk",
}


class IssueCategory(StrEnum):
    NONE = "none"
    LOGIN_FAILURE = "login_failure"
    TRANSFER_FAILURE = "transfer_failure"
    APPLICATION_UNAVAILABLE = "application_unavailable"
    PAYMENT_FAILURE = "payment_failure"
    TRANSACTION_DELAY = "transaction_delay"
    ATM_FAILURE = "atm_failure"
    CARD_FAILURE = "card_failure"
    CUSTOMER_SERVICE_UNREACHABLE = "customer_service_unreachable"
    FRAUD_UNAUTHORIZED = "fraud_unauthorized"  # fraud, transactions the customer did not make
    OTHER = "other"
    # Reputation signals (grouped under ServiceCategory.REPUTATION)
    SOLVENCY_RUMOR = "solvency_rumor"  # "the bank is going bust" rumours
    DEPOSIT_FLIGHT = "deposit_flight"  # people saying they withdraw all money / close accounts
    RUN_CALL = "run_call"  # calls on others to withdraw their money


REPUTATION_ISSUES = (IssueCategory.SOLVENCY_RUMOR, IssueCategory.DEPOSIT_FLIGHT, IssueCategory.RUN_CALL)


class ComplaintSeverity(StrEnum):
    """Estimated impact of a single complaint (not an alarm level)."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Severity(StrEnum):
    """Alarm level. `rank` expresses the escalation order."""

    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    def is_higher_than(self, other: Severity) -> bool:
        return self.rank > other.rank

    @classmethod
    def highest(cls, levels: list[Severity]) -> Severity:
        return max(levels, key=lambda level: level.rank, default=cls.NORMAL)


_SEVERITY_RANK = {Severity.NORMAL: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"

    @property
    def is_active(self) -> bool:
        return self is not IncidentStatus.RESOLVED


class AuditEventType(StrEnum):
    """Entries of the incident audit trail (incident_events.jsonl)."""

    OPENED = "OPENED"
    ESCALATED = "ESCALATED"
    LEVEL_CHANGED = "LEVEL_CHANGED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    NOTE_ADDED = "NOTE_ADDED"
    FEEDBACK_APPLIED = "FEEDBACK_APPLIED"


class AlertType(StrEnum):
    SERVICE_INCIDENT = "SERVICE_INCIDENT"
    COLLECTOR_HEALTH = "COLLECTOR_HEALTH"
    REPUTATION_SIGNAL = "REPUTATION_SIGNAL"


class AlertStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class CollectorRunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class CollectorHealthStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
