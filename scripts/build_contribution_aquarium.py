#!/usr/bin/env python3
"""Build a fish tank contribution calendar with a nearest-food swimming animation."""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "contributions-aquarium.svg"
LOGIN = os.environ.get("GITHUB_PROFILE_LOGIN", "Kaelith69")
API_URL = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")

GRID_X = 70
GRID_Y = 94
CELL = 9
PITCH = 14
LAUNCH_POINT = (42.0, 205.0)
FISH_NOSE_OFFSET = 10.0
SWIM_SPEED = 105.0
EAT_DWELL = 0.64
RESET_FADE = 0.42

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
    "NONE": ("#0A1422", "#172B43", "none"),
    "FIRST_QUARTILE": ("#174BFF", "#7B9CFF", "electric blue"),
    "SECOND_QUARTILE": ("#00A9FF", "#80E5FF", "cyan"),
    "THIRD_QUARTILE": ("#00D9C2", "#8CFFF1", "aqua"),
    "FOURTH_QUARTILE": ("#CE42FF", "#F0ACFF", "violet"),
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
            "User-Agent": "Kaelith69-contribution-aquarium/4.0",
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


def nearest_food_route(
    weeks: list[list[dt.date]],
    start: dt.date,
    end: dt.date,
    days: dict[dt.date, dict[str, Any]],
    launch_point: tuple[float, float] = LAUNCH_POINT,
) -> list[tuple[int, int, dt.date]]:
    """Choose the nearest remaining active cell after every meal."""
    remaining = [
        (column, row, date)
        for column, week in enumerate(weeks)
        for row, date in enumerate(week)
        if start <= date <= end and days[date]["count"] > 0
    ]

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


def fish_approach_points(
    route: list[tuple[int, int, dt.date]],
    launch_point: tuple[float, float] = LAUNCH_POINT,
) -> list[tuple[float, float]]:
    """Place the fish's mouth over each target instead of its body centre."""
    points = [launch_point]
    current = launch_point

    for column, row, _ in route:
        target = cell_center(column, row)
        dx = target[0] - current[0]
        dy = target[1] - current[1]
        length = math.hypot(dx, dy)
        if length == 0:
            approach = target
        else:
            approach = (
                target[0] - dx / length * FISH_NOSE_OFFSET,
                target[1] - dy / length * FISH_NOSE_OFFSET,
            )
        points.append(approach)
        current = approach

    points.append(launch_point)
    return points


def flight_timeline(
    route: list[tuple[int, int, dt.date]],
    launch_point: tuple[float, float] = LAUNCH_POINT,
) -> dict[str, Any]:
    """Build a repeatable motion path with a timed pause at every contribution."""
    points = fish_approach_points(route, launch_point)
    segment_lengths = [
        math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
        for i in range(len(points) - 1)
    ]
    total_path_length = sum(segment_lengths)

    if total_path_length <= 0:
        points = [launch_point, (launch_point[0] + 1, launch_point[1])]
        segment_lengths = [1.0]
        total_path_length = 1.0

    path = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in points)
    key_points = ["0"]
    key_times = ["0"]
    arrivals: dict[dt.date, tuple[float, float]] = {}

    elapsed = 0.0
    distance_so_far = 0.0

    for index, (column, row, date) in enumerate(route):
        distance = segment_lengths[index]
        distance_so_far += distance
        elapsed += distance / SWIM_SPEED
        arrival = elapsed
        fraction = distance_so_far / total_path_length
        key_points.append(f"{fraction:.8f}")
        key_times.append(f"{arrival:.8f}")

        elapsed += EAT_DWELL
        eaten = elapsed
        key_points.append(f"{fraction:.8f}")
        key_times.append(f"{eaten:.8f}")
        arrivals[date] = (arrival, eaten)

    # Return to the launch point after the final meal. The aquarium resets together.
    return_distance = segment_lengths[-1] if route else 1.0
    distance_so_far += return_distance
    elapsed += return_distance / SWIM_SPEED
    returned_at = elapsed

    key_points.append("1")
    key_times.append(f"{returned_at:.8f}")

    cycle = returned_at + RESET_FADE
    key_points.append("1")
    key_times.append(f"{cycle:.8f}")

    normalized_times = [float(value) / cycle for value in key_times]
    if normalized_times[0] != 0 or abs(normalized_times[-1] - 1) > 1e-8:
        raise RuntimeError("Fish animation timeline does not cover the full cycle.")
    if any(b <= a for a, b in zip(normalized_times, normalized_times[1:])):
        raise RuntimeError("Fish animation key times must increase strictly.")

    reset_time = returned_at / cycle
    fish_fade_start = returned_at / cycle
    fish_fade_end = (returned_at + RESET_FADE * .45) / cycle
    fish_hidden_until = (cycle - .001) / cycle
    return {
        "points": points,
        "path": path,
        "key_points": ";".join(key_points),
        "key_times": ";".join(f"{value:.8f}" for value in normalized_times),
        "arrivals": arrivals,
        "returned_at": returned_at,
        "cycle": cycle,
        "reset_time": reset_time,
        "fish_fade_start": fish_fade_start,
        "fish_fade_end": fish_fade_end,
        "fish_hidden_until": fish_hidden_until,
    }


def render_svg(
    start: dt.date,
    end: dt.date,
    total: int,
    days: dict[dt.date, dict[str, Any]],
) -> str:
    weeks = calendar_weeks(start, end)
    route = nearest_food_route(weeks, start, end, days)
    timeline = flight_timeline(route)
    cycle = timeline["cycle"]
    reset_time = timeline["reset_time"]

    pieces: list[str] = []
    pieces.append(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="244"
 viewBox="0 0 860 244" role="img" aria-labelledby="tank-title tank-desc">
<title id="tank-title">{html.escape(LOGIN)} contribution aquarium</title>
<desc id="tank-desc">{total:,} GitHub contributions from {start.isoformat()} to {end.isoformat()}. A white and electric blue fish swims to the nearest active contribution square, eats it, and the complete tank resets after the final square.</desc>
<defs>
  <linearGradient id="water" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#07182A"/><stop offset=".48" stop-color="#041321"/><stop offset="1" stop-color="#020913"/>
  </linearGradient>
  <radialGradient id="water-light" cx="50%" cy="36%" r="70%">
    <stop stop-color="#0B6B9B" stop-opacity=".22"/><stop offset=".56" stop-color="#06446F" stop-opacity=".08"/><stop offset="1" stop-color="#020913" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="glass-edge" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#5FEAFF" stop-opacity=".74"/><stop offset=".48" stop-color="#245F8B" stop-opacity=".34"/><stop offset="1" stop-color="#B5F7FF" stop-opacity=".56"/>
  </linearGradient>
  <linearGradient id="fish-body" x1=".12" y1="0" x2=".82" y2="1">
    <stop stop-color="#FFFFFF"/><stop offset=".36" stop-color="#E8F7FF"/><stop offset=".74" stop-color="#8FCFFF"/><stop offset="1" stop-color="#2E82E8"/>
  </linearGradient>
  <linearGradient id="fish-fin" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#E9FFFF"/><stop offset=".42" stop-color="#45BFFF"/><stop offset="1" stop-color="#1458DB"/>
  </linearGradient>
  <linearGradient id="fish-belly" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#FFFFFF" stop-opacity=".08"/><stop offset="1" stop-color="#FFFFFF" stop-opacity=".82"/>
  </linearGradient>
  <linearGradient id="bubble" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#FFFFFF" stop-opacity=".8"/><stop offset=".45" stop-color="#75DFFF" stop-opacity=".34"/><stop offset="1" stop-color="#2588FF" stop-opacity=".08"/>
  </linearGradient>
  <filter id="fish-glow" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur stdDeviation="2.4" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="tile-glow" x="-90%" y="-90%" width="280%" height="280%">
    <feGaussianBlur stdDeviation="1.6" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="tank-clip"><rect x="1" y="1" width="858" height="242" rx="13"/></clipPath>
</defs>
<g clip-path="url(#tank-clip)">
  <rect width="860" height="244" fill="url(#water)"/>
  <rect width="860" height="244" fill="url(#water-light)"/>

  <g fill="none" stroke="#45C8F2" stroke-linecap="round">
    <path d="M-25 66 Q105 46 225 66 T475 65 T735 62 T900 69" stroke-opacity=".08" stroke-width="1.2"/>
    <path d="M-22 73 Q108 53 228 73 T478 72 T738 69 T902 76" stroke-opacity=".045" stroke-width=".8"/>
    <path d="M35 214 Q100 202 162 214" stroke-opacity=".11"/>
    <path d="M700 216 Q762 202 829 215" stroke-opacity=".1"/>
  </g>

  <g fill="none" stroke="url(#bubble)" stroke-width="1">
    <circle cx="28" cy="105" r="3"><animate attributeName="cy" values="105;84;105" dur="8s" repeatCount="indefinite"/></circle>
    <circle cx="39" cy="151" r="1.8"><animate attributeName="cy" values="151;126;151" dur="9s" begin="1.4s" repeatCount="indefinite"/></circle>
    <circle cx="827" cy="113" r="2.6"><animate attributeName="cy" values="113;88;113" dur="7s" begin=".7s" repeatCount="indefinite"/></circle>
    <circle cx="839" cy="165" r="1.7"><animate attributeName="cy" values="165;143;165" dur="8.5s" begin="2s" repeatCount="indefinite"/></circle>
    <circle cx="22" cy="193" r="1.2"><animate attributeName="cy" values="193;176;193" dur="7.5s" begin="2.8s" repeatCount="indefinite"/></circle>
  </g>

  <path d="M20 56 Q180 52 340 56 T660 55 T840 56" fill="none" stroke="#55DFFF" stroke-opacity=".33"/>
  <path d="M20 58 Q180 54 340 58 T660 57 T840 58" fill="none" stroke="#FFFFFF" stroke-opacity=".08"/>
  <text x="27" y="37" font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="25" letter-spacing="1.5" fill="#EAFBFF">CONTRIBUTIONS</text>
  <text x="824" y="37" text-anchor="end"
        font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="30" letter-spacing=".8" fill="#69E9FF"
        filter="url(#fish-glow)">{total:,}</text>
  <text x="824" y="48" text-anchor="end"
        font-family="Trebuchet MS, Arial, sans-serif"
        font-size="8" letter-spacing="1.2" fill="#77AFCB">365 DAYS</text>

  <g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
     font-weight="700" letter-spacing=".6" fill="#77AFCB">'''
    )

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
        pieces.append(f'<text x="{x}" y="80">{label}</text>')

    for row, label in enumerate(("S", "M", "T", "W", "T", "F", "S")):
        y = GRID_Y + row * PITCH + 7
        pieces.append(f'<text x="49" y="{y}" text-anchor="middle">{label}</text>')
    pieces.append("</g>")

    # Each tile has a wide five pixel channel around it.
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
                arrival, eaten = timeline["arrivals"][date]
                fade_start = (arrival + EAT_DWELL * .34) / cycle
                fade_end = (arrival + EAT_DWELL * .72) / cycle
                animation = (
                    f'<animate attributeName="opacity" values="1;1;0;0;1" '
                    f'keyTimes="0;{fade_start:.8f};{fade_end:.8f};'
                    f'{reset_time:.8f};1" dur="{cycle:.6f}s" '
                    f'repeatCount="indefinite"/>'
                )

            pieces.append(
                f'''<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2.6"
 data-date="{date.isoformat()}" data-count="{count}" data-level="{level}"
 fill="{fill}" stroke="{stroke}" stroke-width=".8"
 opacity="1" {f'filter="url(#tile-glow)"' if count else ''}>
 <title>{date.isoformat()} · {count} contribution{"s" if count != 1 else ""}</title>
 {animation}
</rect>'''
            )

    # A faint wake connects the food stops, not the zero activity cells.
    if route:
        wake = "M " + " L ".join(
            f"{x:.2f},{y:.2f}" for x, y in timeline["points"]
        )
        pieces.append(
            f'<path d="{wake}" fill="none" stroke="#48CFFF" '
            f'stroke-width=".8" stroke-opacity=".12" stroke-dasharray="1.5 5"/>'
        )

    pieces.append(
        f'''<g id="fish" filter="url(#fish-glow)">
  <animateMotion path="{timeline["path"]}"
    keyPoints="{timeline["key_points"]}" keyTimes="{timeline["key_times"]}"
    calcMode="linear" dur="{cycle:.6f}s" rotate="auto"
    repeatCount="indefinite"/>
  <animate attributeName="opacity" values="1;1;0;0;1"
    keyTimes="0;{(timeline["returned_at"] / cycle):.8f};'
    f'{(timeline["returned_at"] + RESET_FADE * .45) / cycle:.8f};'
    f'{(cycle - .001) / cycle:.8f};1" dur="{cycle:.6f}s" repeatCount="indefinite"/>

  <g transform="scale(.55)">
  <!-- Forked tail: the caudal fin beats while the body glides. -->
  <g transform="translate(-15 0)">
    <g>
      <animateTransform attributeName="transform" type="rotate"
        values="-13 0 0;13 0 0;-13 0 0" dur=".34s" repeatCount="indefinite"/>
      <path d="M0 0 C-7 -7 -17 -13 -20 -9 C-18 -4 -16 -1 -21 0
        C-16 1 -18 4 -20 9 C-17 13 -7 7 0 0Z"
        fill="url(#fish-fin)" stroke="#D9F8FF" stroke-width=".8"/>
      <path d="M-2 0L-16 -7 M-2 0L-16 7" stroke="#FFFFFF"
        stroke-opacity=".55" stroke-width=".7" fill="none"/>
    </g>
  </g>

  <!-- Dorsal and ventral fins move with the water. -->
  <path d="M-6 -9 Q-3 -18 5 -16 Q10 -14 12 -9Z"
    fill="url(#fish-fin)" stroke="#D9F8FF" stroke-width=".8"/>
  <path d="M-5 10 Q1 18 8 14 L10 9Z"
    fill="url(#fish-fin)" stroke="#D9F8FF" stroke-width=".7"/>

  <!-- Main fish silhouette. -->
  <path d="M-16 0 C-13 -10 -1 -14 10 -10
    C17 -8 21 -3 22 0 C21 3 17 8 10 10
    C-1 14 -13 10 -16 0Z"
    fill="url(#fish-body)" stroke="#E8FBFF" stroke-width="1.05"/>

  <!-- Subtle belly, flank stripe and scale marks. -->
  <path d="M-8 5 C-1 11 10 9 17 3 C11 11 -1 14 -9 8Z"
    fill="url(#fish-belly)" opacity=".8"/>
  <path d="M-8 -4 Q0 -9 9 -6" fill="none"
    stroke="#FFFFFF" stroke-opacity=".9" stroke-width="1.15" stroke-linecap="round"/>
  <path d="M-1 -5 Q2 -2 0 1 M5 -5 Q8 -2 6 1"
    fill="none" stroke="#2D79C8" stroke-opacity=".55" stroke-width=".75"/>

  <!-- Pectoral fin gives the fish a second, smaller swimming motion. -->
  <g transform="translate(1 3)">
    <animateTransform attributeName="transform" type="rotate"
      values="-5 0 0;12 0 0;-5 0 0" dur=".52s" repeatCount="indefinite"/>
    <path d="M0 0 Q-4 5 1 10 Q7 7 5 1Z"
      fill="url(#fish-fin)" stroke="#FFFFFF" stroke-opacity=".72" stroke-width=".65"/>
  </g>

  <!-- Gill and eye. -->
  <path d="M7 -6 Q3 0 7 6" fill="none"
    stroke="#3A8BCB" stroke-width="1.05" stroke-linecap="round"/>
  <ellipse cx="13" cy="-4.2" rx="2.2" ry="2.65" fill="#08223F"/>
  <circle cx="13.5" cy="-5.1" r=".85" fill="#FFFFFF"/>
  <circle cx="12.2" cy="-3.3" r=".45" fill="#5BE7FF"/>

  <!-- The jaw opens and closes as the fish reaches each food tile. -->
  <path d="M15 1 Q19 2.3 22 1.2" fill="none"
    stroke="#1266B8" stroke-width="1.25" stroke-linecap="round">
    <animate attributeName="d"
      values="M15 1 Q19 2.3 22 1.2;M15 1 Q20 7.4 25 5.2;M15 1 Q19 2.3 22 1.2"
      dur=".38s" repeatCount="indefinite"/>
  </path>
  <path d="M18 2.1L22 2.5" stroke="#FFFFFF" stroke-width=".55" opacity=".85"/>
  <path d="M-13 -3 Q-18 -1 -19 1" fill="none"
    stroke="#DDFBFF" stroke-opacity=".85" stroke-width=".7"/>
  </g>
</g>

<!-- Small air bubbles drift upward from the fish tank floor. -->
<g fill="none" stroke="#8FEAFF" stroke-opacity=".45">
  <circle cx="118" cy="207" r="1.8"><animate attributeName="cy" values="207;184;207" dur="6s" repeatCount="indefinite"/></circle>
  <circle cx="132" cy="216" r="1.1"><animate attributeName="cy" values="216;196;216" dur="7s" begin="1.3s" repeatCount="indefinite"/></circle>
  <circle cx="744" cy="207" r="1.6"><animate attributeName="cy" values="207;185;207" dur="6.5s" begin=".9s" repeatCount="indefinite"/></circle>
</g>

<path d="M22 215H838" stroke="#2B7395" stroke-opacity=".42"/>
<text x="27" y="231" font-family="Trebuchet MS, Arial, sans-serif"
      font-size="8.5" font-weight="600" letter-spacing=".8"
      fill="#77AFCB">{start.strftime("%b %d, %Y").upper()}  /  {end.strftime("%b %d, %Y").upper()}</text>
<g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
   font-weight="700" letter-spacing=".6" fill="#77AFCB">
  <text x="672" y="231">LESS</text>'''
    )

    for index, level in enumerate(
        ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    ):
        fill, stroke, _ = LEVEL_COLORS[level]
        x = 706 + index * 16
        pieces.append(
            f'<rect x="{x}" y="223" width="10" height="10" rx="2.8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width=".8"/>'
        )

    pieces.append(
        '''<text x="794" y="231">MORE</text>
</g>
</g>
<rect x=".7" y=".7" width="858.6" height="242.6" rx="13"
      fill="none" stroke="url(#glass-edge)" stroke-width="1.15"/>
<rect x="4" y="4" width="852" height="236" rx="10"
      fill="none" stroke="#DDFBFF" stroke-opacity=".045"/>
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
    active_days = sum(item["count"] > 0 for item in days.values())
    print(
        f"Rendered {total:,} contributions across {len(days)} days; "
        f"fish route covers {active_days} active contribution cells."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution aquarium update failed: {error}", file=sys.stderr)
        raise
