#!/usr/bin/env python3
"""Build the profile's paper and ink contribution calendar and activity summary."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GRAPH_OUTPUT = ROOT / "assets" / "contributions-neon.svg"
SUMMARY_OUTPUT = ROOT / "assets" / "streak.svg"
LOGIN = os.environ.get("GITHUB_PROFILE_LOGIN", "Kaelith69")
API_URL = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")
SVG_NS = "http://www.w3.org/2000/svg"

QUERY = """
query ContributionCalendar($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
            contributionLevel
          }
        }
      }
    }
  }
}
"""

PAPER = "#E9E3D6"
INK = "#20282D"
MUTED = "#6F756F"
RUST = "#C7553D"
SAGE = "#B8C36A"
SLATE = "#627F95"
VIOLET = "#766C8B"
LEVEL_COLORS = {
    "NONE": "#D8D1C2",
    "FIRST_QUARTILE": SAGE,
    "SECOND_QUARTILE": SLATE,
    "THIRD_QUARTILE": VIOLET,
    "FOURTH_QUARTILE": INK,
}


def contribution_window(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Return the latest 365 dates, including the current UTC date."""
    end = today or dt.datetime.now(dt.timezone.utc).date()
    return end - dt.timedelta(days=364), end


def fetch_calendar(start: dt.date, end: dt.date) -> dict[str, Any]:
    token = os.environ.get("PROFILE_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("PROFILE_STATS_TOKEN or GITHUB_TOKEN is required.")

    payload = json.dumps({
        "query": QUERY,
        "variables": {
            "login": LOGIN,
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T23:59:59Z",
        },
    }).encode()
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Kaelith69-profile-field-notes/4.0",
        },
        method="POST",
    )

    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")[:800]
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise RuntimeError(f"GitHub GraphQL HTTP {exc.code}: {details}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 3:
                raise RuntimeError(f"GitHub GraphQL request failed: {exc}") from exc
        time.sleep(2 ** attempt)
    else:
        raise RuntimeError("GitHub GraphQL request failed after retries.")

    if result.get("errors"):
        messages = "; ".join(item.get("message", "GraphQL error") for item in result["errors"])
        raise RuntimeError(messages)

    user = (result.get("data") or {}).get("user")
    calendar = ((user or {}).get("contributionsCollection") or {}).get("contributionCalendar")
    if not calendar:
        raise RuntimeError(f"No contribution calendar returned for {LOGIN}.")
    return calendar


def collect_days(
    calendar: dict[str, Any], start: dt.date, end: dt.date
) -> dict[dt.date, tuple[int, str]]:
    days: dict[dt.date, tuple[int, str]] = {}
    for week in calendar.get("weeks", []):
        for item in week.get("contributionDays", []):
            date = dt.date.fromisoformat(item["date"])
            count = int(item["contributionCount"])
            level = str(item["contributionLevel"])
            if count < 0 or level not in LEVEL_COLORS:
                raise RuntimeError(f"Invalid contribution data for {date}.")
            if start <= date <= end:
                if date in days:
                    raise RuntimeError(f"Duplicate contribution date: {date}.")
                days[date] = (count, level if count else "NONE")

    expected = (end - start).days + 1
    if len(days) != expected:
        raise RuntimeError(f"Expected {expected} days, received {len(days)}.")
    return days


def streak_metrics(days: dict[dt.date, tuple[int, str]]) -> tuple[int, int, int]:
    ordered = sorted(days)
    counts = [days[date][0] for date in ordered]
    total = sum(counts)
    longest = current = run = 0
    for count in counts:
        if count:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    recent = list(reversed(counts))
    # A GitHub streak remains active through the current UTC day when
    # yesterday had contributions but today has not yet been updated.
    if recent and recent[0] == 0:
        recent = recent[1:]
    for count in recent:
        if not count:
            break
        current += 1
    return total, longest, current


def render_graph(
    start: dt.date,
    end: dt.date,
    total: int,
    days: dict[dt.date, tuple[int, str]],
) -> str:
    width, height = 860, 190
    left, top, pitch, cell = 36, 65, 14, 9
    first_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    columns = ((end - first_sunday).days // 7) + 1
    if columns > 53:
        raise RuntimeError(f"Unexpected calendar width: {columns} columns.")

    parts = [f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}"
 role="img" aria-label="GitHub contribution calendar for the latest 365 days"
 data-total-contributions="{total}" data-start="{start.isoformat()}" data-end="{end.isoformat()}">
<rect width="{width}" height="{height}" fill="{PAPER}"/>
<rect x="0" y="0" width="8" height="{height}" fill="{RUST}"/>
<text x="36" y="25" fill="{INK}" font-family="'Courier New',monospace" font-size="10" letter-spacing="2">CONTRIBUTION HISTORY</text>
<text x="824" y="25" fill="{MUTED}" text-anchor="end" font-family="'Courier New',monospace" font-size="9" letter-spacing="1.4">365 DAYS / {end.strftime("%d %b %Y").upper()}</text>
<text x="824" y="47" fill="{INK}" text-anchor="end" font-family="Georgia,'Times New Roman',serif" font-size="17">{total:,} contributions</text>
<path d="M36 36H824" stroke="#B8B1A3" stroke-width="1"/>
<path d="M36 36H148" stroke="{RUST}" stroke-width="2.5"/>
<g id="months" fill="{MUTED}" font-family="'Courier New',monospace" font-size="8" letter-spacing=".6">''']

    month_positions: list[tuple[int, str]] = []
    for date in sorted(days):
        if date.day == 1 or date == start:
            column = (date - first_sunday).days // 7
            month_positions.append((left + column * pitch, date.strftime("%b").upper()))
    for x, label in month_positions:
        parts.append(f'<text x="{x}" y="56">{label}</text>')
    parts.append("</g><g id=\"calendar\">")

    for offset in range((end - start).days + 1):
        date = start + dt.timedelta(days=offset)
        count, level = days[date]
        column = (date - first_sunday).days // 7
        row = (date.weekday() + 1) % 7
        x, y = left + column * pitch, top + row * pitch
        color = LEVEL_COLORS[level]
        title = f"{date.isoformat()}: {count} contribution" + ("" if count == 1 else "s")
        stroke = f' stroke="{INK}" stroke-width=".8"' if date == end else ""
        parts.append(
            f'<g data-date="{date.isoformat()}" data-count="{count}" data-level="{level}">'
            f'<title>{title}</title>'
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" '
            f'fill="{color}"{stroke}/></g>'
        )

    parts.append(f'''</g>
<path d="M36 174H824" stroke="#B8B1A3" stroke-width=".8"/>
<text x="690" y="185" fill="{MUTED}" font-family="'Courier New',monospace" font-size="8">LESS</text>''')
    legend_x = 727
    for index, color in enumerate(LEVEL_COLORS.values()):
        parts.append(
            f'<rect x="{legend_x + index * 15}" y="178" width="9" height="7" fill="{color}"/>'
        )
    parts.append(
        f'<text x="824" y="185" text-anchor="end" fill="{MUTED}" '
        f'font-family="Courier New,monospace" font-size="8">MORE</text></svg>'
    )
    return "\n".join(parts) + "\n"


def render_summary(
    end: dt.date, total: int, longest: int, current: int
) -> str:
    width, height = 860, 132
    current_unit = "day" if current == 1 else "days"
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}"
 role="img" aria-label="Contribution summary: {total} contributions in 365 days, longest streak {longest} days, current streak {current} {current_unit}"
 data-total="{total}" data-longest-streak="{longest}" data-current-streak="{current}" data-updated="{end.isoformat()}">
<rect width="{width}" height="{height}" fill="{PAPER}"/>
<path d="M0 0H860V132H0Z" fill="none" stroke="#B8B1A3"/>
<rect x="0" y="0" width="8" height="132" fill="{INK}"/>
<text x="34" y="23" fill="{MUTED}" font-family="'Courier New',monospace" font-size="9" letter-spacing="2">ACTIVITY / ROLLING YEAR</text>
<text x="826" y="23" fill="{MUTED}" text-anchor="end" font-family="'Courier New',monospace" font-size="9" letter-spacing="1">UPDATED {end.strftime("%d %b %Y").upper()}</text>
<path d="M34 32H826" stroke="#B8B1A3"/>
<g>
 <text x="36" y="88" fill="{INK}" font-family="Georgia,'Times New Roman',serif" font-size="43">{total:,}</text>
 <text x="38" y="111" fill="{MUTED}" font-family="'Courier New',monospace" font-size="9" letter-spacing="1.2">CONTRIBUTIONS</text>
 <path d="M282 43V116" stroke="#B8B1A3"/>
 <rect x="282" y="43" width="3" height="73" fill="{RUST}"/>
 <text x="316" y="88" fill="{INK}" font-family="Georgia,'Times New Roman',serif" font-size="43">{longest}</text>
 <text x="318" y="111" fill="{MUTED}" font-family="'Courier New',monospace" font-size="9" letter-spacing="1.2">LONGEST RUN / DAYS</text>
 <path d="M560 43V116" stroke="#B8B1A3"/>
 <rect x="560" y="43" width="3" height="73" fill="{SAGE}"/>
 <text x="594" y="88" fill="{INK}" font-family="Georgia,'Times New Roman',serif" font-size="43">{current}</text>
 <text x="596" y="111" fill="{MUTED}" font-family="'Courier New',monospace" font-size="9" letter-spacing="1.2">CURRENT RUN / DAYS</text>
</g>
<path d="M826 43V116" stroke="{SLATE}" stroke-width="3"/>
</svg>
'''


def validate_graph(svg: str, start: dt.date, end: dt.date, total: int) -> None:
    root = ET.fromstring(svg)
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise RuntimeError("Contribution output is not SVG.")
    cells = root.findall(".//*[@data-date]")
    expected = (end - start).days + 1
    if len(cells) != expected:
        raise RuntimeError(f"Expected {expected} daily cells, found {len(cells)}.")
    dates = [dt.date.fromisoformat(cell.attrib["data-date"]) for cell in cells]
    if dates != sorted(dates) or len(set(dates)) != expected:
        raise RuntimeError("Contribution dates are missing, duplicated, or out of order.")
    daily_total = sum(int(cell.attrib["data-count"]) for cell in cells)
    if daily_total != total:
        raise RuntimeError(f"Daily total {daily_total} does not match calendar total {total}.")
    if root.attrib.get("viewBox") != "0 0 860 190":
        raise RuntimeError("Unexpected contribution graph dimensions.")
    if not root.findall(f".//{{{SVG_NS}}}title"):
        raise RuntimeError("Contribution graph is missing its accessible title.")


def validate_summary(svg: str, total: int, longest: int, current: int) -> None:
    root = ET.fromstring(svg)
    expected = {
        "data-total": str(total),
        "data-longest-streak": str(longest),
        "data-current-streak": str(current),
    }
    for key, value in expected.items():
        if root.attrib.get(key) != value:
            raise RuntimeError(f"Activity summary {key} does not match computed data.")


def main() -> None:
    start, end = contribution_window()
    calendar = fetch_calendar(start, end)
    declared_total = int(calendar.get("totalContributions", -1))
    days = collect_days(calendar, start, end)
    total, longest, current = streak_metrics(days)
    if declared_total != total:
        raise RuntimeError(
            f"GitHub calendar total {declared_total} does not match daily total {total}."
        )

    graph = render_graph(start, end, total, days)
    summary = render_summary(end, total, longest, current)
    validate_graph(graph, start, end, total)
    validate_summary(summary, total, longest, current)

    GRAPH_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_OUTPUT.write_text(graph, encoding="utf-8")
    SUMMARY_OUTPUT.write_text(summary, encoding="utf-8")
    print(
        f"Rendered {total:,} contributions, a {longest}-day longest run, "
        f"and a {current}-day current run across 365 days."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Profile artwork update failed: {exc}", file=sys.stderr)
        raise
