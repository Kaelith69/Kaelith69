#!/usr/bin/env python3
"""Render GitHub contribution days as aquatic creatures in an animated fish tank."""

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

GRID_X = 58
GRID_Y = 98
CELL = 9
PITCH = 15
LAUNCH_POINT = (42.0, 205.0)
FISH_SPEED = 115.0
EAT_DWELL = 0.58
RESET_FADE = 0.42
RESET_HOLD = 0.22


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
    "NONE": ("#102033", "#27445D", "quiet"),
    "FIRST_QUARTILE": ("#174BFF", "#86A7FF", "electric blue"),
    "SECOND_QUARTILE": ("#00A9FF", "#9AEAFF", "cyan"),
    "THIRD_QUARTILE": ("#00D9C2", "#A2FFF1", "aqua"),
    "FOURTH_QUARTILE": ("#CE42FF", "#F3B8FF", "violet"),
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
    """Return Sunday first columns matching GitHub's contribution calendar."""
    first_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    last_sunday = end - dt.timedelta(days=(end.weekday() + 1) % 7)
    count = ((last_sunday - first_sunday).days // 7) + 1
    return [
        [first_sunday + dt.timedelta(days=column * 7 + row) for row in range(7)]
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
    """Choose the closest remaining food using Manhattan distance after each meal."""
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
                abs(cell_center(item[0], item[1])[0] - current[0])
                + abs(cell_center(item[0], item[1])[1] - current[1]),
                item[2].isoformat(),
            ),
        )
        route.append(next_cell)
        current = cell_center(next_cell[0], next_cell[1])
        remaining.remove(next_cell)

    return route


def add_orthogonal_point(
    points: list[tuple[float, float]],
    point: tuple[float, float],
) -> None:
    """Append a point only when it adds a nonzero horizontal or vertical segment."""
    if points and math.dist(points[-1], point) < 1e-7:
        return
    if points:
        previous = points[-1]
        if abs(previous[0] - point[0]) > 1e-7 and abs(previous[1] - point[1]) > 1e-7:
            raise RuntimeError(
                f"Diagonal movement rejected: {previous!r} to {point!r}."
            )
    points.append(point)


def orthogonal_flight_points(
    route: list[tuple[int, int, dt.date]],
    launch_point: tuple[float, float] = LAUNCH_POINT,
) -> tuple[list[tuple[float, float]], dict[dt.date, int]]:
    """Build an axis aligned polyline through each selected food, then return home."""
    points = [launch_point]
    stop_indices: dict[dt.date, int] = {}
    current = launch_point

    for column, row, date in route:
        target = cell_center(column, row)
        target_x, target_y = target

        # Horizontal first, then vertical. Every segment is strictly axis aligned.
        add_orthogonal_point(points, (target_x, current[1]))
        add_orthogonal_point(points, target)
        stop_indices[date] = len(points) - 1
        current = target

    # Return to the launch point on the same orthogonal track system.
    add_orthogonal_point(points, (launch_point[0], current[1]))
    add_orthogonal_point(points, launch_point)

    if not route:
        add_orthogonal_point(points, (launch_point[0] + 1, launch_point[1]))
        add_orthogonal_point(points, launch_point)

    return points, stop_indices


def flight_timeline(
    route: list[tuple[int, int, dt.date]],
    launch_point: tuple[float, float] = LAUNCH_POINT,
) -> dict[str, Any]:
    points, stop_indices = orthogonal_flight_points(route, launch_point)
    segment_lengths = [
        math.dist(points[index - 1], points[index])
        for index in range(1, len(points))
    ]
    total_distance = sum(segment_lengths)
    if total_distance <= 0:
        raise RuntimeError("Fish flight path has no measurable length.")

    key_points = ["0"]
    key_times = ["0"]
    arrivals: dict[dt.date, tuple[float, float]] = {}
    elapsed = 0.0
    distance_so_far = 0.0

    for point_index, distance in enumerate(segment_lengths, start=1):
        elapsed += distance / FISH_SPEED
        distance_so_far += distance
        fraction = distance_so_far / total_distance
        key_points.append(f"{fraction:.9f}")
        key_times.append(f"{elapsed:.9f}")

        target_date = next(
            (date for date, stop_index in stop_indices.items() if stop_index == point_index),
            None,
        )
        if target_date is not None:
            arrival = elapsed
            eaten = arrival + EAT_DWELL
            key_points.append(f"{fraction:.9f}")
            key_times.append(f"{eaten:.9f}")
            arrivals[target_date] = (arrival, eaten)
            elapsed = eaten

    returned_at = elapsed
    reset_start = returned_at + RESET_HOLD
    cycle = reset_start + RESET_FADE

    # The final path segment already reaches home at returned_at.
    # Hold at launch, then fade and reset without repeating a key time.
    key_points.append("1")
    key_times.append(f"{reset_start:.9f}")
    key_points.append("1")
    key_times.append(f"{cycle:.9f}")

    normalized_times = [float(value) / cycle for value in key_times]
    if any(b <= a for a, b in zip(normalized_times, normalized_times[1:])):
        raise RuntimeError("Fish animation key times must increase strictly.")
    if abs(normalized_times[-1] - 1.0) > 1e-8:
        raise RuntimeError("Fish animation timeline does not end at one.")

    path = "M " + " L ".join(
        f"{x:.3f},{y:.3f}" for x, y in points
    )
    for first, second in zip(points, points[1:]):
        if abs(first[0] - second[0]) > 1e-7 and abs(first[1] - second[1]) > 1e-7:
            raise RuntimeError("Generated fish flight path contains a diagonal segment.")

    return {
        "points": points,
        "path": path,
        "key_points": ";".join(key_points),
        "key_times": ";".join(f"{value:.9f}" for value in normalized_times),
        "arrivals": arrivals,
        "returned_at": returned_at,
        "reset_start": reset_start,
        "cycle": cycle,
        "reset_start_fraction": reset_start / cycle,
        "fish_fade_start_fraction": reset_start / cycle,
        "fish_fade_end_fraction": (reset_start + RESET_FADE * .85) / cycle,
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

    pieces: list[str] = []
    pieces.append(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="258"
 viewBox="0 0 860 258" role="img" aria-labelledby="tank-title tank-desc">
<title id="tank-title">{html.escape(LOGIN)} contribution aquarium</title>
<desc id="tank-desc">{total:,} GitHub contributions from {start.isoformat()} to {end.isoformat()}. A detailed white and blue fish swims to the nearest contribution creature using horizontal and vertical movements only. Each active creature disappears when eaten and all creatures reset after the fish returns.</desc>
<defs>
  <linearGradient id="water" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#07182B"/><stop offset=".48" stop-color="#041321"/><stop offset="1" stop-color="#020913"/>
  </linearGradient>
  <radialGradient id="water-light" cx="48%" cy="34%" r="72%">
    <stop stop-color="#0B78A5" stop-opacity=".2"/><stop offset=".6" stop-color="#06446F" stop-opacity=".065"/><stop offset="1" stop-color="#020913" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="glass-edge" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#7CF3FF" stop-opacity=".78"/><stop offset=".52" stop-color="#245F8B" stop-opacity=".38"/><stop offset="1" stop-color="#D6FAFF" stop-opacity=".68"/>
  </linearGradient>
  <linearGradient id="fish-body" x1=".12" y1="0" x2=".82" y2="1">
    <stop stop-color="#FFFFFF"/><stop offset=".32" stop-color="#ECF9FF"/><stop offset=".72" stop-color="#A3D8FF"/><stop offset="1" stop-color="#4D9CEB"/>
  </linearGradient>
  <linearGradient id="fish-fin" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#FFFFFF"/><stop offset=".4" stop-color="#64D6FF"/><stop offset="1" stop-color="#1558D8"/>
  </linearGradient>
  <linearGradient id="fish-belly" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#FFFFFF" stop-opacity=".06"/><stop offset="1" stop-color="#FFFFFF" stop-opacity=".78"/>
  </linearGradient>
  <linearGradient id="prey-blue" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#C6F7FF"/><stop offset=".45" stop-color="#27BFFF"/><stop offset="1" stop-color="#175BFF"/>
  </linearGradient>
  <filter id="fish-glow" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur stdDeviation="2.1" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="creature-glow" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur stdDeviation="1.2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="tank-clip"><rect x="1" y="1" width="858" height="256" rx="13"/></clipPath>
</defs>
<g clip-path="url(#tank-clip)">
  <rect width="860" height="258" fill="url(#water)"/>
  <rect width="860" height="258" fill="url(#water-light)"/>

  <!-- Subtle surface caustics and the aquarium glass refraction. -->
  <path d="M18 60 Q160 48 300 59 T580 58 T842 60" fill="none" stroke="#78E8FF" stroke-opacity=".3"/>
  <path d="M18 64 Q160 52 300 63 T580 62 T842 64" fill="none" stroke="#FFFFFF" stroke-opacity=".065"/>
  <g fill="none" stroke="#61DFFF" stroke-linecap="round">
    <path d="M-30 73 Q120 58 240 72 T490 70 T740 71 T900 69" stroke-opacity=".065" stroke-width="1.1"/>
    <path d="M-20 78 Q130 63 250 77 T500 75 T750 76 T900 74" stroke-opacity=".035" stroke-width=".8"/>
  </g>

  <!-- Small bubbles are deliberately kept away from the food field. -->
  <g fill="none" stroke="#9CEBFF" stroke-opacity=".46">
    <circle cx="28" cy="126" r="2"><animate attributeName="cy" values="126;97;126" dur="8s" repeatCount="indefinite"/></circle>
    <circle cx="39" cy="169" r="1.4"><animate attributeName="cy" values="169;139;169" dur="9s" begin="1.2s" repeatCount="indefinite"/></circle>
    <circle cx="827" cy="117" r="2.4"><animate attributeName="cy" values="117;88;117" dur="7s" begin=".6s" repeatCount="indefinite"/></circle>
    <circle cx="839" cy="169" r="1.5"><animate attributeName="cy" values="169;145;169" dur="8.5s" begin="2s" repeatCount="indefinite"/></circle>
    <circle cx="23" cy="211" r="1.1"><animate attributeName="cy" values="211;190;211" dur="7.5s" begin="2.5s" repeatCount="indefinite"/></circle>
  </g>

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
            f'<text x="48" y="{GRID_Y + row * PITCH + 7}" text-anchor="middle">{label}</text>'
        )
    pieces.append("</g>")

    # Every date is a tiny fish fry. Activity controls its neon intensity and hue.
    for column, week in enumerate(weeks):
        for row, date in enumerate(week):
            if not start <= date <= end:
                continue

            detail = days[date]
            count = int(detail["count"])
            level = str(detail["level"])
            fill, stroke, _ = LEVEL_COLORS[level]
            x, y = cell_center(column, row)
            animation = ""

            if count > 0:
                arrival, eaten = timeline["arrivals"][date]
                fade_start = (arrival + EAT_DWELL * .12) / cycle
                fade_end = (arrival + EAT_DWELL * .54) / cycle
                animation = (
                    f'<animate attributeName="opacity" values="1;1;0;0;1" '
                    f'keyTimes="0;{fade_start:.8f};{fade_end:.8f};'
                    f'{timeline["reset_start_fraction"]:.8f};1" '
                    f'dur="{cycle:.6f}s" repeatCount="indefinite"/>'
                )

            opacity = "1" if count else ".42"
            glow = 'filter="url(#creature-glow)"' if count else ""
            pieces.append(
                f'''<g transform="translate({x:.2f} {y:.2f})"
 data-date="{date.isoformat()}" data-count="{count}" data-level="{level}"
 opacity="{opacity}" {glow}>
 <title>{date.isoformat()} · {count} contribution{"s" if count != 1 else ""}</title>
 <path d="M-2.7 0 L-4.8 -1.55 Q-5.7 0 -4.8 1.55Z"
       fill="{stroke}" stroke="{stroke}" stroke-width=".42"/>
 <ellipse cx="-.1" cy="0" rx="3.35" ry="2.05"
          fill="{fill}" stroke="{stroke}" stroke-width=".68"/>
 <path d="M-1.7 -1.05 Q-.1 -2.0 1.6 -1.0"
       fill="none" stroke="#FFFFFF" stroke-opacity=".72" stroke-width=".46"/>
 <circle cx="2.45" cy="-.55" r=".46" fill="#061421"/>
 <circle cx="2.57" cy="-.68" r=".17" fill="#FFFFFF"/>
 {animation}
</g>'''
            )

    # The fish glides on an axis aligned path, turning only at right angles.
    if route:
        fish_motion = (
            f'<animateMotion path="{timeline["path"]}" '
            f'keyPoints="{timeline["key_points"]}" keyTimes="{timeline["key_times"]}" '
            f'calcMode="linear" dur="{cycle:.6f}s" rotate="auto" '
            f'repeatCount="indefinite"/>'
        )
        fish_opacity = (
            f'<animate attributeName="opacity" values="1;1;0;0" '
            f'keyTimes="0;{timeline["reset_start_fraction"]:.8f};'
            f'{timeline["fish_fade_end_fraction"]:.8f};1" '
            f'dur="{cycle:.6f}s" repeatCount="indefinite"/>'
        )
    else:
        fish_motion = '<animateTransform attributeName="transform" type="translate" values="0 0;0 -2;0 0" dur="2.8s" repeatCount="indefinite"/>'
        fish_opacity = '<animate attributeName="opacity" values="1;.94;1" dur="2.8s" repeatCount="indefinite"/>'

    # One detailed fish. Local +X is its nose, so rotate="auto" turns it onto each axis.
    jaw_values = ["M17 1 Q21 2.2 24 1"]
    jaw_times = ["0"]
    for arrival, eaten in timeline["arrivals"].values():
        jaw_times.extend([
            f"{(arrival + EAT_DWELL * .12) / cycle:.8f}",
            f"{(arrival + EAT_DWELL * .54) / cycle:.8f}",
        ])
        jaw_values.extend([
            "M17 1 Q23 8 27 5",
            "M17 1 Q21 2.2 24 1",
        ])
    if timeline["arrivals"]:
        jaw_times.append("1")
        jaw_values.append("M17 1 Q21 2.2 24 1")
        jaw_markup = (
            f'<animate attributeName="d" calcMode="discrete" '
            f'values="{";".join(jaw_values)}" keyTimes="{";".join(jaw_times)}" '
            f'dur="{cycle:.6f}s" repeatCount="indefinite"/>'
        )
    else:
        jaw_markup = ""

    pieces.append(
        f'''<g id="aquarium-fish" filter="url(#fish-glow)">
  {fish_motion}
  {fish_opacity}
  <g transform="scale(.58)">
    <!-- Tail fin. -->
    <g transform="translate(-15 0)">
      <animateTransform attributeName="transform" type="rotate"
        values="-14 0 0;14 0 0;-14 0 0" dur=".36s" repeatCount="indefinite"/>
      <path d="M0 0 C-7 -7 -17 -13 -21 -8 C-18 -3 -17 -1 -22 0
        C-17 1 -18 3 -21 8 C-17 13 -7 7 0 0Z"
        fill="url(#fish-fin)" stroke="#E5FBFF" stroke-width=".9"/>
      <path d="M-2 0L-17 -7 M-2 0L-17 7" stroke="#FFFFFF"
        stroke-opacity=".6" stroke-width=".7" fill="none"/>
    </g>

    <!-- Dorsal and ventral fins. -->
    <path d="M-7 -9 Q-4 -18 5 -16 Q11 -14 12 -9Z"
      fill="url(#fish-fin)" stroke="#E5FBFF" stroke-width=".8"/>
    <path d="M-6 10 Q0 18 8 14 L10 9Z"
      fill="url(#fish-fin)" stroke="#E5FBFF" stroke-width=".7"/>

    <!-- Body, belly and reflective flank. -->
    <path d="M-17 0 C-14 -11 -2 -15 10 -11
      C18 -8 22 -3 24 0 C22 3 18 8 10 11
      C-2 15 -14 11 -17 0Z"
      fill="url(#fish-body)" stroke="#F1FDFF" stroke-width="1.15"/>
    <path d="M-8 5 C-1 12 11 10 19 3 C11 12 -2 15 -10 8Z"
      fill="url(#fish-belly)" opacity=".82"/>
    <path d="M-8 -5 Q1 -10 12 -6" fill="none"
      stroke="#FFFFFF" stroke-opacity=".92" stroke-width="1.15" stroke-linecap="round"/>
    <path d="M-2 -5 Q1 -2 -1 1 M4 -6 Q7 -3 5 0"
      fill="none" stroke="#367EBB" stroke-opacity=".6" stroke-width=".8"/>

    <!-- Pectoral fin and gill. -->
    <g transform="translate(0 3)">
      <animateTransform attributeName="transform" type="rotate"
        values="-6 0 0;13 0 0;-6 0 0" dur=".48s" repeatCount="indefinite"/>
      <path d="M0 0 Q-5 6 1 11 Q8 7 6 1Z"
        fill="url(#fish-fin)" stroke="#FFFFFF" stroke-opacity=".8" stroke-width=".7"/>
    </g>
    <path d="M7 -7 Q3 0 7 7" fill="none"
      stroke="#347CBD" stroke-width="1.1" stroke-linecap="round"/>

    <!-- Eye, mouth and animated lower jaw. -->
    <ellipse cx="14" cy="-4.4" rx="2.3" ry="2.75" fill="#071B35"/>
    <circle cx="14.6" cy="-5.3" r=".9" fill="#FFFFFF"/>
    <circle cx="13.3" cy="-3.4" r=".45" fill="#56E8FF"/>
    <path d="M17 1 Q21 2.2 24 1" fill="none"
      stroke="#135DA8" stroke-width="1.5" stroke-linecap="round">
      {jaw_markup}
    </path>
    <path d="M18 2L22 2.5" stroke="#FFFFFF" stroke-width=".55" opacity=".9"/>
  </g>
</g>

<!-- Fine plant silhouettes frame the floor without competing with the calendar. -->
<g fill="none" stroke="#1A967F" stroke-opacity=".3" stroke-width="1.6" stroke-linecap="round">
  <path d="M16 256 Q22 230 15 216 Q29 228 25 244 Q35 225 33 213"/>
  <path d="M844 256 Q837 233 844 219 Q831 230 834 246 Q824 229 826 215"/>
</g>

<!-- Soft air bubbles move independently of the fish. -->
<g fill="none" stroke="#A3EEFF" stroke-opacity=".5">
  <circle cx="115" cy="223" r="1.7"><animate attributeName="cy" values="223;194;223" dur="6.2s" repeatCount="indefinite"/></circle>
  <circle cx="129" cy="231" r="1.1"><animate attributeName="cy" values="231;208;231" dur="7.3s" begin="1.2s" repeatCount="indefinite"/></circle>
  <circle cx="744" cy="225" r="1.5"><animate attributeName="cy" values="225;199;225" dur="6.6s" begin=".8s" repeatCount="indefinite"/></circle>
</g>

<path d="M22 215H838" stroke="#2B7395" stroke-opacity=".42"/>
<text x="27" y="235" font-family="Trebuchet MS, Arial, sans-serif"
      font-size="8.5" font-weight="600" letter-spacing=".8"
      fill="#77AFCB">{start.strftime("%b %d, %Y").upper()}  /  {end.strftime("%b %d, %Y").upper()}</text>
<g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
   font-weight="700" letter-spacing=".6" fill="#77AFCB">
  <text x="672" y="235">LESS</text>'''
    )

    for index, level in enumerate(
        ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    ):
        fill, stroke, _ = LEVEL_COLORS[level]
        pieces.append(
            f'<rect x="{706 + index * 16}" y="227" width="10" height="10" rx="3" '
            f'fill="{fill}" stroke="{stroke}" stroke-width=".8"/>'
        )

    pieces.append(
        '''<text x="794" y="235">MORE</text>
</g>
</g>
<rect x=".7" y=".7" width="858.6" height="256.6" rx="13"
      fill="none" stroke="url(#glass-edge)" stroke-width="1.15"/>
<rect x="4" y="4" width="852" height="250" rx="10"
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
        f"fish route eats {active_days} active contribution creatures."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution aquarium update failed: {error}", file=sys.stderr)
        raise
