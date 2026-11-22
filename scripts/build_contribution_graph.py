#!/usr/bin/env python3
"""Build a text-free, M&M-inspired GitHub contribution calendar."""

from __future__ import annotations

import datetime as dt
import html
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

CANDY = {
    "NONE": "#E8D5B8",
    "FIRST_QUARTILE": "#F04438",
    "SECOND_QUARTILE": "#FFB21A",
    "THIRD_QUARTILE": "#20B66A",
    "FOURTH_QUARTILE": "#3987F5",
}


def date_window(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    end = today or dt.datetime.now(dt.timezone.utc).date()
    return end - dt.timedelta(days=364), end


def fetch_calendar(start: dt.date, end: dt.date) -> dict[str, Any]:
    token = os.environ.get("PROFILE_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("PROFILE_STATS_TOKEN or GITHUB_TOKEN is required.")

    body = json.dumps({
        "query": QUERY,
        "variables": {
            "login": LOGIN,
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T23:59:59Z",
        },
    }).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Kaelith69-candy-contribution-calendar/1.0",
        },
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode())
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:800]
            last_error = RuntimeError(f"GitHub GraphQL HTTP {error.code}: {detail}")
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise last_error from error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == 2:
                raise RuntimeError(f"GitHub GraphQL request failed: {error}") from error
        time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"GitHub GraphQL request failed: {last_error}")

    if payload.get("errors"):
        raise RuntimeError("; ".join(error.get("message", "GraphQL error") for error in payload["errors"]))

    user = (payload.get("data") or {}).get("user")
    calendar = ((user or {}).get("contributionsCollection") or {}).get("contributionCalendar")
    if not calendar:
        raise RuntimeError(f"No contribution calendar returned for {LOGIN}.")
    return calendar


def collect_days(calendar: dict[str, Any], start: dt.date, end: dt.date) -> dict[dt.date, dict[str, Any]]:
    result: dict[dt.date, dict[str, Any]] = {}
    for week in calendar.get("weeks", []):
        for day in week.get("contributionDays", []):
            date = dt.date.fromisoformat(day["date"])
            count = int(day["contributionCount"])
            level = str(day["contributionLevel"])
            if count < 0 or level not in CANDY:
                raise RuntimeError(f"Invalid contribution data for {date}.")
            if start <= date <= end:
                if date in result:
                    raise RuntimeError(f"Duplicate date returned: {date}.")
                result[date] = {"count": count, "level": level if count else "NONE"}

    expected = (end - start).days + 1
    if len(result) != expected:
        raise RuntimeError(f"Expected {expected} dates, received {len(result)}.")
    return result


def render_svg(start: dt.date, end: dt.date, total: int, days: dict[dt.date, dict[str, Any]]) -> str:
    width, height = 860, 172
    grid_x, grid_y, pitch, size = 34, 43, 15, 11
    first_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7
    )
    parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Candy colored contribution calendar" data-total-contributions="{total}" data-start="{start.isoformat()}" data-end="{end.isoformat()}">
<defs>
  <linearGradient id="chocolate" x1="0" y1="0" x2="0.9" y2="1"><stop offset="0" stop-color="#482719"/><stop offset=".48" stop-color="#32190F"/><stop offset="1" stop-color="#241108"/></linearGradient>
  <radialGradient id="wrapper-glow"><stop offset="0" stop-color="#8A4A24" stop-opacity=".5"/><stop offset="1" stop-color="#8A4A24" stop-opacity="0"/></radialGradient>
  <linearGradient id="candy-gloss" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".7"/><stop offset=".32" stop-color="#FFFFFF" stop-opacity=".2"/><stop offset=".7" stop-color="#FFFFFF" stop-opacity="0"/><stop offset="1" stop-color="#220B08" stop-opacity=".22"/></linearGradient>
  <filter id="drop" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur in="SourceAlpha" stdDeviation="1.6" result="blur"/><feOffset dx="1.1" dy="2" result="offset"/><feComponentTransfer in="offset" result="shadow"><feFuncA type="linear" slope=".42"/></feComponentTransfer><feMerge><feMergeNode in="shadow"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <pattern id="grain" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="3" cy="5" r=".7" fill="#E8C7A2" opacity=".1"/><circle cx="14" cy="14" r=".55" fill="#E8C7A2" opacity=".08"/></pattern>
</defs>
<rect width="860" height="172" rx="17" fill="url(#chocolate)"/>
<rect width="860" height="172" rx="17" fill="url(#wrapper-glow)"/>
<rect width="860" height="172" rx="17" fill="url(#grain)"/>
<rect x="7" y="7" width="846" height="158" rx="12" fill="none" stroke="#C58B55" stroke-opacity=".28"/>
<path d="M17 20H843M17 152H843" stroke="#F1C38A" stroke-opacity=".13"/>
<g opacity=".2"><path d="M0 0L40 0 0 40ZM860 172L820 172 860 132Z" fill="#F4C16B"/></g>''']

    for offset in range((end - start).days + 1):
        date = start + dt.timedelta(days=offset)
        item = days[date]
        week = (date - first_sunday).days // 7
        row = (date.weekday() + 1) % 7
        x, y = grid_x + week * pitch, grid_y + row * pitch
        color = CANDY[item["level"]]
        parts.append(f'''<g data-date="{date.isoformat()}" data-count="{item["count"]}" data-level="{item["level"]}">
<title>{html.escape(date.isoformat())}: {item["count"]} contributions</title>
<ellipse cx="{x + size/2:.1f}" cy="{y + size/2 + 1.3:.1f}" rx="6.1" ry="5.8" fill="#100704" opacity=".48"/>
<circle cx="{x + size/2:.1f}" cy="{y + size/2:.1f}" r="5.65" fill="{color}" stroke="#FFFFFF" stroke-opacity=".3" stroke-width=".45" filter="url(#drop)"/>
<ellipse cx="{x + 3.3:.1f}" cy="{y + 2.5:.1f}" rx="2.55" ry="1.45" fill="#FFFFFF" opacity=".55"/>
<circle cx="{x + size/2:.1f}" cy="{y + size/2:.1f}" r="5.55" fill="url(#candy-gloss)"/>
</g>''')
    parts.append('''<rect x=".7" y=".7" width="858.6" height="170.6" rx="16.3" fill="none" stroke="#FFFFFF" stroke-opacity=".12"/><path d="M-60 0L-30 0 160 172 130 172Z" fill="#FFFFFF" opacity=".025"><animateTransform attributeName="transform" type="translate" values="0 0;760 0;0 0" dur="40s" repeatCount="indefinite"/></path></svg>''')
    return "\n".join(parts)


def main() -> None:
    start, end = date_window()
    calendar = fetch_calendar(start, end)
    total = int(calendar.get("totalContributions", -1))
    days = collect_days(calendar, start, end)
    if total < 0 or sum(day["count"] for day in days.values()) != total:
        raise RuntimeError("Contribution total does not match the daily data. Refusing to publish.")
    svg = render_svg(start, end, total, days)
    root = ET.fromstring(svg)
    if len(root.findall(".//*[@data-date]")) != 365:
        raise RuntimeError("Generated SVG must contain exactly 365 contribution cells.")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    print(f"Rendered {total:,} contributions to {OUTPUT.relative_to(ROOT)}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution graph update failed: {error}", file=sys.stderr)
        raise
