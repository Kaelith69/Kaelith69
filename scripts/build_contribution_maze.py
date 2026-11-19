#!/usr/bin/env python3
"""Render a contribution calendar as a restrained, animated arcade cabinet panel."""

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
OUTPUT = ROOT / "assets" / "contributions-pacman.svg"
LOGIN = os.environ.get("GITHUB_PROFILE_LOGIN", "Kaelith69")
API_URL = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")

CYCLE_SECONDS = 55
GRID_X = 112
GRID_Y = 88
CELL = 9
GAP = 3
PITCH = CELL + GAP

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
    "NONE": ("#172137", "#2A3851"),
    "FIRST_QUARTILE": ("#17434D", "#2D7180"),
    "SECOND_QUARTILE": ("#216B73", "#3B9995"),
    "THIRD_QUARTILE": ("#4AA78C", "#77C8A5"),
    "FOURTH_QUARTILE": ("#E9B84D", "#FFF0D1"),
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
            "User-Agent": "Kaelith69-contribution-arcade/2.0",
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
        sample = ", ".join(missing[:5])
        raise RuntimeError(
            f"Contribution calendar contains {len(result)} of {expected} days. "
            f"Missing: {sample}. Refusing to publish incomplete data."
        )

    return result


def calendar_weeks(start: dt.date, end: dt.date) -> list[list[dt.date]]:
    """Build Sunday-first columns with edge dates retained only as grid positions."""
    first_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    last_sunday = end - dt.timedelta(days=(end.weekday() + 1) % 7)
    count = ((last_sunday - first_sunday).days // 7) + 1
    return [
        [
            first_sunday + dt.timedelta(days=column * 7 + row)
            for row in range(7)
        ]
        for column in range(count)
    ]


def arcade_route(
    weeks: list[list[dt.date]],
    start: dt.date,
    end: dt.date,
) -> list[tuple[int, int, dt.date]]:
    """Snake down alternating week columns, visiting every real date once."""
    route: list[tuple[int, int, dt.date]] = []
    for column, week in enumerate(weeks):
        rows = range(7) if column % 2 == 0 else range(6, -1, -1)
        for row in rows:
            date = week[row]
            if start <= date <= end:
                route.append((column, row, date))
    expected = (end - start).days + 1
    if len(route) != expected or len({item[2] for item in route}) != expected:
        raise RuntimeError("Arcade path does not cover every contribution date exactly once.")
    return route


def render_svg(
    start: dt.date,
    end: dt.date,
    total: int,
    days: dict[dt.date, dict[str, Any]],
) -> str:
    weeks = calendar_weeks(start, end)
    route = arcade_route(weeks, start, end)
    route_indices = {(column, row): index for index, (column, row, _) in enumerate(route)}
    path_points = [
        (
            GRID_X + column * PITCH + CELL / 2,
            GRID_Y + row * PITCH + CELL / 2,
        )
        for column, row, _ in route
    ]
    motion_path = "M " + " L ".join(
        f"{x:.1f},{y:.1f}" for x, y in path_points
    )

    pieces: list[str] = []
    pieces.append(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="226"
 viewBox="0 0 860 226" role="img" aria-labelledby="title desc">
<title id="title">{html.escape(LOGIN)} GitHub contributions</title>
<desc id="desc">{total:,} contributions from {start.isoformat()} to {end.isoformat()}, shown as a 365-day calendar. Pac-Man moves down alternating week columns and consumes active contribution cells.</desc>
<defs>
  <linearGradient id="cabinet" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#111A2D"/><stop offset="1" stop-color="#080D19"/>
  </linearGradient>
  <linearGradient id="header-metal" x1="0" y1="0" x2="1" y2="0">
    <stop stop-color="#F4C653" stop-opacity=".9"/><stop offset=".55" stop-color="#D7C3A3" stop-opacity=".6"/><stop offset="1" stop-color="#55C9C0" stop-opacity=".55"/>
  </linearGradient>
  <linearGradient id="pacman-body" x1=".12" y1="0" x2=".86" y2="1">
    <stop stop-color="#FFF0B8"/><stop offset=".34" stop-color="#F4C653"/><stop offset="1" stop-color="#D58C2E"/>
  </linearGradient>
  <radialGradient id="screen-glass" cx="50%" cy="35%" r="75%">
    <stop stop-color="#26314A" stop-opacity=".22"/><stop offset="1" stop-color="#080D19" stop-opacity=".08"/>
  </radialGradient>
  <filter id="character-shadow" x="-80%" y="-80%" width="260%" height="260%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="1.8" result="shadow"/>
    <feOffset dy="1.2" result="offset"/>
    <feComponentTransfer><feFuncA type="linear" slope=".48"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="screen-clip"><rect x="1" y="1" width="858" height="224" rx="9"/></clipPath>
</defs>
<g clip-path="url(#screen-clip)">
  <rect width="860" height="226" fill="url(#cabinet)"/>
  <rect x="8" y="8" width="844" height="210" rx="6" fill="url(#screen-glass)" stroke="#526487" stroke-opacity=".38"/>
  <path d="M24 57H836" stroke="url(#header-metal)" stroke-opacity=".6"/>
  <path d="M24 58H836" stroke="#080D19" stroke-opacity=".9"/>
  <circle cx="29" cy="33" r="3" fill="#F4C653"/>
  <circle cx="29" cy="33" r="6" fill="none" stroke="#F4C653" stroke-opacity=".24"/>
  <text x="46" y="40" font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="27" letter-spacing="1.5" fill="#FFF0D1">CONTRIBUTIONS</text>
  <text x="821" y="37" text-anchor="end"
        font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="31" letter-spacing=".7" fill="#F4C653">{total:,}</text>
  <text x="822" y="49" text-anchor="end"
        font-family="Trebuchet MS, Arial, sans-serif"
        font-size="8" letter-spacing="1.1" fill="#8DA6BC">LAST 365 DAYS</text>
  <g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
     font-weight="700" letter-spacing=".6" fill="#A5B7CE">'''
    )

    # Month labels sit above the week in which each month begins.
    month_positions: list[tuple[int, str]] = []
    seen_months: set[tuple[int, int]] = set()
    for column, week in enumerate(weeks):
        for date in week:
            if start <= date <= end and date.day == 1:
                key = (date.year, date.month)
                if key not in seen_months:
                    month_positions.append((column, date.strftime("%b").upper()))
                    seen_months.add(key)
    if not month_positions or month_positions[0][0] != 0:
        month_positions.insert(0, (0, start.strftime("%b").upper()))
    for column, label in month_positions:
        x = GRID_X + column * PITCH
        pieces.append(f'<text x="{x}" y="76">{label}</text>')

    day_labels = ["S", "M", "T", "W", "T", "F", "S"]
    for row, label in enumerate(day_labels):
        y = GRID_Y + row * PITCH + 7
        pieces.append(f'<text x="94" y="{y}" text-anchor="middle">{label}</text>')
    pieces.append("</g>")

    # Only the 365 requested dates become data cells. Padded edge dates are omitted.
    for column, week in enumerate(weeks):
        for row, date in enumerate(week):
            if not start <= date <= end:
                continue

            detail = days[date]
            count = int(detail["count"])
            level = str(detail["level"])
            fill, stroke = LEVEL_COLORS[level]
            x = GRID_X + column * PITCH
            y = GRID_Y + row * PITCH
            cell_markup = (
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" '
                f'data-date="{date.isoformat()}" data-count="{count}" '
                f'data-level="{level}" fill="{fill}" stroke="{stroke}" '
                f'stroke-width=".7" fill-opacity="1">'
                f'<title>{date.isoformat()} · {count} contribution'
                f'{"s" if count != 1 else ""}</title>'
            )

            if count > 0:
                index = route_indices[(column, row)]
                begin = index / len(route) * CYCLE_SECONDS
                cell_markup += (
                    f'<animate attributeName="fill-opacity" values="1;.12;.12" '
                    f'keyTimes="0;.006;1" dur="{CYCLE_SECONDS}s" '
                    f'begin="{begin:.3f}s" repeatCount="indefinite"/>'
                )

            cell_markup += "</rect>"
            pieces.append(cell_markup)

    # One purpose-drawn Pac-Man character, scaled to sit just above the cell pitch.
    pieces.append(
        f'''<g filter="url(#character-shadow)">
  <g>
    <animateMotion path="{motion_path}" dur="{CYCLE_SECONDS}s"
                   calcMode="paced" rotate="auto" repeatCount="indefinite"/>
    <g transform="scale(.72)">
      <path d="M0 0 L10 -6.7 A12 12 0 1 1 10 6.7 Z"
            fill="url(#pacman-body)" stroke="#FFF0D1" stroke-width=".9"
            stroke-linejoin="round">
        <animate attributeName="d"
          values="M0 0 L10 -6.7 A12 12 0 1 1 10 6.7 Z;M0 0 L11.9 -.55 A12 12 0 1 1 11.9 .55 Z;M0 0 L10 -6.7 A12 12 0 1 1 10 6.7 Z"
          dur=".38s" repeatCount="indefinite"/>
      </path>
      <path d="M-6 -8.5 Q-1 -13 5 -10.6" fill="none"
            stroke="#FFF9DF" stroke-opacity=".72" stroke-width="1.35"
            stroke-linecap="round"/>
      <ellipse cx="3.1" cy="-6.6" rx="1.55" ry="2.05" fill="#142039"/>
      <circle cx="3.55" cy="-7.25" r=".55" fill="#FFF0D1"/>
    </g>
  </g>
  <animate attributeName="opacity" values="1;1;0" keyTimes="0;.985;1"
           dur="{CYCLE_SECONDS}s" repeatCount="indefinite"/>
</g>'''
    )

    # Minimal controls and an activity key. No decorative captions or ghost characters.
    range_label = (
        f"{start.strftime('%b %d, %Y').upper()}  /  "
        f"{end.strftime('%b %d, %Y').upper()}"
    )
    pieces.append(
        f'''<path d="M24 184H836" stroke="#526487" stroke-opacity=".48"/>
<text x="29" y="203" font-family="Trebuchet MS, Arial, sans-serif"
      font-size="9" font-weight="600" letter-spacing="1.05"
      fill="#8DA6BC">{range_label}</text>
<g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
   font-weight="700" letter-spacing=".7" fill="#8DA6BC">
  <text x="676" y="203">LESS</text>'''
    )

    legend_x = 710
    for index, level in enumerate(
        ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    ):
        fill, stroke = LEVEL_COLORS[level]
        x = legend_x + index * 15
        pieces.append(
            f'<rect x="{x}" y="195" width="9" height="9" rx="2" '
            f'fill="{fill}" stroke="{stroke}" stroke-width=".7"/>'
        )
    pieces.append(
        '''<text x="790" y="203">MORE</text>
</g>
</g>
<rect x=".7" y=".7" width="858.6" height="224.6" rx="9.3"
      fill="none" stroke="#526487" stroke-opacity=".85"/>
<rect x="3.5" y="3.5" width="853" height="219" rx="7"
      fill="none" stroke="#FFF0D1" stroke-opacity=".045"/>
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
    ET.fromstring(svg)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    print(
        f"Rendered {total:,} contributions across {len(days)} days "
        f"from {start.isoformat()} to {end.isoformat()}."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution arcade update failed: {error}", file=sys.stderr)
        raise
