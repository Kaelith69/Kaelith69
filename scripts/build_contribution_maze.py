#!/usr/bin/env python3
"""Render GitHub contributions as a neon activity field visited by a small rocket."""

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
OUTPUT = ROOT / "assets" / "contributions-rocket.svg"
LOGIN = os.environ.get("GITHUB_PROFILE_LOGIN", "Kaelith69")
API_URL = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")

CYCLE_SECONDS = 58
GRID_X = 106
GRID_Y = 87
CELL = 9
PITCH = 12
LAUNCH_POINT = (82.0, 176.0)

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
    "NONE": ("#0B1322", "#17283F", "none"),
    "FIRST_QUARTILE": ("#1748FF", "#6890FF", "blue"),
    "SECOND_QUARTILE": ("#008FFF", "#66D6FF", "cyan"),
    "THIRD_QUARTILE": ("#00D8C2", "#7FFFF0", "teal"),
    "FOURTH_QUARTILE": ("#C52CFF", "#F0A0FF", "violet"),
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
            "User-Agent": "Kaelith69-neon-contribution-rocket/3.0",
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
    """Return Sunday first columns matching GitHub's contribution calendar."""
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


def cell_center(column: int, row: int) -> tuple[float, float]:
    return (
        GRID_X + column * PITCH + CELL / 2,
        GRID_Y + row * PITCH + CELL / 2,
    )


def nearest_neighbor_route(
    weeks: list[list[dt.date]],
    start: dt.date,
    end: dt.date,
    days: dict[dt.date, dict[str, Any]],
    launch_point: tuple[float, float] = LAUNCH_POINT,
) -> list[tuple[int, int, dt.date]]:
    """Visit active contribution cells by repeatedly choosing the nearest unvisited one."""
    remaining: list[tuple[int, int, dt.date]] = []
    for column, week in enumerate(weeks):
        for row, date in enumerate(week):
            if start <= date <= end and days[date]["count"] > 0:
                remaining.append((column, row, date))

    route: list[tuple[int, int, dt.date]] = []
    current = launch_point

    while remaining:
        next_cell = min(
            remaining,
            key=lambda item: (
                (cell_center(item[0], item[1])[0] - current[0]) ** 2
                + (cell_center(item[0], item[1])[1] - current[1]) ** 2,
                item[2].isoformat(),
            ),
        )
        route.append(next_cell)
        current = cell_center(next_cell[0], next_cell[1])
        remaining.remove(next_cell)

    return route


def render_svg(
    start: dt.date,
    end: dt.date,
    total: int,
    days: dict[dt.date, dict[str, Any]],
) -> str:
    weeks = calendar_weeks(start, end)
    route = nearest_neighbor_route(weeks, start, end, days)
    route_index = {
        (column, row): index
        for index, (column, row, _) in enumerate(route)
    }

    path_points = [LAUNCH_POINT]
    path_points.extend(
        cell_center(column, row)
        for column, row, _ in route
    )
    motion_path = "M " + " L ".join(
        f"{x:.2f},{y:.2f}" for x, y in path_points
    )
    if len(path_points) == 1:
        motion_path += f" L {LAUNCH_POINT[0]:.2f},{LAUNCH_POINT[1]:.2f}"

    pieces: list[str] = []
    pieces.append(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="226"
 viewBox="0 0 860 226" role="img" aria-labelledby="title desc">
<title id="title">{html.escape(LOGIN)} contribution rocket</title>
<desc id="desc">{total:,} GitHub contributions from {start.isoformat()} to {end.isoformat()}. A white and electric blue rocket visits each active contribution square in nearest neighbor order across a 365 day neon calendar.</desc>
<defs>
  <linearGradient id="space-bg" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#030711"/><stop offset=".52" stop-color="#071020"/><stop offset="1" stop-color="#08091A"/>
  </linearGradient>
  <radialGradient id="blue-nebula">
    <stop stop-color="#145BFF" stop-opacity=".17"/><stop offset="1" stop-color="#145BFF" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="rocket-shell" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#FFFFFF"/><stop offset=".46" stop-color="#DDEEFF"/><stop offset="1" stop-color="#8DBBFF"/>
  </linearGradient>
  <linearGradient id="rocket-blue" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#A9EFFF"/><stop offset=".45" stop-color="#238CFF"/><stop offset="1" stop-color="#1550F5"/>
  </linearGradient>
  <linearGradient id="engine-flare" x1="0" y1="0" x2="1" y2="0">
    <stop stop-color="#FFFFFF"/><stop offset=".35" stop-color="#73E9FF"/><stop offset="1" stop-color="#167BFF" stop-opacity="0"/>
  </linearGradient>
  <filter id="neon-blue" x="-120%" y="-120%" width="340%" height="340%">
    <feGaussianBlur stdDeviation="2.8" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="cell-glow" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur stdDeviation="1.4" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="screen-clip"><rect x="1" y="1" width="858" height="224" rx="10"/></clipPath>
</defs>
<g clip-path="url(#screen-clip)">
  <rect width="860" height="226" fill="url(#space-bg)"/>
  <ellipse cx="430" cy="125" rx="450" ry="155" fill="url(#blue-nebula)"/>
  <g fill="#BBD7FF" opacity=".3">
    <circle cx="26" cy="36" r=".65"/><circle cx="48" cy="166" r=".7"/>
    <circle cx="270" cy="24" r=".65"/><circle cx="492" cy="33" r=".6"/>
    <circle cx="775" cy="49" r=".7"/><circle cx="837" cy="184" r=".6"/>
    <circle cx="713" cy="24" r=".5"/><circle cx="365" cy="197" r=".55"/>
  </g>
  <path d="M24 55H836" stroke="#28456F" stroke-opacity=".62"/>
  <text x="28" y="37" font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="25" letter-spacing="1.6" fill="#F4F8FF">CONTRIBUTIONS</text>
  <text x="824" y="37" text-anchor="end"
        font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="30" letter-spacing=".8" fill="#6DEBFF"
        filter="url(#neon-blue)">{total:,}</text>
  <text x="824" y="48" text-anchor="end"
        font-family="Trebuchet MS, Arial, sans-serif"
        font-size="8" letter-spacing="1.2" fill="#7794BB">365 DAYS</text>
  <g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
     font-weight="700" letter-spacing=".6" fill="#8299BC">'''
    )

    # Month markers align with the column containing each month's first day.
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
        pieces.append(f'<text x="{x}" y="74">{label}</text>')

    for row, label in enumerate(("S", "M", "T", "W", "T", "F", "S")):
        y = GRID_Y + row * PITCH + 7
        pieces.append(f'<text x="88" y="{y}" text-anchor="middle">{label}</text>')
    pieces.append("</g>")

    # Each square represents exactly one date, with real GitHub intensity levels.
    for column, week in enumerate(weeks):
        for row, date in enumerate(week):
            if not start <= date <= end:
                continue

            detail = days[date]
            count = int(detail["count"])
            level = str(detail["level"])
            fill, stroke, _ = LEVEL_COLORS[level]
            x = GRID_X + column * PITCH
            y = GRID_Y + row * PITCH
            animation = ""

            if count > 0:
                index = route_index[(column, row)]
                progress = min(0.999, max(0.0001, (index + 1) / max(1, len(route))))
                animation = (
                    f'<animate attributeName="opacity" values="1;1;.18" '
                    f'keyTimes="0;{progress:.6f};1" dur="{CYCLE_SECONDS}s" '
                    f'repeatCount="indefinite"/>'
                )

            pieces.append(
                f'''<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2"
 data-date="{date.isoformat()}" data-count="{count}" data-level="{level}"
 fill="{fill}" stroke="{stroke}" stroke-width=".7"
 opacity="1" {f'filter="url(#cell-glow)"' if count else ''}>
 <title>{date.isoformat()} · {count} contribution{"s" if count != 1 else ""}</title>
 {animation}
</rect>'''
            )

    # The flight trace is restrained and exists only to make the route legible.
    pieces.append(
        f'''<path d="{motion_path}" fill="none" stroke="#288CFF"
      stroke-width="1.1" stroke-opacity=".22" stroke-dasharray="2 5"
      stroke-linecap="round"/>'''
    )

    # Horizontal local axis points toward the rocket nose; auto rotation follows the route.
    pieces.append(
        f'''<g filter="url(#neon-blue)">
  <g>
    <animateMotion path="{motion_path}" dur="{CYCLE_SECONDS}s"
                   calcMode="paced" rotate="auto"
                   repeatCount="indefinite"/>
    <g>
      <path d="M-17 -3.5 Q-27 -9 -34 -4 Q-31 0 -34 4 Q-27 9 -17 3.5Z"
            fill="url(#engine-flare)" opacity=".9">
        <animate attributeName="d"
          values="M-17 -3.5 Q-27 -9 -34 -4 Q-31 0 -34 4 Q-27 9 -17 3.5Z;M-17 -3 Q-31 -12 -41 -3 Q-34 0 -41 3 Q-31 12 -17 3Z;M-17 -3.5 Q-27 -9 -34 -4 Q-31 0 -34 4 Q-27 9 -17 3.5Z"
          dur=".32s" repeatCount="indefinite"/>
      </path>
      <path d="M-17 -2 Q-25 -4 -29 0 Q-25 4 -17 2Z"
            fill="#FFFFFF" opacity=".9">
        <animate attributeName="opacity" values=".75;1;.75" dur=".2s" repeatCount="indefinite"/>
      </path>
      <path d="M-16 -4 L-23 -11 L-9 -8 L-4 -4Z"
            fill="#1768FF" stroke="#A4EFFF" stroke-width=".8" stroke-linejoin="round"/>
      <path d="M-16 4 L-23 11 L-9 8 L-4 4Z"
            fill="#1768FF" stroke="#A4EFFF" stroke-width=".8" stroke-linejoin="round"/>
      <path d="M-18 -3.5 H-22 V3.5 H-18Z"
            fill="#0D3DB9" stroke="#68DFFF" stroke-width=".7"/>
      <path d="M-17 0 Q-13 -10 -2 -10 Q10 -10 18 0 Q10 10 -2 10 Q-13 10 -17 0Z"
            fill="url(#rocket-shell)" stroke="#A8E9FF" stroke-width="1.15"/>
      <path d="M-6 -7.5 Q6 -10 15 -2" fill="none"
            stroke="#FFFFFF" stroke-opacity=".9" stroke-width="1.1" stroke-linecap="round"/>
      <path d="M7 -8 Q14 -5 18 0 Q14 5 7 8 L3 0Z"
            fill="url(#rocket-blue)" stroke="#C5F5FF" stroke-width=".65"/>
      <path d="M-7 0 H6" stroke="#1768FF" stroke-opacity=".68" stroke-width="1.2"/>
      <circle cx="-1" cy="0" r="4.05" fill="#0B2E69" stroke="#FFFFFF" stroke-width="1.1"/>
      <circle cx="-1" cy="0" r="2.55" fill="#3DD9FF" stroke="#A9F5FF" stroke-width=".5"/>
      <circle cx="-1.9" cy="-1" r=".85" fill="#FFFFFF"/>
      <path d="M-10 -5 Q-5 -7 -1 -6" fill="none"
            stroke="#FFFFFF" stroke-opacity=".75" stroke-width=".7"/>
    </g>
  </g>
  <animate attributeName="opacity" values="1;1;0;0;1"
           keyTimes="0;.94;.975;.999;1" dur="{CYCLE_SECONDS}s"
           repeatCount="indefinite"/>
</g>'''
    )

    date_label = (
        f"{start.strftime('%b %d, %Y').upper()}  /  "
        f"{end.strftime('%b %d, %Y').upper()}"
    )
    pieces.append(
        f'''<path d="M24 184H836" stroke="#28456F" stroke-opacity=".56"/>
<text x="28" y="203" font-family="Trebuchet MS, Arial, sans-serif"
      font-size="9" font-weight="600" letter-spacing="1.05"
      fill="#8299BC">{date_label}</text>
<g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
   font-weight="700" letter-spacing=".7" fill="#8299BC">
  <text x="674" y="203">LESS</text>'''
    )

    for index, level in enumerate(
        ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    ):
        fill, stroke, _ = LEVEL_COLORS[level]
        x = 708 + index * 15
        pieces.append(
            f'<rect x="{x}" y="195" width="9" height="9" rx="2" '
            f'fill="{fill}" stroke="{stroke}" stroke-width=".7"/>'
        )
    pieces.append(
        '''<text x="790" y="203">MORE</text>
</g>
</g>
<rect x=".7" y=".7" width="858.6" height="224.6" rx="10"
      fill="none" stroke="#27456F" stroke-opacity=".82"/>
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
        f"from {start.isoformat()} to {end.isoformat()}; "
        f"rocket route includes {sum(item['count'] > 0 for item in days.values())} active days."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution rocket update failed: {error}", file=sys.stderr)
        raise
