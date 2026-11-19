#!/usr/bin/env python3
"""Render GitHub activity as a living aquarium with a fish that eats each active day."""

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

GRID_X = 62
GRID_Y = 99
CELL = 10
PITCH = 15
LAUNCH_POINT = (39.0, 181.5)
FISH_MOUTH_OFFSET = 13.5
SWIM_SPEED = 125.0
EAT_DWELL = 0.68
RESET_FADE = 0.48

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
    "NONE": ("#122234", "#28435B"),
    "FIRST_QUARTILE": ("#246BFF", "#8DB0FF"),
    "SECOND_QUARTILE": ("#00B9FF", "#8CEBFF"),
    "THIRD_QUARTILE": ("#00E0C8", "#91FFF1"),
    "FOURTH_QUARTILE": ("#D34DFF", "#F4B4FF"),
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
            "User-Agent": "Kaelith69-contribution-aquarium/5.0",
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
    """Build Sunday-first calendar columns."""
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


def manhattan_distance(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def nearest_food_route(
    weeks: list[list[dt.date]],
    start: dt.date,
    end: dt.date,
    days: dict[dt.date, dict[str, Any]],
    launch_point: tuple[float, float] = LAUNCH_POINT,
) -> list[tuple[int, int, dt.date]]:
    """Choose the nearest unvisited active creature using orthogonal distance."""
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
                manhattan_distance(current, cell_center(item[0], item[1])),
                item[2].isoformat(),
            ),
        )
        route.append(next_cell)
        current = cell_center(next_cell[0], next_cell[1])
        remaining.remove(next_cell)

    return route


def approach_anchor(
    current: tuple[float, float],
    target: tuple[float, float],
) -> tuple[tuple[float, float], str]:
    """Stop the fish with its mouth over the food and define its final axis."""
    dx = target[0] - current[0]
    dy = target[1] - current[1]

    if dx == 0 and dy == 0:
        return target, "x"

    if dx != 0 and (dy == 0 or abs(dx) >= abs(dy)):
        offset = min(FISH_MOUTH_OFFSET, abs(dx) * 0.62)
        direction = 1 if dx > 0 else -1
        return (target[0] - direction * offset, target[1]), "x"

    offset = min(FISH_MOUTH_OFFSET, abs(dy) * 0.62)
    direction = 1 if dy > 0 else -1
    return (target[0], target[1] - direction * offset), "y"


def orthogonal_leg(
    start: tuple[float, float],
    end: tuple[float, float],
    final_axis: str,
) -> list[tuple[float, float]]:
    """Return horizontal and vertical waypoints only, ending on final_axis."""
    if final_axis not in ("x", "y"):
        raise ValueError("final_axis must be 'x' or 'y'.")

    if start == end:
        return []

    if start[0] == end[0] or start[1] == end[1]:
        return [end]

    corner = (
        (start[0], end[1])
        if final_axis == "x"
        else (end[0], start[1])
    )
    return [corner, end]


def build_flight_plan(
    route: list[tuple[int, int, dt.date]],
    launch_point: tuple[float, float] = LAUNCH_POINT,
) -> dict[str, Any]:
    """Construct a rectilinear path and timed feeding pauses at every food stop."""
    points: list[tuple[float, float]] = [launch_point]
    target_indices: dict[int, list[dt.date]] = {}
    current = launch_point

    for column, row, date in route:
        target = cell_center(column, row)
        anchor, final_axis = approach_anchor(current, target)

        for waypoint in orthogonal_leg(current, anchor, final_axis):
            if waypoint != points[-1]:
                points.append(waypoint)

        target_indices.setdefault(len(points) - 1, []).append(date)
        current = anchor

    if route:
        home_dx = launch_point[0] - current[0]
        home_dy = launch_point[1] - current[1]
        home_axis = "x" if abs(home_dx) >= abs(home_dy) else "y"
        for waypoint in orthogonal_leg(current, launch_point, home_axis):
            if waypoint != points[-1]:
                points.append(waypoint)

    if len(points) == 1:
        points.append((launch_point[0] + 1.0, launch_point[1]))

    segments = [
        abs(points[index][0] - points[index - 1][0])
        + abs(points[index][1] - points[index - 1][1])
        for index in range(1, len(points))
    ]
    if any(
        points[index][0] != points[index - 1][0]
        and points[index][1] != points[index - 1][1]
        for index in range(1, len(points))
    ):
        raise RuntimeError("Fish path contains a diagonal segment.")

    total_distance = sum(segments)
    if total_distance <= 0:
        raise RuntimeError("Fish path has no travel distance.")

    path = "M " + " L ".join(
        f"{x:.3f},{y:.3f}" for x, y in points
    )

    key_points = ["0"]
    key_times = [0.0]
    arrivals: dict[dt.date, tuple[float, float]] = {}
    mouth_frames: list[tuple[float, str]] = []
    elapsed = 0.0
    distance_so_far = 0.0

    def add_food_pause(index: int, distance_fraction: float) -> None:
        nonlocal elapsed
        for date in target_indices.get(index, []):
            arrival = elapsed
            open_at = arrival + EAT_DWELL * 0.12
            eaten_at = arrival + EAT_DWELL * 0.56
            close_at = arrival + EAT_DWELL * 0.82

            key_points.append(f"{distance_fraction:.8f}")
            key_times.append(open_at)
            key_points.append(f"{distance_fraction:.8f}")
            key_times.append(close_at)

            mouth_frames.append((open_at, "open"))
            mouth_frames.append((close_at, "closed"))
            arrivals[date] = (arrival, eaten_at)
            elapsed += EAT_DWELL

    add_food_pause(0, 0.0)

    for index, distance in enumerate(segments, start=1):
        distance_so_far += distance
        elapsed += distance / SWIM_SPEED
        fraction = distance_so_far / total_distance

        key_points.append(f"{fraction:.8f}")
        key_times.append(elapsed)
        add_food_pause(index, fraction)

    returned_at = elapsed
    cycle = returned_at + RESET_FADE

    key_points.append("1")
    key_times.append(cycle)

    if len(key_points) != len(key_times):
        raise RuntimeError("Fish path keyPoints and keyTimes lengths differ.")

    normalized_times = [value / cycle for value in key_times]
    if normalized_times[0] != 0 or abs(normalized_times[-1] - 1) > 1e-8:
        raise RuntimeError("Fish animation does not span one complete cycle.")
    if any(b <= a for a, b in zip(normalized_times, normalized_times[1:])):
        raise RuntimeError("Fish animation keyTimes are not strictly increasing.")

    open_path = "M13 1 Q18 2 21 1"
    closed_path = "M13 1 Q18 1.3 21 1"
    mouth_values = [closed_path]
    mouth_times = [0.0]
    for moment, state in mouth_frames:
        mouth_values.append(open_path if state == "open" else closed_path)
        mouth_times.append(moment / cycle)
    if len(mouth_values) == 1:
        mouth_values.append(closed_path)
        mouth_times.append(1.0)

    if any(b <= a for a, b in zip(mouth_times, mouth_times[1:])):
        raise RuntimeError("Fish jaw keyTimes are not strictly increasing.")

    reset_time = returned_at / cycle
    return {
        "points": points,
        "path": path,
        "key_points": ";".join(key_points),
        "key_times": ";".join(f"{value:.8f}" for value in normalized_times),
        "arrivals": arrivals,
        "cycle": cycle,
        "returned_at": returned_at,
        "reset_time": reset_time,
        "mouth_values": ";".join(mouth_values),
        "mouth_times": ";".join(f"{value:.8f}" for value in mouth_times),
    }


def creature_art(index: int, color: str, stroke: str) -> str:
    """Return one small sea creature silhouette centred on the day cell."""
    species = index % 4

    if species == 0:
        return f'''<path d="M-2.8 0 L-5.1 -2 L-5.1 2Z" fill="{color}" stroke="{stroke}" stroke-width=".45"/>
<path d="M-3.1 0 C-2.1 -2.5 2.1 -2.5 3.3 0 C2.1 2.5 -2.1 2.5 -3.1 0Z" fill="{color}" stroke="{stroke}" stroke-width=".55"/>
<circle cx="1.8" cy="-.7" r=".55" fill="#FFFFFF"/>'''
    if species == 1:
        return f'''<path d="M-3.4 1 C-3.3 -3 3.3 -3 3.4 1Z" fill="{color}" stroke="{stroke}" stroke-width=".55"/>
<path d="M-2.3 1 Q-3.3 3 -2.2 4 M0 1 Q-.7 3.2 0 4.2 M2.2 1 Q3.1 3 2 4" fill="none" stroke="{stroke}" stroke-width=".7" stroke-linecap="round"/>
<circle cx="-1.1" cy="-.1" r=".42" fill="#FFFFFF"/><circle cx="1.1" cy="-.1" r=".42" fill="#FFFFFF"/>'''
    if species == 2:
        return f'''<path d="M0 -4.1 L1.25 -1.55 L4 -1.3 L2 0.7 L2.5 3.5 L0 2.1 L-2.5 3.5 L-2 0.7 L-4 -1.3 L-1.25 -1.55Z" fill="{color}" stroke="{stroke}" stroke-width=".55" stroke-linejoin="round"/>
<circle cx="0" cy="0" r=".55" fill="#FFFFFF"/>'''
    return f'''<path d="M-4.4 0 Q-2.2 -3.5 0 -2.3 Q2.2 -3.5 4.4 0 Q2.2 3.5 0 2.3 Q-2.2 3.5 -4.4 0Z" fill="{color}" stroke="{stroke}" stroke-width=".55"/>
<path d="M0 -2.1V2.1" stroke="#FFFFFF" stroke-opacity=".7" stroke-width=".55"/>
<circle cx="2.2" cy="-.45" r=".4" fill="#FFFFFF"/>'''


def render_svg(
    start: dt.date,
    end: dt.date,
    total: int,
    days: dict[dt.date, dict[str, Any]],
) -> str:
    weeks = calendar_weeks(start, end)
    route = nearest_food_route(weeks, start, end, days)
    plan = build_flight_plan(route)
    cycle = plan["cycle"]
    reset_time = plan["reset_time"]

    pieces: list[str] = []
    pieces.append(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="252"
 viewBox="0 0 860 252" role="img" aria-labelledby="tank-title tank-desc">
<title id="tank-title">{html.escape(LOGIN)} contribution aquarium</title>
<desc id="tank-desc">{total:,} GitHub contributions from {start.isoformat()} to {end.isoformat()}. A detailed blue and white fish swims only horizontally and vertically to the nearest active sea creature, eats it, and all creatures return together when the tank is reset.</desc>
<defs>
  <linearGradient id="water" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#061B2D"/><stop offset=".48" stop-color="#041321"/><stop offset="1" stop-color="#020913"/>
  </linearGradient>
  <radialGradient id="water-light" cx="50%" cy="34%" r="72%">
    <stop stop-color="#0B6B9B" stop-opacity=".2"/><stop offset=".6" stop-color="#06446F" stop-opacity=".065"/><stop offset="1" stop-color="#020913" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="glass-edge" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#5FEAFF" stop-opacity=".78"/><stop offset=".5" stop-color="#245F8B" stop-opacity=".3"/><stop offset="1" stop-color="#B5F7FF" stop-opacity=".62"/>
  </linearGradient>
  <linearGradient id="fish-body" x1=".12" y1="0" x2=".82" y2="1">
    <stop stop-color="#FFFFFF"/><stop offset=".35" stop-color="#EAF8FF"/><stop offset=".72" stop-color="#9AD5FF"/><stop offset="1" stop-color="#2E82E8"/>
  </linearGradient>
  <linearGradient id="fish-fin" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#F5FFFF"/><stop offset=".4" stop-color="#55CFFF"/><stop offset="1" stop-color="#1558DB"/>
  </linearGradient>
  <linearGradient id="fish-belly" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#FFFFFF" stop-opacity=".08"/><stop offset="1" stop-color="#FFFFFF" stop-opacity=".8"/>
  </linearGradient>
  <linearGradient id="bubble" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#FFFFFF" stop-opacity=".82"/><stop offset=".45" stop-color="#75DFFF" stop-opacity=".38"/><stop offset="1" stop-color="#2588FF" stop-opacity=".08"/>
  </linearGradient>
  <filter id="fish-glow" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur stdDeviation="2.3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="creature-glow" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur stdDeviation="1.1" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="tank-clip"><rect x="1" y="1" width="858" height="250" rx="13"/></clipPath>
</defs>
<g clip-path="url(#tank-clip)">
  <rect width="860" height="252" fill="url(#water)"/>
  <rect width="860" height="252" fill="url(#water-light)"/>

  <g fill="none" stroke="#45C8F2" stroke-linecap="round">
    <path d="M-25 61 Q105 43 225 61 T475 60 T735 58 T900 64" stroke-opacity=".09" stroke-width="1.2"/>
    <path d="M-22 68 Q108 50 228 68 T478 67 T738 65 T902 71" stroke-opacity=".045" stroke-width=".8"/>
  </g>

  <g fill="none" stroke="url(#bubble)" stroke-width="1">
    <circle cx="27" cy="111" r="3"><animate attributeName="cy" values="111;82;111" dur="8s" repeatCount="indefinite"/></circle>
    <circle cx="39" cy="151" r="1.8"><animate attributeName="cy" values="151;125;151" dur="9s" begin="1.4s" repeatCount="indefinite"/></circle>
    <circle cx="831" cy="118" r="2.6"><animate attributeName="cy" values="118;88;118" dur="7s" begin=".7s" repeatCount="indefinite"/></circle>
    <circle cx="842" cy="169" r="1.7"><animate attributeName="cy" values="169;143;169" dur="8.5s" begin="2s" repeatCount="indefinite"/></circle>
    <circle cx="21" cy="203" r="1.2"><animate attributeName="cy" values="203;179;203" dur="7.5s" begin="2.8s" repeatCount="indefinite"/></circle>
  </g>

  <g fill="none" stroke="#267A8C" stroke-width="1.4" stroke-linecap="round" opacity=".35">
    <path d="M18 251 Q18 235 13 220 M18 251 Q24 237 29 226 M18 251 Q13 241 8 237"/>
    <path d="M839 251 Q840 237 847 222 M839 251 Q832 239 827 230 M839 251 Q846 242 852 239"/>
  </g>

  <path d="M20 57 Q180 53 340 57 T660 56 T840 57" fill="none" stroke="#55DFFF" stroke-opacity=".35"/>
  <path d="M20 59 Q180 55 340 59 T660 58 T840 59" fill="none" stroke="#FFFFFF" stroke-opacity=".08"/>

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
        pieces.append(f'<text x="{GRID_X + column * PITCH}" y="80">{label}</text>')

    for row, label in enumerate(("S", "M", "T", "W", "T", "F", "S")):
        pieces.append(
            f'<text x="37" y="{GRID_Y + row * PITCH + 7}" text-anchor="middle">{label}</text>'
        )
    pieces.append("</g>")

    day_index = 0
    for column, week in enumerate(weeks):
        for row, date in enumerate(week):
            if not start <= date <= end:
                continue

            detail = days[date]
            count = int(detail["count"])
            level = str(detail["level"])
            fill, stroke = LEVEL_COLORS[level]
            x = GRID_X + column * PITCH + CELL / 2
            y = GRID_Y + row * PITCH + CELL / 2
            animation = ""

            if count > 0:
                arrival, eaten = plan["arrivals"][date]
                eat_start = (arrival + EAT_DWELL * .18) / cycle
                eat_end = (arrival + EAT_DWELL * .65) / cycle
                animation = (
                    f'<animate attributeName="opacity" values="1;1;0;0;1" '
                    f'keyTimes="0;{eat_start:.8f};{eat_end:.8f};'
                    f'{reset_time:.8f};1" dur="{cycle:.6f}s" '
                    f'repeatCount="indefinite"/>'
                )

            art = creature_art(day_index, fill, stroke)
            base_opacity = "1" if count > 0 else ".3"
            pieces.append(
                f'''<g transform="translate({x:.1f} {y:.1f})"
 data-date="{date.isoformat()}" data-count="{count}" data-level="{level}"
 opacity="{base_opacity}" {f'filter="url(#creature-glow)"' if count else ''}>
 <title>{date.isoformat()} · {count} contribution{"s" if count != 1 else ""}</title>
 {art}
 {animation}
</g>'''
            )
            day_index += 1

    if plan["points"]:
        route_path = "M " + " L ".join(
            f"{x:.3f},{y:.3f}" for x, y in plan["points"]
        )
        pieces.append(
            f'<path d="{route_path}" fill="none" stroke="#67DFFF" '
            f'stroke-width=".75" stroke-opacity=".13" stroke-dasharray="1.5 5"/>'
        )

    pieces.append(
        f'''<g id="fish" filter="url(#fish-glow)">
  <animateMotion path="{plan["path"]}"
    keyPoints="{plan["key_points"]}" keyTimes="{plan["key_times"]}"
    calcMode="linear" dur="{cycle:.6f}s" rotate="auto"
    repeatCount="indefinite"/>
  <animate attributeName="opacity" values="1;0;0;1"
    keyTimes="0;{reset_time:.8f};{(cycle - .001) / cycle:.8f};1"
    dur="{cycle:.6f}s" repeatCount="indefinite"/>

  <g transform="scale(.70)">
    <g transform="translate(-15 0)">
      <animateTransform attributeName="transform" type="rotate"
        values="-13 0 0;13 0 0;-13 0 0" dur=".36s" repeatCount="indefinite"/>
      <path d="M0 0 C-7 -7 -17 -13 -20 -9 C-18 -4 -16 -1 -21 0
        C-16 1 -18 4 -20 9 C-17 13 -7 7 0 0Z"
        fill="url(#fish-fin)" stroke="#D9F8FF" stroke-width=".8"/>
      <path d="M-2 0L-16 -7 M-2 0L-16 7" stroke="#FFFFFF"
        stroke-opacity=".55" stroke-width=".7" fill="none"/>
    </g>

    <path d="M-6 -9 Q-3 -18 5 -16 Q10 -14 12 -9Z"
      fill="url(#fish-fin)" stroke="#D9F8FF" stroke-width=".8"/>
    <path d="M-5 10 Q1 18 8 14 L10 9Z"
      fill="url(#fish-fin)" stroke="#D9F8FF" stroke-width=".7"/>

    <path d="M-16 0 C-13 -10 -1 -14 10 -10
      C17 -8 21 -3 22 0 C21 3 17 8 10 10
      C-1 14 -13 10 -16 0Z"
      fill="url(#fish-body)" stroke="#E8FBFF" stroke-width="1.05"/>

    <path d="M-8 5 C-1 11 10 9 17 3 C11 11 -1 14 -9 8Z"
      fill="url(#fish-belly)" opacity=".8"/>
    <path d="M-8 -4 Q0 -9 9 -6" fill="none"
      stroke="#FFFFFF" stroke-opacity=".9" stroke-width="1.15" stroke-linecap="round"/>
    <path d="M-1 -5 Q2 -2 0 1 M5 -5 Q8 -2 6 1"
      fill="none" stroke="#2D79C8" stroke-opacity=".55" stroke-width=".75"/>

    <g transform="translate(1 3)">
      <animateTransform attributeName="transform" type="rotate"
        values="-5 0 0;12 0 0;-5 0 0" dur=".52s" repeatCount="indefinite"/>
      <path d="M0 0 Q-4 5 1 10 Q7 7 5 1Z"
        fill="url(#fish-fin)" stroke="#FFFFFF" stroke-opacity=".72" stroke-width=".65"/>
    </g>

    <path d="M7 -6 Q3 0 7 6" fill="none"
      stroke="#3A8BCB" stroke-width="1.05" stroke-linecap="round"/>
    <ellipse cx="13" cy="-4.2" rx="2.2" ry="2.65" fill="#08223F"/>
    <circle cx="13.5" cy="-5.1" r=".85" fill="#FFFFFF"/>
    <circle cx="12.2" cy="-3.3" r=".45" fill="#5BE7FF"/>

    <path d="M13 1 Q18 1.3 21 1" fill="none"
      stroke="#1266B8" stroke-width="1.25" stroke-linecap="round">
      <animate attributeName="d" values="{plan["mouth_values"]}"
        keyTimes="{plan["mouth_times"]}" calcMode="linear"
        dur="{cycle:.6f}s" repeatCount="indefinite"/>
    </path>
    <path d="M-13 -3 Q-18 -1 -19 1" fill="none"
      stroke="#DDFBFF" stroke-opacity=".85" stroke-width=".7"/>
  </g>
</g>

<g fill="none" stroke="#8FEAFF" stroke-opacity=".42">
  <circle cx="116" cy="215" r="1.7"><animate attributeName="cy" values="215;188;215" dur="6s" repeatCount="indefinite"/></circle>
  <circle cx="131" cy="222" r="1.1"><animate attributeName="cy" values="222;200;222" dur="7s" begin="1.3s" repeatCount="indefinite"/></circle>
  <circle cx="746" cy="213" r="1.6"><animate attributeName="cy" values="213;188;213" dur="6.5s" begin=".9s" repeatCount="indefinite"/></circle>
</g>

<path d="M22 221H838" stroke="#2B7395" stroke-opacity=".42"/>
<text x="27" y="237" font-family="Trebuchet MS, Arial, sans-serif"
      font-size="8.5" font-weight="600" letter-spacing=".8"
      fill="#77AFCB">{start.strftime("%b %d, %Y").upper()}  /  {end.strftime("%b %d, %Y").upper()}</text>
<g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
   font-weight="700" letter-spacing=".6" fill="#77AFCB">
  <text x="672" y="237">LESS</text>'''
    )

    for index, level in enumerate(
        ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    ):
        fill, stroke = LEVEL_COLORS[level]
        pieces.append(
            f'<rect x="{706 + index * 16}" y="229" width="10" height="10" rx="2.8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width=".8"/>'
        )

    pieces.append(
        '''<text x="794" y="237">MORE</text>
</g>
</g>
<rect x=".7" y=".7" width="858.6" height="250.6" rx="13"
      fill="none" stroke="url(#glass-edge)" stroke-width="1.15"/>
<rect x="4" y="4" width="852" height="244" rx="10"
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
        f"the fish route feeds on {active_days} active day creatures."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution aquarium update failed: {error}", file=sys.stderr)
        raise
