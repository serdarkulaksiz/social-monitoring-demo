"""Turkish display texts. The API keeps its English codes; only the presentation is translated."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from ..domain.enums import (
    AlertStatus,
    AlertType,
    AuditEventType,
    CollectorHealthStatus,
    ComplaintSeverity,
    IncidentStatus,
    IssueCategory,
    ServiceCategory,
    Severity,
)

SERVICE = {
    ServiceCategory.MOBILE_BANKING: "Mobil Bankacılık",
    ServiceCategory.INTERNET_BANKING: "İnternet Bankacılığı",
    ServiceCategory.TRANSFER: "FAST / EFT / Havale",
    ServiceCategory.ATM: "ATM",
    ServiceCategory.CARD: "Kredi / Banka Kartı",
    ServiceCategory.PAYMENTS: "Ödemeler",
    ServiceCategory.CUSTOMER_SERVICE: "Müşteri Hizmetleri",
    ServiceCategory.ALL_CHANNELS: "Tüm kanallar / genel kesinti",
    ServiceCategory.POS: "POS / üye işyeri",
    ServiceCategory.OTHER: "Diğer",
    ServiceCategory.UNKNOWN: "Belirsiz",
    ServiceCategory.REPUTATION: "İtibar / Mevduat Riski",
}

ISSUE = {
    IssueCategory.NONE: "Yok",
    IssueCategory.LOGIN_FAILURE: "Giriş yapılamıyor",
    IssueCategory.TRANSFER_FAILURE: "Para transferi başarısız",
    IssueCategory.APPLICATION_UNAVAILABLE: "Uygulama / hizmet erişilemiyor",
    IssueCategory.PAYMENT_FAILURE: "Ödeme başarısız",
    IssueCategory.TRANSACTION_DELAY: "İşlem gecikmesi",
    IssueCategory.ATM_FAILURE: "ATM arızası",
    IssueCategory.CARD_FAILURE: "Kart sorunu",
    IssueCategory.CUSTOMER_SERVICE_UNREACHABLE: "Müşteri hizmetlerine ulaşılamıyor",
    IssueCategory.FRAUD_UNAUTHORIZED: "Dolandırıcılık / yetkisiz işlem",
    IssueCategory.OTHER: "Diğer",
    IssueCategory.SOLVENCY_RUMOR: "Batma / iflas söylentisi",
    IssueCategory.DEPOSIT_FLIGHT: "Para çekme / hesap kapatma",
    IssueCategory.RUN_CALL: "Para çekme çağrısı",
}

SEVERITY = {Severity.NORMAL: "NORMAL", Severity.WARNING: "UYARI", Severity.CRITICAL: "KRİTİK"}
COMPLAINT_SEVERITY = {
    ComplaintSeverity.NONE: "yok",
    ComplaintSeverity.LOW: "düşük",
    ComplaintSeverity.MEDIUM: "orta",
    ComplaintSeverity.HIGH: "yüksek",
}
INCIDENT_STATUS = {IncidentStatus.OPEN: "AÇIK", IncidentStatus.ACKNOWLEDGED: "ONAYLANDI", IncidentStatus.RESOLVED: "ÇÖZÜLDÜ"}
ALERT_STATUS = {AlertStatus.ACTIVE: "AKTİF", AlertStatus.ACKNOWLEDGED: "ONAYLANDI", AlertStatus.RESOLVED: "ÇÖZÜLDÜ"}
ALERT_TYPE = {
    AlertType.SERVICE_INCIDENT: "Hizmet olayı",
    AlertType.COLLECTOR_HEALTH: "Veri toplama sağlığı",
    AlertType.REPUTATION_SIGNAL: "İtibar sinyali",
}
COLLECTOR_HEALTH = {
    CollectorHealthStatus.UNKNOWN: "BİLİNMİYOR",
    CollectorHealthStatus.HEALTHY: "SAĞLIKLI",
    CollectorHealthStatus.DEGRADED: "SORUNLU",
    CollectorHealthStatus.STALE: "VERİ GELMİYOR",
}
AUDIT_EVENT = {
    AuditEventType.OPENED: "Olay açıldı",
    AuditEventType.ESCALATED: "Seviye yükseldi",
    AuditEventType.LEVEL_CHANGED: "Güncel seviye değişti",
    AuditEventType.ACKNOWLEDGED: "Onaylandı",
    AuditEventType.RESOLVED: "Çözüldü",
    AuditEventType.NOTE_ADDED: "Not eklendi",
    AuditEventType.FEEDBACK_APPLIED: "Etiket düzeltmesi",
}
VERDICT = {
    "confirm": "Doğru",
    "complaint": "Şikâyet (kategori düzeltildi)",
    "not_complaint": "Şikâyet değil",
    "not_bank_related": "Bankayla ilgisiz",
}
SAMPLE = {
    "review_random": "Rastgele örnek",
    "review_uncertain": "Belirsizler",
    "review_incidents": "Olaydakiler",
    "review_disagreement": "Model ile kural ayrışanlar",
    "incident_evidence": "Olay detayı",
    "reputation": "İtibar sayfası",
    "other": "Diğer / API",
}
COLLECTOR_CONDITION = {"collector_stale": "Collector'dan veri gelmiyor", "collector_failing": "Collector art arda hata veriyor"}
SYSTEM_ACTOR = "Sistem"


def label(mapping: Mapping[Any, str], value: object) -> str:
    """Translated text for an enum member or its string value; unknown values are shown as they are."""
    for key, text in mapping.items():
        if key == value or getattr(key, "value", None) == value:
            return text
    return str(value)


def actor(name: str | None) -> str:
    if not name or name == "system":
        return SYSTEM_ACTOR
    return name


def duration(value: timedelta | None) -> str:
    if value is None:
        return "-"
    seconds = int(value.total_seconds())
    if seconds < 0:
        return "-"
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours} sa {minutes} dk"
    if minutes:
        return f"{minutes} dk {seconds} sn"
    return f"{seconds} sn"


def detection_text(details: Mapping[str, Any]) -> str:
    """Turkish explanation of why an incident was raised, built from structured rule details."""
    rule = details.get("rule")
    window = details.get("window_minutes")
    users = details.get("unique_users")
    if rule == "absolute_threshold":
        repeats = details.get("repeat_complaints", 0)
        text = (
            f"Son {window} dakikada {users} farklı kişi şikâyet etti "
            f"(uyarı eşiği {details.get('warning_threshold')}, kritik eşik {details.get('critical_threshold')} kişi)."
        )
        if repeats:
            text += f" Aynı kişilerden gelen {repeats} tekrar şikâyet sayıma katılmadı."
        return text
    if rule == "baseline":
        text = (
            f"Son {window} dakikada {users} farklı kişi şikâyet etti; bu saat için normal beklenen "
            f"{_number(details.get('expected_users'))} kişi. Bu kadar şikâyetin tesadüfen görülme olasılığı "
            f"{probability(details.get('p_value'))} ({details.get('history_days')} günlük geçmişe göre)."
        )
        if details.get("velocity"):
            text = "Hızlı artış: " + text
        return text
    if rule == "reputation":
        return (
            f"Son {window} dakikada {users} farklı kişi bu konuda paylaşım yaptı; "
            f"toplam yayılım (yeniden paylaşım + alıntı) {details.get('amplification', 0)}."
        )
    return ""


def _number(value: object) -> str:
    if isinstance(value, int | float):
        return f"{value:.1f}".replace(".", ",")
    return "-"


def probability(value: object) -> str:
    if not isinstance(value, int | float):
        return "-"
    if value <= 0:
        return "≈ 0"
    if value < 0.0001:
        inverse = f"{round(1 / value):,}".replace(",", ".")
        return f"yaklaşık 1 / {inverse}"
    return f"%{value * 100:.2f}".replace(".", ",")
