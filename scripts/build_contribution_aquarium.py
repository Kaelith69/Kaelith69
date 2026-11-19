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

GRID_X = 70
GRID_Y = 135
CELL = 11
PITCH = 19
ROW_PITCH = 22
LAUNCH_POINT = (48.0, 301.0)
FISH_SPEED = 105.0
EAT_DWELL = 0.72
RESET_FADE = 0.72
RESET_HOLD = 0.45
FISH_MOUTH_OFFSET = 10.5


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
    "NONE": ("#18313C", "#41616B", "quiet plankton"),
    "FIRST_QUARTILE": ("#2876A6", "#9BD9EF", "reef fish"),
    "SECOND_QUARTILE": ("#21A9B7", "#B0F1E8", "reef shrimp"),
    "THIRD_QUARTILE": ("#55D6B0", "#C5F7E8", "jellyfish"),
    "FOURTH_QUARTILE": ("#FFC879", "#FFF0C9", "glow star"),
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
        GRID_Y + row * ROW_PITCH + CELL / 2,
    )


def fish_target_anchor(
    current: tuple[float, float],
    target: tuple[float, float],
) -> tuple[float, float]:
    """Stop with the fish's nose touching the target creature."""
    dx = target[0] - current[0]
    dy = target[1] - current[1]

    # The route is horizontal first, then vertical. The final approach is
    # vertical whenever rows differ, otherwise horizontal.
    if dy != 0:
        offset = min(FISH_MOUTH_OFFSET, abs(dy) * 0.62)
        return (target[0], target[1] - (1 if dy > 0 else -1) * offset)
    if dx != 0:
        offset = min(FISH_MOUTH_OFFSET, abs(dx) * 0.62)
        return (target[0] - (1 if dx > 0 else -1) * offset, target[1])
    return target


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
        current = fish_target_anchor(current, cell_center(next_cell[0], next_cell[1]))
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
        target = fish_target_anchor(current, cell_center(column, row))
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
    """Build a smooth, rounded motion path with explicit pauses at each meal."""
    points, stop_indices = orthogonal_flight_points(route, launch_point)
    stop_dates_by_index: dict[int, list[dt.date]] = {}
    for date, index in stop_indices.items():
        stop_dates_by_index.setdefault(index, []).append(date)

    commands = [f"M {points[0][0]:.3f},{points[0][1]:.3f}"]
    samples: list[tuple[float, float]] = [points[0]]
    stop_distances: dict[dt.date, float] = {}
    distance_so_far = 0.0

    def add_line(point: tuple[float, float]) -> None:
        nonlocal distance_so_far
        previous = samples[-1]
        distance = math.dist(previous, point)
        if distance < 1e-7:
            return
        commands.append(f"L {point[0]:.3f},{point[1]:.3f}")
        samples.append(point)
        distance_so_far += distance

    for index in range(1, len(points) - 1):
        corner = points[index]
        if index in stop_dates_by_index:
            add_line(corner)
            for date in stop_dates_by_index[index]:
                stop_distances[date] = distance_so_far
            continue

        previous = points[index - 1]
        following = points[index + 1]
        incoming_length = math.dist(previous, corner)
        outgoing_length = math.dist(corner, following)
        radius = min(8.0, incoming_length * 0.32, outgoing_length * 0.32)

        if radius < 1.0:
            add_line(corner)
            continue

        incoming_unit = (
            (previous[0] - corner[0]) / incoming_length,
            (previous[1] - corner[1]) / incoming_length,
        )
        outgoing_unit = (
            (following[0] - corner[0]) / outgoing_length,
            (following[1] - corner[1]) / outgoing_length,
        )
        entry = (
            corner[0] + incoming_unit[0] * radius,
            corner[1] + incoming_unit[1] * radius,
        )
        exit_point = (
            corner[0] + outgoing_unit[0] * radius,
            corner[1] + outgoing_unit[1] * radius,
        )
        add_line(entry)
        commands.append(
            f"Q {corner[0]:.3f},{corner[1]:.3f} "
            f"{exit_point[0]:.3f},{exit_point[1]:.3f}"
        )

        start_point = samples[-1]
        for step in range(1, 17):
            t = step / 16
            one_minus_t = 1 - t
            sample = (
                one_minus_t * one_minus_t * start_point[0]
                + 2 * one_minus_t * t * corner[0]
                + t * t * exit_point[0],
                one_minus_t * one_minus_t * start_point[1]
                + 2 * one_minus_t * t * corner[1]
                + t * t * exit_point[1],
            )
            distance_so_far += math.dist(samples[-1], sample)
            samples.append(sample)

    add_line(points[-1])
    for date in stop_dates_by_index.get(len(points) - 1, []):
        stop_distances[date] = distance_so_far

    total_distance = distance_so_far
    if total_distance <= 0:
        raise RuntimeError("Fish flight path has no measurable length.")

    key_points = ["0"]
    key_times = ["0"]
    arrivals: dict[dt.date, tuple[float, float]] = {}
    elapsed = 0.0
    previous_distance = 0.0

    for _column, _row, date in route:
        stop_distance = stop_distances[date]
        travel_distance = max(0.0, stop_distance - previous_distance)
        travel_time = max(0.34, travel_distance / FISH_SPEED)
        elapsed += travel_time
        arrival = elapsed
        fraction = stop_distance / total_distance

        key_points.append(f"{fraction:.9f}")
        key_times.append(f"{arrival:.9f}")

        eaten = arrival + EAT_DWELL
        key_points.append(f"{fraction:.9f}")
        key_times.append(f"{eaten:.9f}")
        arrivals[date] = (arrival, eaten)

        elapsed = eaten
        previous_distance = stop_distance

    return_distance = max(0.0, total_distance - previous_distance)
    returned_at = elapsed + max(0.55, return_distance / FISH_SPEED)
    key_points.append("1")
    key_times.append(f"{returned_at:.9f}")

    reset_start = returned_at + RESET_HOLD
    cycle = reset_start + RESET_FADE
    key_points.extend(["1", "1"])
    key_times.extend([f"{reset_start:.9f}", f"{cycle:.9f}"])

    normalized_times = [float(value) / cycle for value in key_times]
    if any(b <= a for a, b in zip(normalized_times, normalized_times[1:])):
        raise RuntimeError("Fish animation key times must increase strictly.")
    if any(b < a for a, b in zip(map(float, key_points), map(float, key_points[1:]))):
        raise RuntimeError("Fish animation key points must be monotonic.")
    if abs(normalized_times[-1] - 1.0) > 1e-8:
        raise RuntimeError("Fish animation timeline does not end at one.")

    key_splines = ";".join(
        "0.42 0 0.58 1" for _ in range(len(key_points) - 1)
    )
    path = " ".join(commands)

    for first, second in zip(points, points[1:]):
        if abs(first[0] - second[0]) > 1e-7 and abs(first[1] - second[1]) > 1e-7:
            raise RuntimeError("Generated fish flight path contains a diagonal segment.")

    return {
        "points": points,
        "path": path,
        "key_points": ";".join(key_points),
        "key_times": ";".join(f"{value:.9f}" for value in normalized_times),
        "key_splines": key_splines,
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
    """Render a wide, layered abyssal reef with a feeding fish and resting octopus."""
    weeks = calendar_weeks(start, end)
    route = nearest_food_route(weeks, start, end, days)
    timeline = flight_timeline(route)
    cycle = timeline["cycle"]

    pieces: list[str] = []
    pieces.append(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="430"
 viewBox="0 0 1120 430" role="img" aria-labelledby="tank-title tank-desc">
<title id="tank-title">{html.escape(LOGIN)} contribution reef</title>
<desc id="tank-desc">{total:,} GitHub contributions from {start.isoformat()} to {end.isoformat()}. A white reef fish follows a rounded, eased route to each active contribution organism. A color shifting octopus rests on the sandy seabed beneath a living reef with drifting plankton, jellyfish, kelp and underwater light.</desc>
<defs>
  <linearGradient id="water" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#041A27"/><stop offset=".34" stop-color="#062A38"/>
    <stop offset=".72" stop-color="#082332"/><stop offset="1" stop-color="#04131E"/>
  </linearGradient>
  <radialGradient id="water-light" cx="50%" cy="22%" r="80%">
    <stop stop-color="#2A9EAA" stop-opacity=".19"/>
    <stop offset=".48" stop-color="#176C7A" stop-opacity=".07"/>
    <stop offset="1" stop-color="#02101A" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="surface-light" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#B8F9F1" stop-opacity=".25"/>
    <stop offset="1" stop-color="#55C8C0" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="glass-edge" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#A7F1E5" stop-opacity=".8"/>
    <stop offset=".46" stop-color="#317D8A" stop-opacity=".38"/>
    <stop offset="1" stop-color="#D7F5E9" stop-opacity=".74"/>
  </linearGradient>
  <linearGradient id="sand" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#315B5B"/><stop offset=".42" stop-color="#244449"/>
    <stop offset="1" stop-color="#102A32"/>
  </linearGradient>
  <linearGradient id="fish-body" x1=".12" y1="0" x2=".82" y2="1">
    <stop stop-color="#FFFFFF"/><stop offset=".3" stop-color="#E8F8F3"/>
    <stop offset=".72" stop-color="#9ED5DB"/><stop offset="1" stop-color="#438BA5"/>
  </linearGradient>
  <linearGradient id="fish-fin" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#F6FFFF"/><stop offset=".42" stop-color="#70C9D1"/>
    <stop offset="1" stop-color="#286A88"/>
  </linearGradient>
  <linearGradient id="fish-belly" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#FFFFFF" stop-opacity=".08"/>
    <stop offset="1" stop-color="#FFFFFF" stop-opacity=".76"/>
  </linearGradient>
  <linearGradient id="reef-blue" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#BCEBE9"/><stop offset=".45" stop-color="#3F9BAE"/>
    <stop offset="1" stop-color="#24627D"/>
  </linearGradient>
  <linearGradient id="octopus-skin" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#E0A184">
      <animate attributeName="stop-color" values="#D99B80;#64B9A6;#8298D6;#C38BAA;#D99B80" dur="38s" repeatCount="indefinite"/>
    </stop>
    <stop offset=".48" stop-color="#A85F68">
      <animate attributeName="stop-color" values="#A85F68;#347F83;#526EAD;#985D85;#A85F68" dur="38s" repeatCount="indefinite"/>
    </stop>
    <stop offset="1" stop-color="#70475A">
      <animate attributeName="stop-color" values="#70475A;#25565E;#394C83;#69445F;#70475A" dur="38s" repeatCount="indefinite"/>
    </stop>
  </linearGradient>
  <linearGradient id="octopus-arm" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#E4AA8D">
      <animate attributeName="stop-color" values="#E4AA8D;#76C9B3;#9AACE7;#D29DBA;#E4AA8D" dur="38s" repeatCount="indefinite"/>
    </stop>
    <stop offset="1" stop-color="#744C62">
      <animate attributeName="stop-color" values="#744C62;#2D6C69;#46558E;#774A72;#744C62" dur="38s" repeatCount="indefinite"/>
    </stop>
  </linearGradient>
  <linearGradient id="octopus-highlight" x1="0" y1="0" x2="1" y2="0">
    <stop stop-color="#FFE2C9" stop-opacity=".75"/>
    <stop offset="1" stop-color="#A2E9DB" stop-opacity=".5"/>
  </linearGradient>
  <radialGradient id="octopus-blush">
    <stop stop-color="#F8C7A9" stop-opacity=".65"/>
    <stop offset="1" stop-color="#F8C7A9" stop-opacity="0"/>
  </radialGradient>
  <filter id="fish-glow" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur stdDeviation="2.3" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="food-glow" x="-120%" y="-120%" width="340%" height="340%">
    <feGaussianBlur stdDeviation="1.1" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="soft-glow" x="-150%" y="-150%" width="400%" height="400%">
    <feGaussianBlur stdDeviation="4" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="tank-clip"><rect x="1" y="1" width="1118" height="428" rx="19"/></clipPath>
</defs>

<g clip-path="url(#tank-clip)">
  <rect width="1120" height="430" fill="url(#water)"/>
  <rect width="1120" height="430" fill="url(#water-light)"/>

  <!-- Slow surface glow and moving shafts of water light. -->
  <path d="M0 75 Q170 58 340 72 T680 70 T1120 71 L1120 95 Q920 88 740 92 T360 91 T0 94Z"
        fill="url(#surface-light)" opacity=".64"/>
  <path d="M18 79 Q200 65 380 77 T760 75 T1100 78"
        fill="none" stroke="#A9F4EB" stroke-opacity=".32" stroke-width="1.1"/>
  <path d="M18 84 Q200 70 380 82 T760 80 T1100 83"
        fill="none" stroke="#FFFFFF" stroke-opacity=".08" stroke-width=".8"/>
  <g opacity=".18">
    <path d="M125 75 L205 75 L295 314 L245 314Z" fill="#8DE5D9" opacity=".11">
      <animateTransform attributeName="transform" type="translate" values="-28 0;28 0;-28 0" dur="27s" repeatCount="indefinite"/>
    </path>
    <path d="M470 75 L545 75 L627 303 L572 303Z" fill="#B3F0E7" opacity=".13">
      <animateTransform attributeName="transform" type="translate" values="22 0;-22 0;22 0" dur="31s" repeatCount="indefinite"/>
    </path>
    <path d="M865 75 L930 75 L1010 306 L965 306Z" fill="#74C8D0" opacity=".1">
      <animateTransform attributeName="transform" type="translate" values="-18 0;18 0;-18 0" dur="24s" repeatCount="indefinite"/>
    </path>
  </g>
  <g fill="none" stroke="#A3F0E5" stroke-opacity=".075" stroke-width="1.2">
    <path d="M-20 102 Q140 87 290 101 T590 100 T900 99 T1140 100">
      <animateTransform attributeName="transform" type="translate" values="-8 0;8 0;-8 0" dur="19s" repeatCount="indefinite"/>
    </path>
    <path d="M-20 108 Q140 93 290 107 T590 106 T900 105 T1140 106">
      <animateTransform attributeName="transform" type="translate" values="9 0;-9 0;9 0" dur="23s" repeatCount="indefinite"/>
    </path>
  </g>

  <!-- Distant fish schools create movement behind the calendar. -->
  <g fill="#9ADCD8" opacity=".24">
    <g transform="translate(175 99)">
      <path d="M-12 0 Q-5 -5 3 0 Q-5 5 -12 0Z M3 0L9 -5L8 0L9 5Z"/>
      <path d="M10 7 Q16 3 23 7 Q16 11 10 7Z M23 7L28 3L27 7L28 11Z" opacity=".7"/>
      <path d="M-28 10 Q-22 6 -15 10 Q-22 14 -28 10Z M-15 10L-10 6L-11 10L-10 14Z" opacity=".6"/>
      <animateTransform attributeName="transform" type="translate" values="0 0;80 -4;0 0" dur="29s" repeatCount="indefinite"/>
    </g>
    <g transform="translate(810 96)">
      <path d="M-10 0 Q-3 -5 5 0 Q-3 5 -10 0Z M5 0L11 -5L10 0L11 5Z"/>
      <path d="M12 -8 Q19 -12 26 -8 Q19 -4 12 -8Z M26 -8L32 -12L31 -8L32 -4Z" opacity=".7"/>
      <animateTransform attributeName="transform" type="translate" values="0 0;-66 5;0 0" dur="33s" repeatCount="indefinite"/>
    </g>
  </g>

  <!-- Soft plankton field. Particles move independently at different depths. -->
  <g fill="#B9F6E9">
    <circle cx="64" cy="119" r="1.3" opacity=".42"><animate attributeName="cy" values="119;111;119" dur="7s" repeatCount="indefinite"/></circle>
    <circle cx="1035" cy="119" r="1.5" opacity=".34"><animate attributeName="cy" values="119;106;119" dur="8s" begin="1s" repeatCount="indefinite"/></circle>
    <circle cx="104" cy="292" r="1.1" opacity=".48"><animate attributeName="cy" values="292;278;292" dur="9s" repeatCount="indefinite"/></circle>
    <circle cx="1000" cy="294" r="1.4" opacity=".38"><animate attributeName="cy" values="294;280;294" dur="8.5s" begin="2s" repeatCount="indefinite"/></circle>
    <circle cx="333" cy="309" r=".9" opacity=".35"><animate attributeName="cy" values="309;299;309" dur="7.5s" begin="1.6s" repeatCount="indefinite"/></circle>
    <circle cx="755" cy="307" r="1" opacity=".38"><animate attributeName="cy" values="307;295;307" dur="8.2s" begin=".8s" repeatCount="indefinite"/></circle>
  </g>

  <!-- Header is intentionally quiet and spacious. -->
  <text x="39" y="52" font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="29" letter-spacing="1.7" fill="#EAF8F3">CONTRIBUTIONS</text>
  <text x="40" y="68" font-family="Trebuchet MS, Arial, sans-serif"
        font-size="8" font-weight="700" letter-spacing="1.8"
        fill="#75AAA9">A YEAR OF BUILDING BELOW THE SURFACE</text>
  <text x="1080" y="51" text-anchor="end"
        font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="35" letter-spacing=".9" fill="#9CEBE0"
        filter="url(#soft-glow)">{total:,}</text>
  <text x="1080" y="65" text-anchor="end"
        font-family="Trebuchet MS, Arial, sans-serif"
        font-size="8" letter-spacing="1.5" fill="#83B8B5">365 DAY REEF</text>

  <g font-family="Trebuchet MS, Arial, sans-serif" font-size="9"
     font-weight="700" letter-spacing=".85" fill="#8AC2BE">'''
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
        pieces.append(f'<text x="{GRID_X + column * PITCH}" y="113">{label}</text>')

    for row, label in enumerate(("S", "M", "T", "W", "T", "F", "S")):
        pieces.append(
            f'<text x="48" y="{GRID_Y + row * ROW_PITCH + 8}" text-anchor="middle">{label}</text>'
        )
    pieces.append("</g>")

    # Each contribution is represented by a tiny resident of the reef.
    for column, week in enumerate(weeks):
        for row, date in enumerate(week):
            if not start <= date <= end:
                continue

            detail = days[date]
            count = int(detail["count"])
            level = str(detail["level"])
            fill, stroke, organism = LEVEL_COLORS[level]
            x, y = cell_center(column, row)
            animation = ""

            if count > 0:
                arrival, eaten = timeline["arrivals"][date]
                fade_start = (arrival + EAT_DWELL * .14) / cycle
                fade_end = (arrival + EAT_DWELL * .58) / cycle
                animation = (
                    f'<animate attributeName="opacity" values="1;1;0;0;1" '
                    f'keyTimes="0;{fade_start:.8f};{fade_end:.8f};'
                    f'{timeline["reset_start_fraction"]:.8f};1" '
                    f'dur="{cycle:.6f}s" repeatCount="indefinite"/>'
                )

            opacity = "1" if count else ".48"
            glow = 'filter="url(#food-glow)"' if count else ""
            if level == "NONE":
                artwork = f'''
 <path d="M-3.8 0 Q-1.2 -3.1 2.8 -1.1 Q4 0 2.8 1.1 Q-1.2 3.1 -3.8 0Z"
       fill="{fill}" stroke="{stroke}" stroke-width=".55"/>
 <ellipse cx="-.2" cy="0" rx="2.6" ry="1.25" fill="{fill}" opacity=".75"/>
 <circle cx="1.7" cy="-.35" r=".35" fill="#D6F3E9"/>'''
            elif level == "FIRST_QUARTILE":
                artwork = f'''
 <path d="M-3.2 0 L-5.3 -2.2 Q-6.1 0 -5.3 2.2Z"
       fill="{stroke}" stroke="{stroke}" stroke-width=".4"/>
 <ellipse cx="-.2" cy="0" rx="4.3" ry="2.65"
          fill="{fill}" stroke="{stroke}" stroke-width=".65"/>
 <path d="M-2 -1.3 Q0 -2.1 2 -1.1" fill="none"
       stroke="#F3FFFA" stroke-opacity=".82" stroke-width=".55"/>
 <circle cx="2.75" cy="-.65" r=".48" fill="#08202A"/>
 <circle cx="2.9" cy="-.78" r=".17" fill="#FFFFFF"/>'''
            elif level == "SECOND_QUARTILE":
                artwork = f'''
 <path d="M-4.4 -.2 Q-2.2 -3 1.1 -2.4 Q4 -1.4 3.2 .9
       Q1.3 3.1 -2.2 1.5Z" fill="{fill}" stroke="{stroke}" stroke-width=".65"/>
 <ellipse cx="-.2" cy="0" rx="3.2" ry="2" fill="{fill}" opacity=".6"/>
 <path d="M-2 -1.1 Q-4 -3.2 -5 -3.4 M1 -1.5 Q3 -3.4 4 -3.4
       M-3 1 Q-4.5 2.8 -5 3.2" fill="none" stroke="{stroke}" stroke-width=".55"/>
 <circle cx="2.4" cy="-.65" r=".38" fill="#09232C"/>'''
            elif level == "THIRD_QUARTILE":
                artwork = f'''
 <path d="M-4.1 0 C-4.3 -3.1 -1.5 -4.2 1 -3.6
       C3.7 -3.1 4.3 -.9 3.5 .3Z"
       fill="{fill}" stroke="{stroke}" stroke-width=".65"/>
 <ellipse cx="-.2" cy="-.8" rx="3.2" ry="2.5" fill="{fill}" opacity=".35"/>
 <path d="M-3.1 .5 Q-4.3 2 -3 3.7 M-.8 .8 Q-1.7 2.6 -.4 4
       M1.2 .5 Q2.2 2.5 2 3.8 M3 .3 Q4.2 2.2 4.4 3.3"
       fill="none" stroke="{stroke}" stroke-width=".75" stroke-linecap="round"/>'''
            else:
                artwork = f'''
 <path d="M0 -4.6 C1.1 -2.5 3.7 -2.8 4.4 -1.2
       C2.6 -.1 3.8 2.3 2.1 3.6 C.8 2.1 -1.3 4.1 -2.4 2.5
       C-1.7 .6 -4.1 -.1 -4.2 -1.8 C-2.1 -1.7 -1.5 -4.1 0 -4.6Z"
       fill="{fill}" stroke="{stroke}" stroke-width=".7"/>
 <ellipse cx="-.1" cy="-.1" rx="1.3" ry="1.3" fill="#FFF4D7"/>
 <circle cx="0" cy="-.1" r=".55" fill="#A86A42"/>'''

            pieces.append(
                f'''<g transform="translate({x:.2f} {y:.2f})"
 data-date="{date.isoformat()}" data-count="{count}" data-level="{level}"
 data-organism="{organism}" opacity="{opacity}" {glow}>
 <title>{date.isoformat()} · {count} contribution{"s" if count != 1 else ""} · {organism}</title>
 {artwork}
 {animation}
</g>'''
            )

    # The main fish follows a rounded path. Spline easing reduces abrupt starts and stops.
    fish_motion = (
        f'<animateMotion path="{timeline["path"]}" '
        f'keyPoints="{timeline["key_points"]}" keyTimes="{timeline["key_times"]}" '
        f'keySplines="{timeline["key_splines"]}" calcMode="spline" '
        f'dur="{cycle:.6f}s" rotate="auto" repeatCount="indefinite"/>'
    )

    jaw_values = ["M17 1 Q21 2.2 24 1"]
    jaw_times = ["0"]
    for arrival, eaten in timeline["arrivals"].values():
        jaw_times.extend([
            f"{(arrival + EAT_DWELL * .14) / cycle:.8f}",
            f"{(arrival + EAT_DWELL * .58) / cycle:.8f}",
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
        f'''<g id="reef-feeding-fish" filter="url(#fish-glow)">
  {fish_motion}
  <g transform="translate(-16.3 0) scale(.68)">
    <!-- Flexible tail with a gentle side to side beat. -->
    <g transform="translate(-15 0)">
      <animateTransform attributeName="transform" type="rotate"
        values="-12 0 0;12 0 0;-12 0 0" dur=".62s"
        calcMode="spline" keySplines=".42 0 .58 1;.42 0 .58 1"
        repeatCount="indefinite"/>
      <path d="M0 0 C-7 -7 -17 -13 -21 -8 C-18 -3 -17 -1 -22 0
        C-17 1 -18 3 -21 8 C-17 13 -7 7 0 0Z"
        fill="url(#fish-fin)" stroke="#D8F8F0" stroke-width=".9"/>
      <path d="M-2 0L-17 -7 M-2 0L-17 7" stroke="#FFFFFF"
        stroke-opacity=".55" stroke-width=".7" fill="none"/>
    </g>

    <path d="M-7 -9 Q-4 -18 5 -16 Q11 -14 12 -9Z"
      fill="url(#fish-fin)" stroke="#DDF7F2" stroke-width=".8"/>
    <path d="M-6 10 Q0 18 8 14 L10 9Z"
      fill="url(#fish-fin)" stroke="#DDF7F2" stroke-width=".7"/>

    <path d="M-17 0 C-14 -11 -2 -15 10 -11
      C18 -8 22 -3 24 0 C22 3 18 8 10 11
      C-2 15 -14 11 -17 0Z"
      fill="url(#fish-body)" stroke="#F1FFFA" stroke-width="1.15"/>
    <path d="M-8 5 C-1 12 11 10 19 3 C11 12 -2 15 -10 8Z"
      fill="url(#fish-belly)" opacity=".82"/>
    <path d="M-8 -5 Q1 -10 12 -6" fill="none"
      stroke="#FFFFFF" stroke-opacity=".88" stroke-width="1.15" stroke-linecap="round"/>
    <path d="M-2 -5 Q1 -2 -1 1 M4 -6 Q7 -3 5 0"
      fill="none" stroke="#367C83" stroke-opacity=".6" stroke-width=".8"/>

    <g transform="translate(0 3)">
      <animateTransform attributeName="transform" type="rotate"
        values="-7 0 0;13 0 0;-7 0 0" dur=".78s"
        calcMode="spline" keySplines=".42 0 .58 1;.42 0 .58 1"
        repeatCount="indefinite"/>
      <path d="M0 0 Q-5 6 1 11 Q8 7 6 1Z"
        fill="url(#fish-fin)" stroke="#FFFFFF" stroke-opacity=".75" stroke-width=".7"/>
    </g>
    <path d="M7 -7 Q3 0 7 7" fill="none"
      stroke="#347C83" stroke-width="1.1" stroke-linecap="round"/>

    <ellipse cx="14" cy="-4.4" rx="2.3" ry="2.75" fill="#071B29"/>
    <circle cx="14.6" cy="-5.3" r=".9" fill="#FFFFFF"/>
    <circle cx="13.3" cy="-3.4" r=".45" fill="#74E4D6"/>
    <path d="M17 1 Q21 2.2 24 1" fill="none"
      stroke="#135B6A" stroke-width="1.5" stroke-linecap="round">
      {jaw_markup}
    </path>
    <path d="M18 2L22 2.5" stroke="#FFFFFF" stroke-width=".55" opacity=".9"/>
  </g>
</g>

<!-- The foreground seabed is layered behind the octopus and coral. -->
<path d="M0 356 C125 342 225 355 344 350 C466 344 570 359 702 350
 C850 339 970 356 1120 346 L1120 430 L0 430Z"
      fill="url(#sand)" opacity=".92"/>
<path d="M0 356 C125 342 225 355 344 350 C466 344 570 359 702 350
 C850 339 970 356 1120 346"
      fill="none" stroke="#7BB8A8" stroke-opacity=".28" stroke-width="1.1"/>
<g fill="#9BC9B4" opacity=".23">
  <ellipse cx="105" cy="372" rx="17" ry="3"/>
  <ellipse cx="278" cy="379" rx="24" ry="3"/>
  <ellipse cx="810" cy="375" rx="19" ry="3"/>
  <ellipse cx="1020" cy="369" rx="26" ry="3"/>
</g>
<g fill="#B9D4C5" opacity=".35">
  <circle cx="145" cy="364" r="1.1"/><circle cx="169" cy="379" r=".8"/>
  <circle cx="313" cy="365" r="1.2"/><circle cx="751" cy="375" r="1"/>
  <circle cx="1045" cy="385" r="1.3"/><circle cx="1080" cy="367" r=".8"/>
</g>

<!-- Kelp and coral frame the lower corners without intruding into the calendar. -->
<g fill="none" stroke-linecap="round">
  <g stroke="#3B9B83" stroke-width="3" opacity=".56">
    <path d="M24 358 C30 330 16 317 27 292 C39 318 31 337 39 358">
      <animate attributeName="d" values="M24 358 C30 330 16 317 27 292 C39 318 31 337 39 358;M24 358 C35 330 25 314 34 292 C43 319 28 338 39 358;M24 358 C30 330 16 317 27 292 C39 318 31 337 39 358" dur="8s" repeatCount="indefinite"/>
    </path>
    <path d="M1085 357 C1077 331 1098 316 1088 291 C1074 316 1082 337 1072 359">
      <animate attributeName="d" values="M1085 357 C1077 331 1098 316 1088 291 C1074 316 1082 337 1072 359;M1085 357 C1073 331 1089 313 1080 291 C1069 317 1088 338 1072 359;M1085 357 C1077 331 1098 316 1088 291 C1074 316 1082 337 1072 359" dur="9s" repeatCount="indefinite"/>
    </path>
  </g>
  <g stroke="#BE8F7A" stroke-width="2" opacity=".45">
    <path d="M57 358 Q47 337 56 322 M56 342 Q41 332 43 320 M57 349 Q74 337 76 325"/>
    <path d="M1052 357 Q1065 337 1058 321 M1058 344 Q1075 333 1076 321"/>
  </g>
</g>

<!-- A drifting jellyfish in the lower water column. -->
<g id="reef-jellyfish" transform="translate(150 335)" opacity=".68">
  <animateTransform attributeName="transform" type="translate"
    values="150 335;158 323;151 335" dur="14s" repeatCount="indefinite"/>
  <path d="M-12 1 C-13 -14 -5 -22 0 -22 C7 -22 13 -13 12 1Z"
        fill="#68CFC3" fill-opacity=".24" stroke="#B8F5E9" stroke-opacity=".7" stroke-width=".9"/>
  <path d="M-10 1 Q0 7 10 1" fill="none" stroke="#B8F5E9" stroke-width="1"/>
  <g fill="none" stroke="#A7EBDD" stroke-width="1" stroke-linecap="round">
    <path d="M-8 3 Q-13 13 -9 21"><animate attributeName="d" values="M-8 3 Q-13 13 -9 21;M-8 3 Q-4 13 -9 21;M-8 3 Q-13 13 -9 21" dur="3.2s" repeatCount="indefinite"/></path>
    <path d="M-2 4 Q-5 15 -1 24"><animate attributeName="d" values="M-2 4 Q-5 15 -1 24;M-2 4 Q2 15 -1 24;M-2 4 Q-5 15 -1 24" dur="3.8s" repeatCount="indefinite"/></path>
    <path d="M5 4 Q10 13 7 22"><animate attributeName="d" values="M5 4 Q10 13 7 22;M5 4 Q1 13 7 22;M5 4 Q10 13 7 22" dur="3.4s" repeatCount="indefinite"/></path>
  </g>
</g>

<!-- Eight relaxed arms, soft suckers, and a color-shifting mantle. -->
<g id="resting-octopus" transform="translate(560 340)">
  <ellipse cx="0" cy="31" rx="154" ry="8" fill="#071923" opacity=".36"/>
'''
    )

    octopus_arms = [
        ("-20 3", "M0 0 C-25 -13 -49 -8 -72 2 S-112 10 -145 21", "-15 -5 -28 -7 -41 -5 -55 -1 -71 3 -87 5 -104 9 -123 15", "-1.8 0 0;1.2 0 0;-1.8 0 0", "7.6s", "0s"),
        ("-17 8", "M0 0 C-28 -2 -51 11 -73 18 S-116 20 -143 35", "-16 3 -31 7 -46 12 -62 16 -80 18 -99 20 -123 29", "1.4 0 0;-1.1 0 0;1.4 0 0", "8.2s", ".6s"),
        ("-10 13", "M0 0 C-24 13 -38 28 -65 31 S-112 38 -128 54", "-12 8 -27 18 -42 26 -58 30 -76 34 -96 41 -113 49", "-1.2 0 0;1.5 0 0;-1.2 0 0", "9.1s", "1s"),
        ("-2 15", "M0 0 C-13 20 -28 39 -51 43 S-93 53 -108 68", "-8 12 -20 28 -33 38 -49 43 -67 51 -88 61", "1.3 0 0;-1.1 0 0;1.3 0 0", "8.7s", "1.4s"),
        ("10 12", "M0 0 C22 12 39 29 65 31 S112 39 130 54", "11 9 26 18 42 27 57 31 76 36 96 43 116 50", "-1.4 0 0;1.1 0 0;-1.4 0 0", "8.4s", ".4s"),
        ("17 7", "M0 0 C27 0 52 11 74 16 S117 19 146 34", "16 2 31 6 47 11 64 15 84 18 104 23 129 30", "1.3 0 0;-1.2 0 0;1.3 0 0", "9.3s", "1.2s"),
        ("20 2", "M0 0 C29 -12 50 -8 76 0 S119 9 147 18", "16 -5 33 -7 49 -5 66 -1 87 3 109 9 130 14", "-1.2 0 0;1.5 0 0;-1.2 0 0", "8.9s", ".8s"),
        ("3 17", "M0 0 C17 22 33 39 61 45 S105 55 120 71", "7 12 22 28 39 39 56 44 76 51 97 62 112 68", "1.1 0 0;-1.4 0 0;1.1 0 0", "9.6s", "1.7s"),
    ]
    for translate, arm_path, sucker_coords, rotation, duration, begin in octopus_arms:
        pieces.append(
            f'''  <g transform="translate({translate})" data-octopus-arm="true">
    <animateTransform attributeName="transform" type="rotate" additive="sum"
      values="{rotation}" dur="{duration}" begin="{begin}" repeatCount="indefinite"/>
    <path d="{arm_path}" fill="none" stroke="#1A2733" stroke-opacity=".48"
      stroke-width="11" stroke-linecap="round"/>
    <path d="{arm_path}" fill="none" stroke="url(#octopus-arm)"
      stroke-width="8.2" stroke-linecap="round"/>
    <path d="{arm_path}" fill="none" stroke="url(#octopus-highlight)"
      stroke-width="1.15" stroke-linecap="round" opacity=".46"/>
'''
        )
        coordinates = sucker_coords.split()
        for index in range(0, len(coordinates), 2):
            sx, sy = coordinates[index], coordinates[index + 1]
            pieces.append(
                f'    <circle cx="{sx}" cy="{sy}" r="1.35" fill="#F0D5C3" fill-opacity=".68" stroke="#664D5A" stroke-opacity=".38" stroke-width=".4"/>\n'
            )
        pieces.append("  </g>\n")

    pieces.append(
        '''  <g id="octopus-mantle">
    <animateTransform attributeName="transform" type="scale"
      values="1 1;1.025 .975;1 1" dur="5.8s" repeatCount="indefinite"/>
    <path d="M-30 17 C-43 2 -37 -23 -20 -34 C-7 -43 12 -42 26 -29
      C42 -14 38 10 25 21 C10 31 -17 31 -30 17Z"
      fill="url(#octopus-skin)" stroke="#F0C4AB" stroke-opacity=".55" stroke-width="1.2"/>
    <path d="M-24 8 C-24 -15 -10 -30 4 -31 C20 -30 30 -12 26 6
      C16 17 -9 22 -24 8Z" fill="url(#octopus-blush)" opacity=".6"/>
    <g fill="#F4C2A6" opacity=".46">
      <circle cx="-18" cy="-13" r="2.1"><animate attributeName="opacity" values=".25;.65;.25" dur="8s" repeatCount="indefinite"/></circle>
      <circle cx="-8" cy="-25" r="1.5"><animate attributeName="opacity" values=".6;.22;.6" dur="7s" repeatCount="indefinite"/></circle>
      <circle cx="10" cy="-21" r="2"><animate attributeName="opacity" values=".25;.7;.25" dur="9s" repeatCount="indefinite"/></circle>
      <circle cx="22" cy="-8" r="1.4"><animate attributeName="opacity" values=".65;.2;.65" dur="7.8s" repeatCount="indefinite"/></circle>
      <circle cx="14" cy="12" r="1.7"><animate attributeName="opacity" values=".2;.6;.2" dur="8.5s" repeatCount="indefinite"/></circle>
    </g>
    <path d="M-17 -23 Q-4 -36 14 -27" fill="none"
      stroke="#FFF0DF" stroke-opacity=".42" stroke-width="2.2" stroke-linecap="round"/>
    <ellipse cx="-10" cy="7" rx="4.1" ry="5.1" fill="#F7D5BD" opacity=".82"/>
    <ellipse cx="10" cy="7" rx="4.1" ry="5.1" fill="#F7D5BD" opacity=".82"/>
    <ellipse cx="-9.5" cy="7" rx="2.2" ry="3.4" fill="#172A37"/>
    <ellipse cx="10.5" cy="7" rx="2.2" ry="3.4" fill="#172A37"/>
    <circle cx="-10.1" cy="5.6" r=".8" fill="#FFFFFF"/>
    <circle cx="9.9" cy="5.6" r=".8" fill="#FFFFFF"/>
    <path d="M-3 17 Q0 20 3 17" fill="none" stroke="#704B5B"
      stroke-width="1" stroke-linecap="round" opacity=".6"/>
  </g>
</g>

<!-- A few bubbles rise from the octopus den and nearby reef crevices. -->
<g fill="none" stroke="#BDEEE2" stroke-opacity=".58">
  <circle cx="397" cy="339" r="2.1"><animate attributeName="cy" values="339;302;339" dur="7.5s" repeatCount="indefinite"/></circle>
  <circle cx="405" cy="354" r="1.2"><animate attributeName="cy" values="354;328;354" dur="8.4s" begin="1.4s" repeatCount="indefinite"/></circle>
  <circle cx="733" cy="337" r="1.8"><animate attributeName="cy" values="337;306;337" dur="7.1s" begin=".8s" repeatCount="indefinite"/></circle>
  <circle cx="744" cy="350" r="1.1"><animate attributeName="cy" values="350;325;350" dur="8.1s" begin="2s" repeatCount="indefinite"/></circle>
</g>

<!-- The footer is a clear information strip, separate from the seabed. -->
<path d="M24 389 Q250 382 450 388 T850 387 T1096 389"
      fill="none" stroke="#83C9BE" stroke-opacity=".2"/>
<text x="39" y="411" font-family="Trebuchet MS, Arial, sans-serif"
      font-size="9" font-weight="700" letter-spacing=".9"
      fill="#9BCBC0">{start.strftime("%b %d, %Y").upper()}  /  {end.strftime("%b %d, %Y").upper()}</text>
<g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
   font-weight="700" letter-spacing=".7" fill="#91BFB8">
  <text x="822" y="411">LESS</text>'''
    )

    for index, level in enumerate(
        ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    ):
        fill, stroke, _ = LEVEL_COLORS[level]
        pieces.append(
            f'<rect x="{858 + index * 20}" y="402" width="12" height="12" rx="4" '
            f'fill="{fill}" stroke="{stroke}" stroke-width=".85"/>'
        )

    pieces.append(
        '''<text x="972" y="411">MORE</text>
</g>
</g>
<rect x=".75" y=".75" width="1118.5" height="428.5" rx="19"
      fill="none" stroke="url(#glass-edge)" stroke-width="1.35"/>
<rect x="5" y="5" width="1110" height="420" rx="15"
      fill="none" stroke="#E2FFF5" stroke-opacity=".055"/>
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
        f"reef fish route eats {active_days} active contribution organisms, "
        f"with the color-shifting octopus resting on the seabed."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution aquarium update failed: {error}", file=sys.stderr)
        raise
