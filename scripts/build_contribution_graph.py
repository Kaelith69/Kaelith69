#!/usr/bin/env python3
"""Render a compact, textless brutalist contribution calendar from GitHub GraphQL."""

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
OUTPUT = ROOT / "assets" / "contributions-neon.svg"
LOGIN = os.environ.get("GITHUB_PROFILE_LOGIN", "Kaelith69")
API_URL = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")

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

# One shared palette: navy surfaces, violet to cyan to acid-lime intensity.
LEVEL_COLORS = {
    "NONE": "#17213E",
    "FIRST_QUARTILE": "#6B3FA0",
    "SECOND_QUARTILE": "#A855F7",
    "THIRD_QUARTILE": "#35D9E6",
    "FOURTH_QUARTILE": "#C6F432",
}


def contribution_window(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Return the latest 365 complete UTC dates, including today."""
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
            "User-Agent": "Kaelith69-profile-contribution-graph/2.0",
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


def render_svg(
    start: dt.date,
    end: dt.date,
    total: int,
    days: dict[dt.date, tuple[int, str]],
) -> str:
    width, height = 860, 154
    left, top, pitch, cell = 17, 24, 12, 8
    first_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    # 365 days span 53 calendar columns when aligned to Sunday.
    columns = ((end - first_sunday).days // 7) + 1
    if columns > 69:
        raise RuntimeError(f"Unexpected calendar width: {columns} columns.")

    parts = [f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
 viewBox="0 0 {width} {height}" role="img"
 aria-label="GitHub contribution activity"
 data-total-contributions="{total}" data-start="{start.isoformat()}" data-end="{end.isoformat()}">
<defs>
 <linearGradient id="surface" x1="0" y1="0" x2="1" y2="1">
  <stop stop-color="#101A34"/><stop offset="1" stop-color="#080D1D"/>
 </linearGradient>
 <linearGradient id="edge" x1="0" y1="0" x2="1" y2="0">
  <stop stop-color="#A855F7"/><stop offset=".52" stop-color="#35D9E6"/><stop offset="1" stop-color="#C6F432"/>
 </linearGradient>
 <filter id="cell-glow" x="-80%" y="-80%" width="260%" height="260%">
  <feGaussianBlur stdDeviation="1.5" result="blur"/>
  <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
 </filter>
 <pattern id="grain" width="18" height="18" patternUnits="userSpaceOnUse">
  <circle cx="2" cy="3" r=".55" fill="#D8DCE8" opacity=".1"/>
  <circle cx="12" cy="13" r=".45" fill="#35D9E6" opacity=".07"/>
 </pattern>
</defs>
<rect width="860" height="154" rx="2" fill="url(#surface)"/>
<rect width="860" height="154" rx="2" fill="url(#grain)"/>
<path d="M0 1H860M0 153H860" stroke="url(#edge)" stroke-width="2"/>
<path d="M17 17H843" stroke="#D8DCE8" stroke-opacity=".13"/>
<path d="M17 137H843" stroke="#D8DCE8" stroke-opacity=".13"/>
<path d="M17 9H83" stroke="#A855F7" stroke-width="3"/>
<path d="M777 9H843" stroke="#C6F432" stroke-width="3"/>
<g id="calendar">''']

    for offset in range((end - start).days + 1):
        date = start + dt.timedelta(days=offset)
        count, level = days[date]
        column = (date - first_sunday).days // 7
        row = (date.weekday() + 1) % 7
        x, y = left + column * pitch, top + row * pitch
        color = LEVEL_COLORS[level]
        filter_attr = 'filter="url(#cell-glow)"' if count and level == "FOURTH_QUARTILE" else ""
        # SVG title keeps per-day detail accessible without adding visible labels.
        parts.append(
            f'<g data-date="{date.isoformat()}" data-count="{count}" '
            f'data-level="{level}"><title>{date.isoformat()}: {count} contributions</title>'
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="0.7" '
            f'{filter_attr}/>'
            f'{"filter=\"url(#cell-glow)\"" if count and level == "FOURTH_QUARTILE" else ""}/>'
            f'</g>'
        )

    parts.append("</g></svg>")
    return "\n".join(parts) + "\n"


def validate_svg(svg: str, start: dt.date, end: dt.date, total: int) -> None:
    root = ET.fromstring(svg)
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise RuntimeError("Output root is not SVG.")
    cells = root.findall(".//*[@data-date]")
    expected = (end - start).days + 1
    if len(cells) != expected:
        raise RuntimeError(f"Expected {expected} cells, found {len(cells)}.")
    dates = [dt.date.fromisoformat(cell.attrib["data-date"]) for cell in cells]
    if dates != sorted(dates) or len(set(dates)) != expected:
        raise RuntimeError("Dates are missing, duplicated, or out of order.")
    daily_total = sum(int(cell.attrib["data-count"]) for cell in cells)
    if daily_total != total:
        raise RuntimeError(f"Daily total {daily_total} does not match calendar total {total}.")
    if root.attrib.get("viewBox") != "0 0 860 154":
        raise RuntimeError("Unexpected SVG viewBox.")
    if root.findall(".//{http://www.w3.org/2000/svg}text"):
        raise RuntimeError("The contribution graph must remain textless.")


def main() -> None:
    start, end = contribution_window()
    calendar = fetch_calendar(start, end)
    total = int(calendar.get("totalContributions", -1))
    days = collect_days(calendar, start, end)
    if total < 0 or sum(count for count, _ in days.values()) != total:
        raise RuntimeError("Contribution total does not match daily data. Refusing to publish.")
    svg = render_svg(start, end, total, days)
    validate_svg(svg, start, end, total)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    print(f"Rendered {total:,} contributions across 365 days to {OUTPUT.relative_to(ROOT)}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Contribution graph update failed: {exc}", file=sys.stderr)
        raise
