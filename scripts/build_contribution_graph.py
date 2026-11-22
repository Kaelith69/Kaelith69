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
    "NONE": ("#101D35", "#263D60"),
    "FIRST_QUARTILE": ("#174BAA", "#79A8FF"),
    "SECOND_QUARTILE": ("#1678D1", "#8CC8FF"),
    "THIRD_QUARTILE": ("#18AFCB", "#A2F0FF"),
    "FOURTH_QUARTILE": ("#8B61F6", "#E0D2FF"),
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


def render_svg(
    start: dt.date,
    end: dt.date,
    total: int,
    days: dict[dt.date, dict[str, Any]],
) -> str:
    """Render a compact, data-accurate 365-day calendar with a subtle light sweep."""
    width, height = 860, 226
    grid_x, grid_y = 66, 89
    pitch, cell = 14, 9
    first_sunday, columns = calendar_columns(start, end)

    pieces = [
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
 viewBox="0 0 {width} {height}" role="img"
 aria-labelledby="contribution-title contribution-desc"
 data-total-contributions="{total}" data-start="{start.isoformat()}" data-end="{end.isoformat()}">
<title id="contribution-title">{html.escape(LOGIN)} contribution activity</title>
<desc id="contribution-desc">{total:,} GitHub contributions from {start.isoformat()} to {end.isoformat()}, shown as a 365-day contribution calendar in neon blue, cyan and violet.</desc>
<defs>
  <linearGradient id="panel-bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#080D1D"/>
    <stop offset=".58" stop-color="#0C1730"/>
    <stop offset="1" stop-color="#111B37"/>
  </linearGradient>
  <radialGradient id="blue-haze" cx="78%" cy="15%" r="72%">
    <stop offset="0" stop-color="#255DCE" stop-opacity=".20"/>
    <stop offset=".55" stop-color="#255DCE" stop-opacity=".06"/>
    <stop offset="1" stop-color="#255DCE" stop-opacity="0"/>
  </radialGradient>
  <radialGradient id="violet-haze" cx="94%" cy="88%" r="62%">
    <stop offset="0" stop-color="#8057E8" stop-opacity=".17"/>
    <stop offset="1" stop-color="#8057E8" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="scan-glow" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#7DDCFF" stop-opacity="0"/>
    <stop offset=".5" stop-color="#BCEBFF" stop-opacity=".9"/>
    <stop offset="1" stop-color="#7D8CFF" stop-opacity="0"/>
  </linearGradient>
  <filter id="cell-glow" x="-80%" y="-80%" width="260%" height="260%">
    <feGaussianBlur stdDeviation="1.8" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="comic-shadow" x="-70%" y="-70%" width="240%" height="240%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="1.8" result="blur"/>
    <feOffset in="blur" dx="1.8" dy="2.4" result="offset"/>
    <feComponentTransfer in="offset" result="shadow"><feFuncA type="linear" slope=".65" intercept="0"/></feComponentTransfer>
    <feMerge><feMergeNode in="shadow"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <linearGradient id="cell-light" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#FFFFFF" stop-opacity=".62"/>
    <stop offset=".22" stop-color="#FFFFFF" stop-opacity=".15"/>
    <stop offset=".65" stop-color="#FFFFFF" stop-opacity="0"/>
    <stop offset="1" stop-color="#020611" stop-opacity=".32"/>
  </linearGradient>
  <pattern id="comic-dots" width="8" height="8" patternUnits="userSpaceOnUse">
    <circle cx="1.2" cy="1.2" r=".65" fill="#93B9FF" opacity=".16"/>
  </pattern>
  <linearGradient id="comic-beam" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#FFFFFF" stop-opacity=".18"/>
    <stop offset=".35" stop-color="#6FCBFF" stop-opacity=".06"/>
    <stop offset="1" stop-color="#7B52F6" stop-opacity="0"/>
  </linearGradient>
  <clipPath id="grid-clip">
    <rect x="62" y="85" width="744" height="103" rx="3"/>
  </clipPath>
</defs>

<rect width="860" height="226" rx="16" fill="url(#panel-bg)"/>
<rect width="860" height="226" rx="16" fill="url(#blue-haze)"/>
<rect width="860" height="226" rx="16" fill="url(#violet-haze)"/>
<rect width="860" height="226" rx="16" fill="url(#comic-dots)"/>
<path d="M0 0L480 0L190 226H0Z" fill="url(#comic-beam)" opacity=".42"/>
<path d="M18 1H842" stroke="#5C91E8" stroke-opacity=".3"/>
<path d="M18 1H220" stroke="#7DDCFF" stroke-width="1.1" stroke-opacity=".72"/>

<g font-family="Inter, Segoe UI, Arial, sans-serif">
  <text x="34" y="37" fill="#EAF3FF" font-size="12" font-weight="700"
        letter-spacing="1.8">CONTRIBUTION ACTIVITY</text>
  <text x="34" y="51" fill="#8198BC" font-size="8" letter-spacing="1.2"
        font-weight="600">LAST 365 DAYS</text>
  <text x="824" y="36" text-anchor="end" fill="#F6FAFF"
        font-size="25" font-weight="700">{total:,}</text>
  <text x="824" y="50" text-anchor="end" fill="#8CA9D4"
        font-size="7.5" font-weight="700" letter-spacing="1.15">TOTAL CONTRIBUTIONS</text>
</g>

<path d="M34 61H826" stroke="#36517C" stroke-opacity=".48"/>
<path d="M34 61H250" stroke="#5D9EFF" stroke-opacity=".48"/>

<g font-family="Inter, Segoe UI, Arial, sans-serif" fill="#8299BC"
   font-size="8" font-weight="600" letter-spacing=".7">'''
    ]

    last_month: tuple[int, int] | None = None
    for offset in range((end - start).days + 1):
        date = start + dt.timedelta(days=offset)
        month_key = (date.year, date.month)
        if month_key != last_month:
            column = (date - first_sunday).days // 7
            x = grid_x + column * pitch
            pieces.append(
                f'<text x="{x}" y="78">{date.strftime("%b").upper()}</text>'
            )
            last_month = month_key

    pieces.append(
        '''</g>
<g font-family="Inter, Segoe UI, Arial, sans-serif" fill="#7187A9"
   font-size="7.5" font-weight="600">'''
    )
    for row, label in ((1, "M"), (3, "W"), (5, "F")):
        y = grid_y + row * pitch + 6.5
        pieces.append(f'<text x="39" y="{y}" text-anchor="middle">{label}</text>')
    pieces.append("</g>")

    for offset in range((end - start).days + 1):
        date = start + dt.timedelta(days=offset)
        item = days[date]
        column = (date - first_sunday).days // 7
        row = (date.weekday() + 1) % 7
        x = grid_x + column * pitch
        y = grid_y + row * pitch
        level = item["level"]
        fill, stroke = LEVEL_COLORS[level]
        glow = ' filter="url(#cell-glow)"' if level == "FOURTH_QUARTILE" else ""
        title = html.escape(
            f'{date.strftime("%B %d, %Y")} · {item["count"]} contributions'
        )
        pieces.append(
            x_plus_1 = x + 1
        y_plus_1 = y + 1
        x_plus_5_5 = x + 5.5
        f'''<g data-date="{date.isoformat()}" data-count="{item["count"]}"
 data-level="{level}"{glow}>
  <title>{title}</title>
  <rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.4"
        fill="#020611" opacity=".6" transform="translate(1.4 1.8)"/>
  <rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.4"
        fill="{fill}" stroke="#020611" stroke-width="1.1" filter="url(#comic-shadow)"/>
  <rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.4"
        fill="url(#cell-light)" stroke="{stroke}" stroke-width=".65"/>
  <path d="M{x_plus_1} {y_plus_1}H{x_plus_5_5}" stroke="#FFFFFF" stroke-opacity=".62" stroke-width=".65" stroke-linecap="round"/>
</g>'''
        )

    pieces.append(
        f'''<g clip-path="url(#grid-clip)" opacity=".23" pointer-events="none">
  <path d="M0 0V100" transform="translate(66 87)"
        stroke="url(#scan-glow)" stroke-width="18">
    <animateTransform attributeName="transform" type="translate"
      values="66 87;806 87;66 87" dur="32s" repeatCount="indefinite"/>
  </path>
</g>

<path d="M34 195H826" stroke="#36517C" stroke-opacity=".38"/>
<g font-family="Inter, Segoe UI, Arial, sans-serif" font-size="7.5"
   font-weight="600" letter-spacing=".55">
  <text x="34" y="210" fill="#8198BC">{start.strftime("%b %d, %Y").upper()}  ·  {end.strftime("%b %d, %Y").upper()}</text>
  <text x="612" y="210" fill="#8198BC">LESS</text>'''
    )

    legend_levels = ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    for index, level in enumerate(legend_levels):
        fill, stroke = LEVEL_COLORS[level]
        x = 645 + index * 15
        pieces.append(
            f'<rect x="{x}" y="201" width="9" height="9" rx="2.6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width=".65"/>'
        )

    pieces.append(
        '''  <text x="728" y="210" fill="#8198BC">MORE</text>
</g>
<circle cx="826" cy="25" r="2.2" fill="#8BDFFF">
  <animate attributeName="opacity" values=".35;1;.35" dur="4.8s"
           repeatCount="indefinite"/>
</circle>
<rect x=".7" y=".7" width="858.6" height="224.6" rx="15.5"
      fill="none" stroke="#3B5882" stroke-opacity=".72" stroke-width="1.1"/>
<path d="M14 17V43M846 17V43M14 183V209M846 183V209"
      stroke="#6C8FC5" stroke-opacity=".48" stroke-width="1"/>
</svg>'''
    )

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
