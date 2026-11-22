#!/usr/bin/env python3
"""Render a restrained neon contribution calendar for the GitHub profile."""

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

LEVEL_COLORS = {
    "NONE": ("#F5E6C8", "#D9BE91"),
    "FIRST_QUARTILE": ("#E53935", "#FF8A80"),
    "SECOND_QUARTILE": ("#FF9800", "#FFD180"),
    "THIRD_QUARTILE": ("#4CAF50", "#B9F6CA"),
    "FOURTH_QUARTILE": ("#3F51B5", "#C5CAE9"),
}


def date_window(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    end = today or dt.datetime.now(dt.timezone.utc).date()
    return end - dt.timedelta(days=364), end


def fetch_calendar(start: dt.date, end: dt.date) -> dict[str, Any]:
    token = os.environ.get("PROFILE_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("PROFILE_STATS_TOKEN or GITHUB_TOKEN is required.")

    variables = {
        "login": LOGIN,
        "from": f"{start.isoformat()}T00:00:00Z",
        "to": f"{end.isoformat()}T23:59:59Z",
    }
    body = json.dumps({"query": QUERY, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Kaelith69-neon-contribution-graph/1.0",
        },
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:800]
            last_error = RuntimeError(
                f"GitHub GraphQL returned HTTP {error.code}: {detail}"
            )
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise last_error from error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == 2:
                raise RuntimeError(f"Could not reach GitHub GraphQL: {error}") from error
        time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"GitHub GraphQL request failed: {last_error}")

    if payload.get("errors"):
        messages = "; ".join(
            item.get("message", "Unknown GraphQL error")
            for item in payload["errors"]
        )
        raise RuntimeError(f"GitHub GraphQL error: {messages}")

    user = (payload.get("data") or {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user {LOGIN!r} was not returned by GraphQL.")

    calendar = (user.get("contributionsCollection") or {}).get("contributionCalendar")
    if not calendar:
        raise RuntimeError("GitHub did not return a contribution calendar.")

    return calendar


def collect_days(
    calendar: dict[str, Any],
    start: dt.date,
    end: dt.date,
) -> dict[dt.date, dict[str, Any]]:
    result: dict[dt.date, dict[str, Any]] = {}

    for week in calendar.get("weeks", []):
        for day in week.get("contributionDays", []):
            try:
                date = dt.date.fromisoformat(day["date"])
                count = int(day["contributionCount"])
                level = str(day["contributionLevel"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError(f"Malformed contribution day: {day!r}") from error

            if count < 0:
                raise RuntimeError(f"Negative contribution count for {date}.")
            if level not in LEVEL_COLORS:
                raise RuntimeError(f"Unknown contribution level {level!r} for {date}.")
            if start <= date <= end:
                if date in result:
                    raise RuntimeError(f"Duplicate contribution day returned for {date}.")
                result[date] = {
                    "count": count,
                    "level": level if count else "NONE",
                }

    expected = (end - start).days + 1
    if len(result) != expected:
        missing = [
            (start + dt.timedelta(days=offset)).isoformat()
            for offset in range(expected)
            if start + dt.timedelta(days=offset) not in result
        ]
        raise RuntimeError(
            f"Contribution calendar contains {len(result)} of {expected} days. "
            f"Missing: {', '.join(missing[:5])}. Refusing to publish incomplete data."
        )

    return result


def calendar_columns(start: dt.date, end: dt.date) -> tuple[dt.date, int]:
    first_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    last_sunday = end - dt.timedelta(days=(end.weekday() + 1) % 7)
    columns = ((last_sunday - first_sunday).days // 7) + 1
    return first_sunday, columns


def render_svg(start: dt.date, end: dt.date, total: int, days: dict[dt.date, dict[str, Any]]) -> str:
    """Render a text-free, glossy candy-inspired contribution calendar."""
    width, height = 860, 226
    grid_x, grid_y, pitch, cell = 42, 50, 14, 9
    first_sunday, _ = calendar_columns(start, end)
    pieces = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="contribution-title contribution-desc" data-total-contributions="{total}" data-start="{start.isoformat()}" data-end="{end.isoformat()}">
<title id="contribution-title">{html.escape(LOGIN)} contribution activity</title><desc id="contribution-desc">A text-free, candy-coated 365-day contribution calendar.</desc>
<defs>
<linearGradient id="wrapper-bg" x1="0" y1="0" x2=".9" y2="1"><stop offset="0" stop-color="#FFF8E8"/><stop offset=".48" stop-color="#F7E8C7"/><stop offset="1" stop-color="#E8D3A9"/></linearGradient>
<radialGradient id="soft-glow" cx="50%" cy="40%" r="70%"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".78"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></radialGradient>
<linearGradient id="candy-shine" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".9"/><stop offset=".28" stop-color="#FFFFFF" stop-opacity=".32"/><stop offset=".62" stop-color="#FFFFFF" stop-opacity="0"/><stop offset="1" stop-color="#3B1B14" stop-opacity=".24"/></linearGradient>
<filter id="candy-shadow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur in="SourceAlpha" stdDeviation="1.3" result="blur"/><feOffset in="blur" dx="1.3" dy="1.8" result="offset"/><feComponentTransfer in="offset" result="shadow"><feFuncA type="linear" slope=".34" intercept="0"/></feComponentTransfer><feMerge><feMergeNode in="shadow"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<pattern id="wrapper-speckle" width="18" height="18" patternUnits="userSpaceOnUse"><circle cx="3" cy="4" r=".7" fill="#9B7446" opacity=".11"/><circle cx="13" cy="12" r=".55" fill="#9B7446" opacity=".09"/></pattern>
<clipPath id="grid-clip"><rect x="34" y="38" width="792" height="150" rx="12"/></clipPath></defs>
<rect width="860" height="226" rx="18" fill="url(#wrapper-bg)"/><rect width="860" height="226" rx="18" fill="url(#wrapper-speckle)"/><rect x="8" y="8" width="844" height="210" rx="14" fill="none" stroke="#FFFFFF" stroke-opacity=".65" stroke-width="1.4"/><path d="M18 22H842" stroke="#FFFFFF" stroke-opacity=".48"/><path d="M18 204H842" stroke="#B58A50" stroke-opacity=".16"/><rect x="24" y="28" width="812" height="170" rx="12" fill="#E9D7B6" opacity=".28"/><rect x="24" y="28" width="812" height="170" rx="12" fill="url(#soft-glow)" opacity=".45"/>''']
    for offset in range((end-start).days+1):
        date=start+dt.timedelta(days=offset); item=days[date]
        column=(date-first_sunday).days//7; row=(date.weekday()+1)%7
        x=grid_x+column*pitch; y=grid_y+row*pitch
        fill,stroke=LEVEL_COLORS[item["level"]]
        pieces.append(f'''<g data-date="{date.isoformat()}" data-count="{item["count"]}" data-level="{item["level"]}"><title>{html.escape(f'{date.isoformat()} · {item["count"]} contributions')}</title>
<ellipse cx="{x+cell/2:.1f}" cy="{y+cell/2+1.2:.1f}" rx="5.4" ry="5.2" fill="#7A4C26" opacity=".2"/>
<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="4.2" fill="{fill}" stroke="{stroke}" stroke-width=".55" filter="url(#candy-shadow)"/>
<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="4.2" fill="url(#candy-shine)"/>
<ellipse cx="{x+2.7:.1f}" cy="{y+2.2:.1f}" rx="2.1" ry="1.15" fill="#FFFFFF" opacity=".58"/></g>''')
    pieces.append('''<g clip-path="url(#grid-clip)" opacity=".14" pointer-events="none"><path d="M0 0V160" stroke="#FFFFFF" stroke-width="22"><animateTransform attributeName="transform" type="translate" values="34 38;826 38;34 38" dur="36s" repeatCount="indefinite"/></path></g><rect x=".7" y=".7" width="858.6" height="224.6" rx="17.3" fill="none" stroke="#FFFFFF" stroke-opacity=".55" stroke-width="1.2"/></svg>''')
    return "\n".join(pieces)

def main() -> None:
    start, end = date_window()
    calendar = fetch_calendar(start, end)
    total = int(calendar.get("totalContributions", -1))
    if total < 0:
        raise RuntimeError("GitHub returned an invalid totalContributions value.")

    days = collect_days(calendar, start, end)
    day_total = sum(item["count"] for item in days.values())
    if day_total != total:
        raise RuntimeError(
            f"Daily contribution sum ({day_total}) does not match "
            f"GitHub's calendar total ({total}). Refusing to publish inconsistent data."
        )

    svg = render_svg(start, end, total, days)
    root = ET.fromstring(svg)
    if len(root.findall(".//*[@data-date]")) != 365:
        raise RuntimeError("Generated SVG does not contain exactly 365 contribution days.")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    active_days = sum(item["count"] > 0 for item in days.values())
    print(
        f"Rendered {total:,} contributions across {len(days)} days "
        f"({active_days} active days) to {OUTPUT.relative_to(ROOT)}."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution graph update failed: {error}", file=sys.stderr)
        raise
