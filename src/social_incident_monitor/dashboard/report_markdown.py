"""Turkish Markdown rendering of an incident report (download from the dashboard or the API)."""

from __future__ import annotations

from datetime import UTC, datetime

from ..application.incident_views import IncidentReport
from . import labels_tr as tr

DISCLAIMER_TR = (
    "Bu rapor kamuya açık sosyal medya sinyallerini özetler. Bir olayın regülatöre bildirilip "
    "bildirilmeyeceği kararına girdi sağlar; bu kararın yerine geçmez."
)


def render_incident_markdown(report: IncidentReport) -> str:
    incident = report.incident
    service = tr.label(tr.SERVICE, incident.service_category)
    issue = tr.label(tr.ISSUE, incident.issue_category)
    lines = [
        f"# Olay raporu: {service} — {issue}",
        "",
        f"- **Olay kimliği:** `{incident.incident_id}`",
        f"- **Durum:** {tr.label(tr.INCIDENT_STATUS, incident.status)}",
        f"- **En yüksek seviye:** {tr.label(tr.SEVERITY, incident.severity)} (güncel: {tr.label(tr.SEVERITY, incident.current_level)})",
        f"- **Açılış:** {_time(incident.opened_at)}",
        f"- **İlk şikâyet:** {_time(incident.first_complaint_at)}",
        f"- **Zirve:** {incident.peak_unique_users_count} farklı kişi ({_time(incident.peak_at)})",
        f"- **Onay:** {_time(incident.acknowledged_at)} {_by(incident.acknowledged_by)}",
        f"- **Çözüm:** {_time(incident.resolved_at)} {_by(incident.resolved_by)}",
    ]
    if incident.resolution_reason:
        lines.append(f"- **Çözüm nedeni:** {_cell(incident.resolution_reason)}")
    if incident.resolution_note:
        lines.append(f"- **Çözüm notu:** {_cell(incident.resolution_note)}")
    lines += [
        f"- **Rapor zamanı:** {_time(report.generated_at)}",
        "",
        "## Tespit gerekçesi",
        "",
        _inline(tr.detection_text(incident.detection_details) or incident.detection_reason),
        "",
        "## Süreler",
        "",
        "| Ölçüm | Süre |",
        "|---|---|",
        f"| İlk şikâyetten tespite | {tr.duration(report.timings.time_to_detect)} |",
        f"| Tespitten onaya | {tr.duration(report.timings.time_to_acknowledge)} |",
        f"| Tespitten çözüme | {tr.duration(report.timings.time_to_resolve)} |",
        "",
        "## Zaman çizelgesi",
        "",
    ]
    if report.timeline:
        lines += ["| Zaman | Olay | Kim | Ayrıntı | Kayıt özeti (hash) |", "|---|---|---|---|---|"]
        for event in report.timeline:
            detail = event.details.get("note") or event.details.get("reason") or ""
            lines.append(
                f"| {_time(event.occurred_at)} | {tr.label(tr.AUDIT_EVENT, event.event_type)} | {_cell(tr.actor(event.actor))} "
                f"| {_cell(str(detail))} | `{event.record_hash[:12]}` |"
            )
    else:
        lines.append("Bu olay için denetim kaydı yok (kayıt özelliği eklenmeden önce açılmış olabilir).")
    lines += [
        "",
        f"Denetim kaydı bütünlüğü: **{'doğrulandı' if report.audit_trail_verified else 'DOĞRULANAMADI — verify-audit komutunu çalıştırın'}**",
        "",
        "## Kanıt gönderileri",
        "",
        f"{_time(report.evidence.period_start)} – {_time(report.evidence.period_end)} arasında "
        f"{report.evidence.unique_users} farklı kişiden {report.evidence.total_posts} gönderi.",
        "",
    ]
    if report.evidence.posts:
        lines += ["| Zaman | Kullanıcı | Gönderi | Etkileşim | Link |", "|---|---|---|---|---|"]
        for item in report.evidence.posts:
            post = item.post
            user = post.author_handle or post.author_id
            if post.author_display_name:
                user = f"{post.author_display_name} (@{user})" if post.author_handle else f"{post.author_display_name} ({user})"
            engagement = (
                f"{item.engagement.like_count} beğeni, {item.engagement.repost_count} paylaşım"
                if item.engagement
                else "-"
            )
            link = f"[aç]({_url(post.url)})" if post.url and post.url.startswith("https://") else "-"
            lines.append(f"| {_time(post.created_at)} | {_cell(user)} | {_cell(post.text)} | {engagement} | {link} |")
        if report.evidence.total_posts > len(report.evidence.posts):
            lines.append("")
            lines.append(f"İlk {len(report.evidence.posts)} gönderi gösterildi.")
    lines += ["", "---", "", f"_{DISCLAIMER_TR}_", ""]
    return "\n".join(lines)


def _time(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "-"


def _by(name: str | None) -> str:
    return f"({tr.actor(name)})" if name else ""


def _url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _inline(text: str) -> str:
    return " ".join(text.split())


def _cell(text: str) -> str:
    """Single-line table cell; pipes and Markdown control characters cannot break the table."""
    cleaned = _inline(text).replace("\\", "\\\\").replace("|", "\\|")
    for char in "*_`[]<>":
        cleaned = cleaned.replace(char, "\\" + char)
    return cleaned[:500]
