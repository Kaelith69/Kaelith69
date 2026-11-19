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

GRID_X = 112
GRID_Y = 190
CELL = 13
PITCH = 20
ROW_PITCH = 25
LAUNCH_POINT = (58.0, 430.0)
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
    "NONE": ("#08132B", "#1E3A5F", "abyss"),
    "FIRST_QUARTILE": ("#1D4ED8", "#93C5FD", "electric blue"),
    "SECOND_QUARTILE": ("#00BBF9", "#E0FBF7", "bioluminescent cyan"),
    "THIRD_QUARTILE": ("#7B2CBF", "#DCCBFF", "ultraviolet"),
    "FOURTH_QUARTILE": ("#FF2A85", "#FFE5F1", "neon magenta"),
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
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="650"
 viewBox="0 0 1200 650" role="img" aria-labelledby="tank-title tank-desc">
<title id="tank-title">{html.escape(LOGIN)} contribution reef</title>
<desc id="tank-desc">{total:,} GitHub contributions from {start.isoformat()} to {end.isoformat()}. A white reef fish follows a rounded, eased route to each active contribution organism. A color shifting octopus rests on the sandy seabed beneath a living reef with drifting plankton, jellyfish, kelp and underwater light.</desc>
<defs>
  <linearGradient id="water" x1="0%" y1="0%" x2="0%" y2="100%">
    <stop offset="0%" stop-color="#030712"/><stop offset="45%" stop-color="#07132E"/>
    <stop offset="100%" stop-color="#0D1F46"/>
  </linearGradient>
  <radialGradient id="water-light" cx="48%" cy="28%" r="78%">
    <stop stop-color="#263BFF" stop-opacity=".24"/>
    <stop offset=".34" stop-color="#7028C9" stop-opacity=".12"/>
    <stop offset=".67" stop-color="#00BFEF" stop-opacity=".045"/>
    <stop offset="1" stop-color="#030411" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="surface-light" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#00E5FF" stop-opacity=".32"/>
    <stop offset=".42" stop-color="#8A5CFF" stop-opacity=".14"/>
    <stop offset="1" stop-color="#FF3BC8" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="glass-edge" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#00E5FF" stop-opacity=".92"/>
    <stop offset=".42" stop-color="#625BFF" stop-opacity=".58"/>
    <stop offset=".72" stop-color="#FF3BC8" stop-opacity=".42"/>
    <stop offset="1" stop-color="#00E5FF" stop-opacity=".86"/>
  </linearGradient>
  <linearGradient id="sand" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#26204D"/><stop offset=".42" stop-color="#171A3B"/>
    <stop offset="1" stop-color="#090C20"/>
  </linearGradient>
  <linearGradient id="fish-body" x1=".12" y1="0" x2=".82" y2="1">
    <stop stop-color="#FFFFFF"/><stop offset=".3" stop-color="#E9F5FF"/>
    <stop offset=".72" stop-color="#A6BFFF"/><stop offset="1" stop-color="#4964D8"/>
  </linearGradient>
  <linearGradient id="fish-fin" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#FFFFFF"/><stop offset=".38" stop-color="#00E5FF"/>
    <stop offset="1" stop-color="#7657FF"/>
  </linearGradient>
  <linearGradient id="fish-belly" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#FFFFFF" stop-opacity=".08"/>
    <stop offset="1" stop-color="#FFFFFF" stop-opacity=".76"/>
  </linearGradient>
  <linearGradient id="reef-blue" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#FFC4F2"/><stop offset=".42" stop-color="#A44DFF"/>
    <stop offset="1" stop-color="#263BFF"/>
  </linearGradient>
  <linearGradient id="octopus-skin" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#FF4FD8">
      <animate attributeName="stop-color" values="#FF4FD8;#9B5CFF;#00E5FF;#A45BFF;#FF4FD8" dur="38s" repeatCount="indefinite"/>
    </stop>
    <stop offset=".48" stop-color="#A13EDB">
      <animate attributeName="stop-color" values="#A13EDB;#485BFF;#00AEEF;#D235C6;#A13EDB" dur="38s" repeatCount="indefinite"/>
    </stop>
    <stop offset="1" stop-color="#39235F">
      <animate attributeName="stop-color" values="#39235F;#1B2F78;#07506F;#63206C;#39235F" dur="38s" repeatCount="indefinite"/>
    </stop>
  </linearGradient>
  <linearGradient id="octopus-arm" x1="0" y1="0" x2="0" y2="1">
    <stop stop-color="#FF8BE4">
      <animate attributeName="stop-color" values="#FF8BE4;#A98BFF;#6CF5FF;#D98BFF;#FF8BE4" dur="38s" repeatCount="indefinite"/>
    </stop>
    <stop offset="1" stop-color="#542B8F">
      <animate attributeName="stop-color" values="#542B8F;#273D9E;#075A86;#812D87;#542B8F" dur="38s" repeatCount="indefinite"/>
    </stop>
  </linearGradient>
  <linearGradient id="octopus-highlight" x1="0" y1="0" x2="1" y2="0">
    <stop stop-color="#FFFFFF" stop-opacity=".88"/>
    <stop offset=".46" stop-color="#00E5FF" stop-opacity=".82"/>
    <stop offset="1" stop-color="#FF73DF" stop-opacity=".6"/>
  </linearGradient>
  <radialGradient id="octopus-blush">
    <stop stop-color="#FF8BE4" stop-opacity=".72"/>
    <stop offset=".52" stop-color="#8B5CFF" stop-opacity=".3"/>
    <stop offset="1" stop-color="#8B5CFF" stop-opacity="0"/>
  </radialGradient>
  <!-- Comic-print textures: coarse halftone and Ben-Day dots. -->
  <pattern id="halftone-cyan" width="9" height="9" patternUnits="userSpaceOnUse">
    <circle cx="2" cy="2" r="1.35" fill="#00E5FF" fill-opacity=".72"/>
  </pattern>
  <pattern id="halftone-violet" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(18)">
    <circle cx="2" cy="2" r="1.2" fill="#A78BFF" fill-opacity=".72"/>
  </pattern>
  <pattern id="halftone-ink" width="7" height="7" patternUnits="userSpaceOnUse">
    <circle cx="2" cy="2" r="1.05" fill="#030411" fill-opacity=".78"/>
  </pattern>
  <filter id="ink-shadow" x="-40%" y="-40%" width="180%" height="180%">
    <feMorphology in="SourceAlpha" operator="dilate" radius="1.6" result="spread"/>
    <feFlood flood-color="#030411" result="ink"/>
    <feComposite in="ink" in2="spread" operator="in" result="outline"/>
    <feMerge><feMergeNode in="outline"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
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
  <clipPath id="tank-clip"><rect x="1" y="1" width="1198" height="648" rx="20"/></clipPath>
</defs>

<g clip-path="url(#tank-clip)">
  <rect width="1200" height="650" fill="url(#water)"/>
  <rect width="1200" height="650" fill="url(#water-light)"/>

  <!-- Angular comic panels and restrained halftone shadows. -->
  <path d="M0 110 L176 110 L286 430 L150 430Z" fill="#263BFF" opacity=".045"/>
  <path d="M1200 106 L1098 106 L1006 423 L1120 423Z" fill="#FF3BC8" opacity=".035"/>
  <path d="M0 340 L108 340 L140 470 L0 470Z" fill="url(#halftone-violet)" opacity=".22"/>
  <path d="M1200 336 L1090 336 L1058 470 L1200 470Z" fill="url(#halftone-cyan)" opacity=".16"/>
  <path d="M0 105 L160 105 L198 220 L0 220Z" fill="url(#halftone-ink)" opacity=".1"/>
  <path d="M1200 105 L1108 105 L1072 218 L1200 218Z" fill="url(#halftone-ink)" opacity=".09"/>

  <!-- Comic-book panel header with an offset magenta print shadow. -->
  <path d="M35 20 L311 20 L295 68 L35 68Z" fill="#FF3BC8" opacity=".78"/>
  <path d="M28 16 L305 16 L289 63 L28 63Z"
        fill="#0A0A20" stroke="#030411" stroke-width="4" stroke-linejoin="bevel"/>
  <path d="M31 19 L299 19 L284 59 L31 59Z"
        fill="none" stroke="#00E5FF" stroke-width="1.45" stroke-linejoin="bevel"/>
  <path d="M31 65 L287 65" fill="none" stroke="#8B5CFF" stroke-width="2.2"/>
  <path d="M304 22 L312 14 L319 22 L312 30Z"
        fill="#FF3BC8" stroke="#030411" stroke-width="2"/>

  <!-- Slow surface glow and moving shafts of water light. -->
  <path d="M0 84 Q170 66 340 80 T680 78 T1120 79 L1120 105 Q920 96 740 100 T360 99 T0 102Z"
        fill="url(#surface-light)" opacity=".64"/>
  <path d="M18 88 Q200 71 380 85 T760 83 T1180 86"
        fill="none" stroke="#00E5FF" stroke-opacity=".48" stroke-width="1.2"/>
  <path d="M18 94 Q200 77 380 91 T760 89 T1180 92"
        fill="none" stroke="#A78BFF" stroke-opacity=".18" stroke-width=".9"/>
  <g opacity=".18">
    <path d="M125 84 L205 84 L295 405 L245 405Z" fill="#00E5FF" opacity=".16">
      <animateTransform attributeName="transform" type="translate" values="-28 0;28 0;-28 0" dur="27s" repeatCount="indefinite"/>
    </path>
    <path d="M470 84 L545 84 L627 395 L572 395Z" fill="#8B5CFF" opacity=".17">
      <animateTransform attributeName="transform" type="translate" values="22 0;-22 0;22 0" dur="31s" repeatCount="indefinite"/>
    </path>
    <path d="M865 84 L930 84 L1010 405 L965 405Z" fill="#FF3BC8" opacity=".13">
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

  <!-- Inked motion marks and small speed streaks. -->
  <g fill="none" stroke="#030411" stroke-width="3" opacity=".58">
    <path d="M450 112 l15 -3 M461 119 l18 -2 M650 124 l13 -3"/>
    <path d="M832 116 l13 -3 M842 122 l18 -2"/>
  </g>
  <g fill="none" stroke="#00E5FF" stroke-width="1.1" opacity=".45">
    <path d="M450 110 l15 -3 M461 117 l18 -2 M650 122 l13 -3"/>
    <path d="M832 114 l13 -3 M842 120 l18 -2"/>
  </g>

  <!-- Distant fish schools create movement behind the calendar. -->
  <g fill="#A4D8FF" opacity=".3">
    <g transform="translate(175 99)">
      <path d="M-12 0 Q-5 -5 3 0 Q-5 5 -12 0Z M3 0L9 -5L8 0L9 5Z"/>
      <path d="M10 7 Q16 3 23 7 Q16 11 10 7Z M23 7L28 3L27 7L28 11Z" opacity=".7"/>
      <path d="M-28 10 Q-22 6 -15 10 Q-22 14 -28 10Z M-15 10L-10 6L-11 10L-10 14Z" opacity=".6"/>
      <animateTransform attributeName="transform" type="translate" additive="sum" values="0 0;80 -4;0 0" dur="29s" repeatCount="indefinite"/>
    </g>
    <g transform="translate(810 96)">
      <path d="M-10 0 Q-3 -5 5 0 Q-3 5 -10 0Z M5 0L11 -5L10 0L11 5Z"/>
      <path d="M12 -8 Q19 -12 26 -8 Q19 -4 12 -8Z M26 -8L32 -12L31 -8L32 -4Z" opacity=".7"/>
      <animateTransform attributeName="transform" type="translate" additive="sum" values="0 0;-66 5;0 0" dur="33s" repeatCount="indefinite"/>
    </g>
  </g>

  <!-- Soft plankton field. Particles move independently at different depths. -->
  <g fill="#9AEFFF">
    <circle cx="64" cy="119" r="1.3" opacity=".42"><animate attributeName="cy" values="119;111;119" dur="7s" repeatCount="indefinite"/></circle>
    <circle cx="1115" cy="119" r="1.5" opacity=".34"><animate attributeName="cy" values="119;106;119" dur="8s" begin="1s" repeatCount="indefinite"/></circle>
    <circle cx="104" cy="292" r="1.1" opacity=".48"><animate attributeName="cy" values="292;278;292" dur="9s" repeatCount="indefinite"/></circle>
    <circle cx="1080" cy="294" r="1.4" opacity=".38"><animate attributeName="cy" values="294;280;294" dur="8.5s" begin="2s" repeatCount="indefinite"/></circle>
    <circle cx="333" cy="309" r=".9" opacity=".35"><animate attributeName="cy" values="309;299;309" dur="7.5s" begin="1.6s" repeatCount="indefinite"/></circle>
    <circle cx="755" cy="307" r="1" opacity=".38"><animate attributeName="cy" values="307;295;307" dur="8.2s" begin=".8s" repeatCount="indefinite"/></circle>
  </g>

  <!-- Halftone-backed data badge and comic impact rays. -->
  <path d="M1104 17 L1188 17 L1188 62 L1104 62Z"
        fill="url(#halftone-cyan)" opacity=".25"/>
  <path d="M1143 19 L1152 7 L1161 18 L1174 13 L1173 28 L1185 35
           L1171 42 L1175 55 L1160 51 L1153 63 L1145 52 L1131 57
           L1134 43 L1120 36 L1133 28 L1129 15Z"
        fill="#263BFF" opacity=".3" stroke="#030411" stroke-width="2.5"
        stroke-linejoin="miter"/>

  <!-- Angular comic panel behind the live contribution matrix. -->
  <path d="M74 177 L1173 177 L1181 370 L68 370Z"
        fill="#030712" fill-opacity=".36" stroke="#030411" stroke-width="4"
        stroke-linejoin="bevel"/>
  <path d="M79 181 L1168 181 L1174 365 L74 365Z"
        fill="url(#halftone-ink)" opacity=".2"/>
  <path d="M78 182 L1168 182" fill="none" stroke="#FF2A85"
        stroke-width="1.35" stroke-opacity=".72"/>
  <path d="M75 371 L1179 371" fill="none" stroke="#00F5D4"
        stroke-width="1.8" stroke-opacity=".42"/>
  <path d="M75 182 L84 182 L75 194 M1168 182 L1177 182 L1177 194"
        fill="none" stroke="#00F5D4" stroke-width="1.5"/>
  <!-- Header keeps its comic-book display lettering. -->
  <text x="39" y="52" font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="29" letter-spacing="1.7" fill="#EAF8F3">CONTRIBUTIONS</text>
  <text x="40" y="68" font-family="Trebuchet MS, Arial, sans-serif"
        font-size="8" font-weight="700" letter-spacing="1.8"
        fill="#8F91D8">A YEAR OF BUILDING BELOW THE SURFACE</text>
  <text x="1155" y="51" text-anchor="end"
        font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="37" letter-spacing="1.1" fill="#00E5FF"
        filter="url(#soft-glow)">{total:,}</text>
  <text x="1155" y="65" text-anchor="end"
        font-family="Trebuchet MS, Arial, sans-serif"
        font-size="8.5" letter-spacing="1.7" fill="#FF69D4">365 DAY REEF</text>

  <g font-family="Trebuchet MS, Arial, sans-serif" font-size="9.5"
     font-weight="700" letter-spacing=".95" fill="#A7A8F7">'''
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
        pieces.append(f'<text x="{GRID_X + column * PITCH}" y="165">{label}</text>')

    for row, label in enumerate(("S", "M", "T", "W", "T", "F", "S")):
        pieces.append(
            f'<text x="78" y="{GRID_Y + row * ROW_PITCH + 8}" text-anchor="middle">{label}</text>'
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
 <rect x="-5.5" y="-4.5" width="11" height="9" rx="2.5"
       fill="{fill}" stroke="{stroke}" stroke-width="1.05"/>
 <path d="M-3.8 -2.8 H3.5" fill="none" stroke="#5676A8"
       stroke-opacity=".36" stroke-width=".65" stroke-linecap="round"/>'''
            elif level == "FIRST_QUARTILE":
                artwork = f'''
 <path d="M-6 -5 H4.2 L6 -3.2 V5 H-6Z"
       fill="{fill}" stroke="#030712" stroke-width="1.8" stroke-linejoin="bevel"/>
 <path d="M-3.7 -2.6 H2.8" stroke="#93C5FD" stroke-width="1.25"
       stroke-linecap="round"/>
 <path d="M-5 3.6 L-1 3.6" stroke="#030712" stroke-opacity=".38" stroke-width=".8"/>'''
            elif level == "SECOND_QUARTILE":
                artwork = f'''
 <path d="M-6.5 -5.5 H4.4 L6.5 -3.5 V5.5 H-6.5Z"
       fill="{fill}" stroke="#030712" stroke-width="1.8" stroke-linejoin="bevel"/>
 <path d="M-4 -3 H3.7" stroke="#E0FBF7" stroke-width="1.45"
       stroke-linecap="round"/>
 <path d="M-4 3.6 H1.5" stroke="#FFFFFF" stroke-opacity=".46" stroke-width=".7"/>'''
            elif level == "THIRD_QUARTILE":
                artwork = f'''
 <path d="M-6.5 -5.5 H3.7 L6.5 -2.7 V5.5 H-6.5Z"
       fill="{fill}" stroke="#030712" stroke-width="1.8" stroke-linejoin="bevel"/>
 <path d="M-3.8 -2.9 H2.9" stroke="#F8E8FF" stroke-width="1.4"
       stroke-linecap="round"/>
 <path d="M-4.5 3.7 L-1.5 3.7" stroke="#030712" stroke-opacity=".38" stroke-width=".8"/>'''
            else:
                artwork = f'''
 <path d="M-7 -4.5 L-3 -6 H4.8 L7 -3.5 V4.4 L3 6 H-4.8 L-7 3Z"
       fill="{fill}" stroke="#030712" stroke-width="2" stroke-linejoin="miter"/>
 <path d="M-4.2 -3.2 H3.2" stroke="#FFE5F1" stroke-width="1.5"
       stroke-linecap="round"/>
 <path d="M-4 3.7 H1.8" stroke="#FFBE0B" stroke-opacity=".9" stroke-width=".9"
       stroke-linecap="round"/>'''

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
      <animateTransform attributeName="transform" type="rotate" additive="sum"
        values="-12 0 0;12 0 0;-12 0 0" dur=".62s"
        calcMode="spline" keySplines=".42 0 .58 1;.42 0 .58 1"
        repeatCount="indefinite"/>
      <path d="M0 0 C-7 -7 -17 -13 -21 -8 C-18 -3 -17 -1 -22 0
        C-17 1 -18 3 -21 8 C-17 13 -7 7 0 0Z"
        fill="url(#fish-fin)" stroke="#CBF4FF" stroke-width=".9"/>
      <path d="M-2 0L-17 -7 M-2 0L-17 7" stroke="#FFFFFF"
        stroke-opacity=".55" stroke-width=".7" fill="none"/>
    </g>

    <path d="M-7 -9 Q-4 -18 5 -16 Q11 -14 12 -9Z"
      fill="url(#fish-fin)" stroke="#C8E9FF" stroke-width=".8"/>
    <path d="M-6 10 Q0 18 8 14 L10 9Z"
      fill="url(#fish-fin)" stroke="#DDF7F2" stroke-width=".7"/>

    <!-- Heavy ink contour with a cyan registration edge. -->
    <path d="M-17 0 C-14 -11 -2 -15 10 -11
      C18 -8 22 -3 24 0 C22 3 18 8 10 11
      C-2 15 -14 11 -17 0Z"
      fill="none" stroke="#030411" stroke-width="4.2" stroke-linejoin="round"/>
    <path d="M-17 0 C-14 -11 -2 -15 10 -11
      C18 -8 22 -3 24 0 C22 3 18 8 10 11
      C-2 15 -14 11 -17 0Z"
      fill="url(#fish-body)" stroke="#E9F2FF" stroke-width="1.35" stroke-linejoin="round"/>
    <path d="M-8 5 C-1 12 11 10 19 3 C11 12 -2 15 -10 8Z"
      fill="url(#fish-belly)" opacity=".82"/>
    <path d="M-8 -5 Q1 -10 12 -6" fill="none"
      stroke="#FFFFFF" stroke-opacity=".88" stroke-width="1.15" stroke-linecap="round"/>
    <path d="M-2 -5 Q1 -2 -1 1 M4 -6 Q7 -3 5 0"
      fill="none" stroke="#6278FF" stroke-opacity=".6" stroke-width=".8"/>

    <g transform="translate(0 3)">
      <animateTransform attributeName="transform" type="rotate" additive="sum"
        values="-7 0 0;13 0 0;-7 0 0" dur=".78s"
        calcMode="spline" keySplines=".42 0 .58 1;.42 0 .58 1"
        repeatCount="indefinite"/>
      <path d="M0 0 Q-5 6 1 11 Q8 7 6 1Z"
        fill="none" stroke="#030411" stroke-width="2.7" stroke-linejoin="round"/>
      <path d="M0 0 Q-5 6 1 11 Q8 7 6 1Z"
        fill="url(#fish-fin)" stroke="#FFFFFF" stroke-opacity=".88" stroke-width=".9"/>
    </g>
    <path d="M7 -7 Q3 0 7 7" fill="none"
      stroke="#526DFF" stroke-width="1.1" stroke-linecap="round"/>

    <ellipse cx="14" cy="-4.4" rx="2.3" ry="2.75" fill="#071B29"/>
    <circle cx="14.6" cy="-5.3" r=".9" fill="#FFFFFF"/>
    <circle cx="13.3" cy="-3.4" r=".45" fill="#00E5FF"/>
    <path d="M17 1 Q21 2.2 24 1" fill="none"
      stroke="#334AFF" stroke-width="1.5" stroke-linecap="round">
      {jaw_markup}
    </path>
    <path d="M18 2L22 2.5" stroke="#FFFFFF" stroke-width=".55" opacity=".9"/>
  </g>
</g>

<!-- Comic reef ridge and deep foreground shelf. -->
<path d="M8 486 Q290 467 600 486 T1192 482 L1192 650 L8 650Z"
      fill="#091633" stroke="#030712" stroke-width="4.2" stroke-linejoin="bevel"/>
<path d="M8 486 Q290 467 600 486 T1192 482"
      fill="none" stroke="#00F5D4" stroke-width="2.2" opacity=".62"/>
<path d="M8 493 Q290 476 600 493 T1192 489"
      fill="none" stroke="#FF2A85" stroke-width="1.2" opacity=".4"/>

<!-- Left faceted boulder, with comic bevel and crosshatch shadows. -->
<g id="left-rock">
  <path d="M8 492 L78 472 L168 485 L238 538 L258 610 L8 610Z"
        fill="url(#rockLit)" stroke="#030712" stroke-width="4.2" stroke-linejoin="bevel"/>
  <path d="M8 492 L78 472 L168 485 L138 518 L52 510Z"
        fill="#254685" stroke="#030712" stroke-width="2.1" opacity=".8"/>
  <path d="M168 485 L238 538 L258 610 L155 610 L138 518Z"
        fill="url(#rockShade)"/>
  <path d="M168 485 L238 538 L258 610 L155 610 L138 518Z"
        fill="url(#halftone-ink)" opacity=".76"/>
  <path d="M12 492 L78 472 L166 485" fill="none"
        stroke="#00F5D4" stroke-width="2.2" opacity=".8"/>
  <path d="M22 526 L114 546 M35 548 L128 570"
        fill="none" stroke="#8B5CFF" stroke-width="1.2" opacity=".5"/>
</g>

<!-- Right faceted boulder, lit by the magenta side of the reef. -->
<g id="right-rock">
  <path d="M1192 492 L1122 472 L1032 485 L962 538 L942 610 L1192 610Z"
        fill="url(#rockLit)" stroke="#030712" stroke-width="4.2" stroke-linejoin="bevel"/>
  <path d="M1192 492 L1122 472 L1032 485 L1062 518 L1148 510Z"
        fill="#254685" stroke="#030712" stroke-width="2.1" opacity=".8"/>
  <path d="M1032 485 L962 538 L942 610 L1045 610 L1062 518Z"
        fill="url(#rockShade)"/>
  <path d="M1032 485 L962 538 L942 610 L1045 610 L1062 518Z"
        fill="url(#halftone-ink)" opacity=".76"/>
  <path d="M1188 492 L1122 472 L1034 485" fill="none"
        stroke="#FF2A85" stroke-width="2.2" opacity=".88"/>
  <path d="M1090 548 L1000 570 M1078 526 L984 548"
        fill="none" stroke="#8B5CFF" stroke-width="1.2" opacity=".5"/>
</g>

<!-- Left reef cluster: ribbon kelp, branching staghorn coral and tube anemones. -->
<g transform="translate(42 495)">
  <path d="M0 0 C-9 -42 -6 -88 3 -125 C11 -88 12 -42 6 0Z"
        fill="#00F5D4" stroke="#030712" stroke-width="3" stroke-linejoin="round"/>
  <path d="M2 -14 C-2 -48 0 -80 3 -112"
        fill="none" stroke="#030712" stroke-width="1.8" stroke-linecap="round"/>
  <path d="M28 8 L28 -30 L11 -54 L17 -58 L32 -38 L36 -68 L44 -68
           L40 -34 L58 -52 L63 -47 L41 -22 L41 8Z"
        fill="#FF2A85" stroke="#030712" stroke-width="3"
        stroke-linejoin="bevel" filter="url(#soft-glow)"/>
  <path d="M28 -30 L32 -38 L36 -68 M40 -34 L58 -52"
        fill="none" stroke="#FFE5F1" stroke-width="1.3" opacity=".75"/>
  <g stroke="#030712" stroke-width="2.5" stroke-linejoin="round">
    <path d="M80 16 L82 -20 Q83 -31 91 -31 Q99 -30 98 -20 L100 16Z"
          fill="#7B2CBF"/>
    <path d="M84 -25 Q73 -37 77 -46 M90 -28 Q88 -43 94 -51 M96 -25 Q106 -38 105 -46"
          fill="none" stroke="#00F5D4" stroke-width="2.2" stroke-linecap="round"/>
    <path d="M108 22 L110 -11 Q111 -21 117 -21 Q124 -20 124 -11 L126 22Z"
          fill="#1D4ED8"/>
    <path d="M112 -17 Q104 -29 108 -36 M118 -20 Q120 -34 125 -40"
          fill="none" stroke="#FFBE0B" stroke-width="2" stroke-linecap="round"/>
  </g>
</g>

<!-- Right reef cluster mirrors the left, but keeps a different silhouette. -->
<g transform="translate(1156 495)">
  <path d="M0 0 C9 -42 6 -88 -3 -125 C-11 -88 -12 -42 -6 0Z"
        fill="#00F5D4" stroke="#030712" stroke-width="3" stroke-linejoin="round"/>
  <path d="M-2 -14 C2 -48 0 -80 -3 -112"
        fill="none" stroke="#030712" stroke-width="1.8" stroke-linecap="round"/>
  <path d="M-28 8 L-28 -30 L-11 -54 L-17 -58 L-32 -38 L-36 -68 L-44 -68
           L-40 -34 L-58 -52 L-63 -47 L-41 -22 L-41 8Z"
        fill="#FF2A85" stroke="#030712" stroke-width="3"
        stroke-linejoin="bevel" filter="url(#soft-glow)"/>
  <path d="M-28 -30 L-32 -38 L-36 -68 M-40 -34 L-58 -52"
        fill="none" stroke="#FFE5F1" stroke-width="1.3" opacity=".75"/>
  <g stroke="#030712" stroke-width="2.5" stroke-linejoin="round">
    <path d="M-82 16 L-84 -20 Q-85 -31 -93 -31 Q-101 -30 -100 -20 L-102 16Z"
          fill="#7B2CBF"/>
    <path d="M-86 -25 Q-75 -37 -79 -46 M-92 -28 Q-90 -43 -96 -51 M-98 -25 Q-108 -38 -107 -46"
          fill="none" stroke="#00F5D4" stroke-width="2.2" stroke-linecap="round"/>
    <path d="M-108 22 L-110 -11 Q-111 -21 -117 -21 Q-124 -20 -124 -11 L-126 22Z"
          fill="#1D4ED8"/>
    <path d="M-112 -17 Q-104 -29 -108 -36 M-118 -20 Q-120 -34 -125 -40"
          fill="none" stroke="#FFBE0B" stroke-width="2" stroke-linecap="round"/>
  </g>
</g>

<!-- Small comic coral fans break up the long seabed line. -->
<g fill="none" stroke-linecap="round" stroke-linejoin="round">
  <g stroke="#FFBE0B" stroke-width="2.4" opacity=".86">
    <path d="M286 490 Q276 467 282 449 M282 467 Q263 455 260 441 M282 471 Q300 454 306 438"/>
    <path d="M914 490 Q924 467 918 449 M918 467 Q937 455 940 441 M918 471 Q900 454 894 438"/>
  </g>
  <g stroke="#8B5CFF" stroke-width="2.2" opacity=".9">
    <path d="M318 490 Q313 469 324 454 M324 470 Q340 456 342 444 M324 476 Q307 463 302 450"/>
    <path d="M882 490 Q887 469 876 454 M876 470 Q860 456 858 444 M876 476 Q893 463 898 450"/>
  </g>
</g>

<!-- Layered sand shelf with a hard comic ink edge. -->
<path d="M8 594 Q320 578 600 592 T1192 588 L1192 650 L8 650Z"
      fill="#060D20" stroke="#030712" stroke-width="4" stroke-linejoin="bevel"/>
<path d="M8 594 Q320 578 600 592 T1192 588"
      fill="none" stroke="#00F5D4" stroke-width="1.7" opacity=".58"/>
<path d="M48 615 Q150 604 236 614 M950 614 Q1048 604 1148 615"
      fill="none" stroke="#7B2CBF" stroke-width="1.3" opacity=".62"/>
<g fill="#FFBE0B" opacity=".52">
  <ellipse cx="169" cy="611" rx="16" ry="2.5"/>
  <ellipse cx="1033" cy="610" rx="21" ry="2.5"/>
</g>
<!-- A drifting jellyfish in the lower water column. -->
<g id="reef-jellyfish" transform="translate(190 410)" opacity=".82">
  <animateTransform attributeName="transform" type="translate" additive="sum"
    values="0 0;11 -18;2 0" dur="14s" repeatCount="indefinite"/>
  <path d="M-12 1 C-13 -14 -5 -22 0 -22 C7 -22 13 -13 12 1Z"
        fill="#8B5CFF" fill-opacity=".26" stroke="#D5BCFF" stroke-opacity=".92" stroke-width=".9"/>
  <path d="M-10 1 Q0 7 10 1" fill="none" stroke="#FF79D8" stroke-width="1.1"/>
  <g fill="none" stroke="#00E5FF" stroke-width="1.1" stroke-linecap="round">
    <path d="M-8 3 Q-13 13 -9 21"><animate attributeName="d" values="M-8 3 Q-13 13 -9 21;M-8 3 Q-4 13 -9 21;M-8 3 Q-13 13 -9 21" dur="3.2s" repeatCount="indefinite"/></path>
    <path d="M-2 4 Q-5 15 -1 24"><animate attributeName="d" values="M-2 4 Q-5 15 -1 24;M-2 4 Q2 15 -1 24;M-2 4 Q-5 15 -1 24" dur="3.8s" repeatCount="indefinite"/></path>
    <path d="M5 4 Q10 13 7 22"><animate attributeName="d" values="M5 4 Q10 13 7 22;M5 4 Q1 13 7 22;M5 4 Q10 13 7 22" dur="3.4s" repeatCount="indefinite"/></path>
  </g>
</g>

    pieces.append(
        '''<g id="resting-octopus" transform="translate(600 514)">
  <!-- Broad contact shadow anchors the animal to the sand. -->
  <ellipse cx="0" cy="116" rx="183" ry="13"
           fill="#030712" opacity=".82"/>
  <ellipse cx="0" cy="117" rx="136" ry="5"
           fill="#8B5CFF" opacity=".16"/>

  <!-- Quiet comic impact contour behind the mantle, not a cartoon sunburst. -->
  <path d="M-8 -151 L12 -126 L46 -144 L42 -110 L78 -98
           L52 -73 L75 -45 L37 -40 L27 -5 L0 -28
           L-32 -5 L-38 -42 L-77 -53 L-51 -82 L-76 -111
           L-39 -115Z"
        fill="#7B2CBF" opacity=".16" stroke="#030712"
        stroke-width="4" stroke-linejoin="bevel"/>
  <path d="M-5 -140 L12 -118 L42 -132 L36 -105 L63 -92
           L43 -72 L62 -49 L31 -43"
        fill="none" stroke="#FF2A85" stroke-width="2"
        stroke-linejoin="bevel" opacity=".72"/>
'''
    )

    octopus_arms = [
        # Four arms fan out on each side. Broad shoulders taper toward curled tips.
        (-13, 23, 155, 1.02,
         "M0 -8 C22 -20 45 -18 68 -8 C95 3 120 17 148 25 C160 29 171 23 173 13 C184 23 178 39 163 45 C146 52 128 42 105 31 C81 18 56 5 34 4 C18 3 8 8 0 8 Q-6 0 0 -8Z",
         [(20, 2), (36, 3), (52, 5), (68, 8), (84, 12), (100, 17), (116, 22), (132, 27)],
         "9.1s", "0s"),
        (-18, 28, 187, 1.00,
         "M0 -7 C20 -17 42 -12 61 0 C85 14 107 30 132 36 C147 40 158 31 154 20 C169 31 164 48 148 54 C130 60 111 46 92 34 C70 20 45 8 22 10 L0 8Z",
         [(19, 2), (35, 5), (51, 9), (67, 14), (83, 20), (99, 26), (115, 32)],
         "10.1s", ".7s"),
        (-14, 34, 218, .94,
         "M0 -8 C17 -16 35 -8 50 7 C68 26 84 47 107 53 C121 57 133 47 126 35 C142 44 142 62 127 71 C109 80 91 64 75 47 C57 27 39 12 20 10 L0 8Z",
         [(18, 3), (33, 7), (48, 13), (63, 21), (78, 30), (93, 39), (108, 48)],
         "10.4s", "1.4s"),
        (-7, 39, 246, .88,
         "M0 -8 C15 -16 29 -6 41 10 C54 27 65 48 82 55 C96 60 106 49 98 38 C114 42 119 60 106 71 C89 81 72 66 59 48 C43 27 29 11 14 10 L0 8Z",
         [(15, 3), (29, 8), (43, 15), (57, 24), (70, 34), (83, 44), (96, 55)],
         "9.6s", "2.0s"),
        (13, 23, 25, 1.00,
         "M0 -8 C22 -20 45 -18 68 -8 C95 3 120 17 148 25 C160 29 171 23 173 13 C184 23 178 39 163 45 C146 52 128 42 105 31 C81 18 56 5 34 4 C18 3 8 8 0 8 Q-6 0 0 -8Z",
         [(20, 2), (36, 3), (52, 5), (68, 8), (84, 12), (100, 17), (116, 22), (132, 27)],
         "9.4s", ".3s"),
        (18, 28, -8, 1.03,
         "M0 -7 C20 -17 42 -12 61 0 C85 14 107 30 132 36 C147 40 158 31 154 20 C169 31 164 48 148 54 C130 60 111 46 92 34 C70 20 45 8 22 10 L0 8Z",
         [(19, 2), (35, 5), (51, 9), (67, 14), (83, 20), (99, 26), (115, 32)],
         "10.3s", "1.0s"),
        (14, 34, -38, .96,
         "M0 -8 C17 -16 35 -8 50 7 C68 26 84 47 107 53 C121 57 133 47 126 35 C142 44 142 62 127 71 C109 80 91 64 75 47 C57 27 39 12 20 10 L0 8Z",
         [(18, 3), (33, 7), (48, 13), (63, 21), (78, 30), (93, 39), (108, 48)],
         "10.7s", "1.7s"),
        (7, 39, -66, .90,
         "M0 -8 C15 -16 29 -6 41 10 C54 27 65 48 82 55 C96 60 106 49 98 38 C114 42 119 60 106 71 C89 81 72 66 59 48 C43 27 29 11 14 10 L0 8Z",
         [(15, 3), (29, 8), (43, 15), (57, 24), (70, 34), (83, 44), (96, 55)],
         "9.8s", ".9s"),
    ]

    for arm_index, (x, y, angle, scale, arm_path, cups, duration, begin) in enumerate(octopus_arms):
        pieces.append(
            f\\'\\'\\'  <g data-octopus-arm="true" data-arm-index="{arm_index}"
       transform="translate({x} {y}) rotate({angle}) scale({scale})">
    <g>
      <animateTransform attributeName="transform" type="rotate" additive="sum"
        values="-1.1 0 0;1.1 0 0;-1.1 0 0" dur="{duration}"
        begin="{begin}" calcMode="spline"
        keySplines=".42 0 .58 1;.42 0 .58 1" repeatCount="indefinite"/>
      <path d="{arm_path}" fill="#030712" stroke="#02030B"
        stroke-width="5.2" stroke-linejoin="round"/>
      <path d="{arm_path}" fill="url(#octopus-arm)"
        stroke="#FFB5F0" stroke-opacity=".78" stroke-width="1.1"
        stroke-linejoin="round"/>
      <path d="{arm_path}" fill="url(#halftone-violet)" opacity=".24"
        stroke="none"/>
      <path d="M8 -3 C29 -13 51 -11 73 -3"
        fill="none" stroke="url(#octopus-highlight)"
        stroke-width="2.1" stroke-linecap="round" opacity=".8"/>
\\'\\'\\'
        )
        for cup_x, cup_y in cups:
            for row_offset, opacity in ((-3.1, ".72"), (0, "1")):
                pieces.append(
                    f\\'\\'\\'      <g data-sucker="true" transform="translate({cup_x} {cup_y + row_offset})">
        <ellipse rx="3.0" ry="2.05" fill="#08071A"
          stroke="#FFBE0B" stroke-width=".62" stroke-opacity=".78"/>
        <ellipse rx="1.2" ry=".8" fill="#00F5D4" opacity="{opacity}"/>
        <circle cx="-.55" cy="-.45" r=".42" fill="#FFFFFF" opacity=".92"/>
      </g>
\\'\\'\\'
                )
        pieces.append("    </g>\\n  </g>\\n")

    pieces.append(
        f\\'\\'\\'  <g id="octopus-mantle">
    <animateTransform attributeName="transform" type="scale"
      values="1 1;1.018 .988;1 1" dur="6.8s"
      calcMode="spline" keySplines=".42 0 .58 1;.42 0 .58 1"
      repeatCount="indefinite"/>

    <!-- Rear mantle sac: broad dome tapering into the neck. -->
    <path d="M-37 -20 C-59 -42 -61 -77 -49 -107
      C-37 -139 -12 -153 17 -147 C48 -141 64 -112 59 -81
      C55 -52 38 -26 18 -13 C1 -3 -22 -6 -37 -20Z"
      fill="none" stroke="#030712" stroke-width="6"
      stroke-linejoin="round"/>
    <path d="M-37 -20 C-59 -42 -61 -77 -49 -107
      C-37 -139 -12 -153 17 -147 C48 -141 64 -112 59 -81
      C55 -52 38 -26 18 -13 C1 -3 -22 -6 -37 -20Z"
      fill="url(#octopus-skin)" stroke="#FFB5F0"
      stroke-opacity=".92" stroke-width="1.7" stroke-linejoin="round"/>
    <!-- Inked shadow plane and offset halftone print. -->
    <path d="M8 -144 C42 -137 62 -112 59 -81
      C55 -51 37 -25 18 -13 L5 -8 C26 -35 39 -67 35 -96
      C31 -122 19 -137 8 -144Z"
      fill="#030712" opacity=".48"/>
    <path d="M-37 -20 C-59 -42 -61 -77 -49 -107
      C-37 -139 -12 -153 17 -147 C48 -141 64 -112 59 -81
      C55 -52 38 -26 18 -13 C1 -3 -22 -6 -37 -20Z"
      fill="url(#halftone-violet)" opacity=".36"/>
    <path d="M-41 -34 C-55 -63 -53 -100 -32 -126"
      fill="none" stroke="#00F5D4" stroke-width="4.2"
      stroke-linecap="round" opacity=".88"/>
    <path d="M32 -130 Q55 -103 49 -70"
      fill="none" stroke="#FF2A85" stroke-width="3.2"
      stroke-linecap="round" opacity=".78"/>

    <!-- Chromatophore spots, including warm amber freckles. -->
    <g fill="#FFBE0B" opacity=".7">
      <circle cx="-31" cy="-89" r="2.2"><animate attributeName="opacity" values=".25;.8;.25" dur="7.4s" repeatCount="indefinite"/></circle>
      <circle cx="-12" cy="-122" r="1.7"><animate attributeName="opacity" values=".8;.28;.8" dur="8.1s" repeatCount="indefinite"/></circle>
      <circle cx="22" cy="-109" r="2.3"><animate attributeName="opacity" values=".3;.85;.3" dur="6.9s" repeatCount="indefinite"/></circle>
      <circle cx="39" cy="-73" r="1.8"><animate attributeName="opacity" values=".8;.25;.8" dur="7.8s" repeatCount="indefinite"/></circle>
      <circle cx="-20" cy="-57" r="1.6"><animate attributeName="opacity" values=".28;.8;.28" dur="9s" repeatCount="indefinite"/></circle>
    </g>
    <g fill="#00F5D4" opacity=".62">
      <circle cx="-22" cy="-103" r="1.5"/>
      <circle cx="4" cy="-133" r="1.8"/>
      <circle cx="34" cy="-93" r="1.35"/>
      <circle cx="10" cy="-63" r="1.5"/>
    </g>
    <path d="M-31 -112 Q-15 -143 12 -139"
      fill="none" stroke="#FFFFFF" stroke-opacity=".82"
      stroke-width="3.2" stroke-linecap="round"/>

    <!-- Cephalic head beneath the mantle, with lateral eye bulges. -->
    <path d="M-43 -13 C-59 -3 -60 19 -46 37
      C-31 55 -12 61 8 59 C30 58 48 47 57 29
      C65 11 52 -8 35 -16 C16 -26 -25 -25 -43 -13Z"
      fill="none" stroke="#030712" stroke-width="6"
      stroke-linejoin="round"/>
    <path d="M-43 -13 C-59 -3 -60 19 -46 37
      C-31 55 -12 61 8 59 C30 58 48 47 57 29
      C65 11 52 -8 35 -16 C16 -26 -25 -25 -43 -13Z"
      fill="url(#octopus-arm)" stroke="#FFB5F0"
      stroke-opacity=".92" stroke-width="1.6" stroke-linejoin="round"/>
    <path d="M-49 21 Q-38 47 -13 53"
      fill="none" stroke="#00F5D4" stroke-width="2"
      stroke-linecap="round" opacity=".78"/>
    <path d="M23 -13 Q47 -6 56 14"
      fill="none" stroke="#FF2A85" stroke-width="2"
      stroke-linecap="round" opacity=".8"/>
    <path d="M-38 3 Q-19 -13 0 -9 Q24 -13 44 4
      L39 34 Q12 52 -20 38Z"
      fill="url(#halftone-violet)" opacity=".3"/>

    <!-- Lateral eyes with horizontal pupils rather than oversized round cartoon eyes. -->
    <ellipse cx="-25" cy="10" rx="8.6" ry="10.8"
      fill="#D8F7FF" stroke="#030712" stroke-width="2.8"/>
    <ellipse cx="27" cy="10" rx="8.6" ry="10.8"
      fill="#D8F7FF" stroke="#030712" stroke-width="2.8"/>
    <ellipse cx="-25" cy="10.4" rx="4.7" ry="2.25" fill="#08091C"/>
    <ellipse cx="27" cy="10.4" rx="4.7" ry="2.25" fill="#08091C"/>
    <circle cx="-27" cy="7.3" r="1.7" fill="#FFFFFF"/>
    <circle cx="25" cy="7.3" r="1.7" fill="#FFFFFF"/>
    <path d="M-36 -2 Q-25 -10 -14 -4 M16 -4 Q28 -11 39 -2"
      fill="none" stroke="#030712" stroke-width="3.4"
      stroke-linecap="round"/>

    <!-- Siphon funnel on the right underside of the head. -->
    <g id="octopus-siphon">
      <animateTransform attributeName="transform" type="scale"
        values="1 1;1 .94;1 1" dur="4.8s" repeatCount="indefinite"/>
      <path d="M38 16 C53 7 65 19 56 33
        L45 43 Q43 31 34 25Z"
        fill="#7B2CBF" stroke="#030712" stroke-width="2.8"
        stroke-linejoin="round"/>
      <ellipse cx="55" cy="22" rx="3.3" ry="5.2"
        fill="#030712" transform="rotate(18 55 22)"/>
      <path d="M43 18 Q51 12 57 18"
        fill="none" stroke="#00F5D4" stroke-width="1.5"
        stroke-linecap="round"/>
    </g>

    <!-- Beak and oral web sit where all eight arms meet. -->
    <path d="M-6 43 Q0 35 6 43 L2 52 Q0 55 -3 52Z"
      fill="#08091C" stroke="#FFBE0B" stroke-width="1.25"
      stroke-linejoin="round"/>
    <path d="M-1 43 L1 50" stroke="#FF2A85" stroke-width="1.2"
      stroke-linecap="round"/>
  </g>
</g>

<!-- Bubbles rise from the octopus den and catch the neon rim light. -->
<g fill="none" stroke="#00F5D4" stroke-opacity=".85" stroke-width="1.1">
  <circle cx="375" cy="470" r="2.5"><animate attributeName="cy" values="470;420;470" dur="8s" repeatCount="indefinite"/></circle>
  <circle cx="388" cy="482" r="1.4"><animate attributeName="cy" values="482;447;482" dur="9s" begin="1.4s" repeatCount="indefinite"/></circle>
  <circle cx="402" cy="463" r="1.8"><animate attributeName="cy" values="463;424;463" dur="7.5s" begin=".7s" repeatCount="indefinite"/></circle>
</g>
<g fill="none" stroke="#FF2A85" stroke-opacity=".86" stroke-width="1">
  <circle cx="810" cy="468" r="2.2"><animate attributeName="cy" values="468;425;468" dur="7.7s" begin=".8s" repeatCount="indefinite"/></circle>
  <circle cx="825" cy="482" r="1.35"><animate attributeName="cy" values="482;442;482" dur="8.8s" begin="2s" repeatCount="indefinite"/></circle>
  <circle cx="795" cy="458" r="1.55"><animate attributeName="cy" values="458;418;458" dur="7.1s" begin="1.2s" repeatCount="indefinite"/></circle>
</g>

<!-- The footer is a clear information strip, separate from the seabed. -->
<path d="M24 558 Q250 550 450 556 T850 555 T1096 558"
      fill="none" stroke="#00E5FF" stroke-opacity=".36"/>
<text x="39" y="578" font-family="Trebuchet MS, Arial, sans-serif"
      font-size="9" font-weight="700" letter-spacing=".9"
      fill="#B9B7FF">{start.strftime("%b %d, %Y").upper()}  /  {end.strftime("%b %d, %Y").upper()}</text>
<g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
   font-weight="700" letter-spacing=".8" fill="#A9A5FF">
  <text x="820" y="578">LESS</text>'''
    )

    for index, level in enumerate(
        ("NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE")
    ):
        fill, stroke, _ = LEVEL_COLORS[level]
        pieces.append(
            f'<rect x="{858 + index * 20}" y="569" width="12" height="12" rx="4" '
            f'fill="{fill}" stroke="{stroke}" stroke-width=".85"/>'
        )

    pieces.append(
        '''<text x="972" y="578">MORE</text>
</g>
</g>
<!-- Offset ink frame and registration edges finish the comic panel. -->
<rect x="3.5" y="3.5" width="1113" height="583" rx="19"
      fill="none" stroke="#030411" stroke-width="7"/>
<rect x=".75" y=".75" width="1118.5" height="588.5" rx="22"
      fill="none" stroke="url(#glass-edge)" stroke-width="2.4"/>
<rect x="7" y="7" width="1106" height="576" rx="17"
      fill="none" stroke="#00E5FF" stroke-opacity=".4" stroke-width=".8"/>
<path d="M12 19 L12 92 M1108 19 L1108 92 M12 516 L12 570 M1108 516 L1108 570"
      fill="none" stroke="#FF3BC8" stroke-width="2.2" opacity=".72"/>
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
