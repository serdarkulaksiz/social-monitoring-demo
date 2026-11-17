"""Small server-side SVG charts. No JavaScript library and no CDN: the dashboard must work
on a closed bank network. Colours come from CSS classes, so the strict Content-Security-Policy
(no inline styles) holds; every text is escaped."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from html import escape

from markupsafe import Markup


def _top(value: float) -> int:
    """A round axis maximum at or above ``value``."""
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if value <= step:
            return step
    return int(value) + 1


def time_chart(
    points: Sequence[tuple[datetime, int]],
    *,
    expected: Sequence[float] | None = None,
    markers: Sequence[tuple[datetime, str]] = (),
    label: str,
    width: int = 720,
    height: int = 210,
) -> Markup:
    """People per time bucket as a line, the baseline expectation as a dashed line, events as markers."""
    if not points:
        return Markup('<p class="empty">Bu aralıkta veri yok.</p>')
    left, right, top, bottom = 34, 8, 10, 26
    plot_width, plot_height = width - left - right, height - top - bottom
    values = [value for _, value in points]
    maximum = _top(max([*values, *(expected or [0]), 1]))
    step = plot_width / max(len(points) - 1, 1)

    def x(index: float) -> float:
        return left + index * step

    def y(value: float) -> float:
        return top + plot_height - (value / maximum) * plot_height

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">',
        f"<title>{escape(label)}</title>",
        f'<line class="axis" x1="{left}" y1="{y(0):.1f}" x2="{width - right}" y2="{y(0):.1f}"/>',
        f'<line class="grid" x1="{left}" y1="{y(maximum):.1f}" x2="{width - right}" y2="{y(maximum):.1f}"/>',
        f'<text class="tick" x="{left - 6}" y="{y(0) + 4:.1f}" text-anchor="end">0</text>',
        f'<text class="tick" x="{left - 6}" y="{y(maximum) + 4:.1f}" text-anchor="end">{maximum}</text>',
    ]
    label_every = max(1, len(points) // 6)
    for index, (moment, _) in enumerate(points):
        if index % label_every == 0:
            parts.append(
                f'<text class="tick" x="{x(index):.1f}" y="{height - 8}" text-anchor="middle">'
                f"{moment.astimezone(UTC):%H:%M}</text>"
            )
    if expected:
        line = " ".join(f"{x(index):.1f},{y(value):.1f}" for index, value in enumerate(expected))
        parts.append(f'<polyline class="expected" points="{line}"><title>Bu saatler için normal beklenen</title></polyline>')
    line = " ".join(f"{x(index):.1f},{y(value):.1f}" for index, value in enumerate(values))
    parts.append(f'<polyline class="series" points="{line}"/>')
    for index, (moment, value) in enumerate(points):
        if value:
            parts.append(
                f'<circle class="dot" cx="{x(index):.1f}" cy="{y(value):.1f}" r="2.5">'
                f"<title>{moment.astimezone(UTC):%H:%M} UTC: {value} kişi</title></circle>"
            )
    start, end = points[0][0], points[-1][0]
    span = (end - start).total_seconds() or 1
    for moment, text in markers:
        if start <= moment <= end:
            position = x((moment - start).total_seconds() / span * (len(points) - 1))
            parts.append(
                f'<line class="marker" x1="{position:.1f}" y1="{top}" x2="{position:.1f}" y2="{y(0):.1f}">'
                f"<title>{escape(text)}</title></line>"
            )
    parts.append("</svg>")
    return Markup("".join(parts))


def sparkline(values: Sequence[int], *, label: str, width: int = 120, height: int = 28) -> Markup:
    if not values:
        return Markup("")
    maximum = max(max(values), 1)
    step = width / max(len(values) - 1, 1)
    line = " ".join(f"{index * step:.1f},{height - 2 - value / maximum * (height - 4):.1f}" for index, value in enumerate(values))
    return Markup(
        f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">'
        f'<title>{escape(label)}</title><polyline class="series" points="{line}"/></svg>'
    )


def bar(value: int, maximum: int) -> Markup:
    """A horizontal bar for tables; the width is an SVG attribute, not an inline style."""
    share = 0 if maximum <= 0 else max(2, round(100 * value / maximum))
    return Markup(
        f'<svg class="bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">'
        f'<rect class="track" width="100" height="8" rx="3"/><rect class="fill" width="{share}" height="8" rx="3"/></svg>'
    )
