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

<!-- The foreground seabed is layered behind the octopus and coral. -->
<path d="M0 470 C125 448 225 469 344 462 C466 453 570 478 702 462
 C850 451 970 472 1120 458 L1120 590 L0 590Z"
      fill="url(#sand)" opacity=".96"/>
<path d="M0 470 C125 448 225 469 344 462 C466 453 570 478 702 462
 C850 451 970 472 1120 458"
      fill="none" stroke="#8B5CFF" stroke-opacity=".5" stroke-width="1.4"/>
<g fill="#6757FF" opacity=".32">
  <ellipse cx="105" cy="492" rx="17" ry="3"/>
  <ellipse cx="278" cy="499" rx="24" ry="3"/>
  <ellipse cx="810" cy="495" rx="19" ry="3"/>
  <ellipse cx="1020" cy="489" rx="26" ry="3"/>
</g>
<g fill="#A69BFF" opacity=".5">
  <circle cx="145" cy="484" r="1.1"/><circle cx="169" cy="499" r=".8"/>
  <circle cx="313" cy="485" r="1.2"/><circle cx="751" cy="495" r="1"/>
  <circle cx="1045" cy="505" r="1.3"/><circle cx="1080" cy="487" r=".8"/>
</g>

<!-- Neon coral gardens and kelp frame the foreground shelf. -->
<g fill="none" stroke-linecap="round" stroke-linejoin="round">
  <g stroke="#00E5FF" stroke-width="3.4" opacity=".82">
    <path d="M25 470 C31 444 17 424 28 384 C41 421 32 447 40 470">
      <animate attributeName="d" values="M25 470 C31 444 17 424 28 384 C41 421 32 447 40 470;M25 470 C36 444 25 421 35 384 C44 422 29 448 40 470;M25 470 C31 444 17 424 28 384 C41 421 32 447 40 470" dur="9s" repeatCount="indefinite"/>
    </path>
    <path d="M1085 470 C1077 443 1098 421 1088 384 C1074 419 1082 448 1072 470">
      <animate attributeName="d" values="M1085 470 C1077 443 1098 421 1088 384 C1074 419 1082 448 1072 470;M1085 470 C1073 443 1089 419 1080 384 C1069 420 1088 449 1072 470;M1085 470 C1077 443 1098 421 1088 384 C1074 419 1082 448 1072 470" dur="10s" repeatCount="indefinite"/>
    </path>
  </g>
  <g stroke="#FF3BC8" stroke-width="2.6" opacity=".88">
    <path d="M112 470 Q104 450 113 428 M113 452 Q94 438 98 420 M113 460 Q132 442 136 423 M110 445 Q108 430 119 416"/>
    <path d="M1007 470 Q1017 450 1008 428 M1008 452 Q1027 438 1023 420 M1008 460 Q989 442 985 423"/>
  </g>
  <g stroke="#8B5CFF" stroke-width="2.4" opacity=".95">
    <path d="M72 470 Q73 451 62 437 M72 455 Q87 444 87 429 M72 461 Q58 454 54 443"/>
    <path d="M1047 470 Q1046 450 1058 435 M1047 455 Q1033 444 1032 429"/>
  </g>
</g>
<g fill="#171638" stroke="#5143A4" stroke-opacity=".78" stroke-width="1">
  <path d="M0 470 Q30 448 62 455 Q89 444 116 470 L140 590H0Z"/>
  <path d="M1120 464 Q1090 446 1060 456 Q1036 444 1005 469 L980 590H1120Z"/>
</g>
<g fill="none" stroke="#FF3BC8" stroke-width="1.35" stroke-linecap="round" opacity=".82">
  <path d="M126 470 Q124 455 134 448 Q146 442 144 429 M134 448 Q119 442 117 430 M134 448 Q151 444 158 434"/>
  <path d="M994 469 Q996 452 985 446 Q974 439 976 427 M985 446 Q999 440 1003 429 M985 446 Q969 440 963 432"/>
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

<!-- Eight relaxed arms, soft suckers, and a color-shifting mantle. -->
<g id="resting-octopus" transform="translate(560 482)">
  <ellipse cx="0" cy="31" rx="154" ry="8" fill="#030411" opacity=".68"/>
  <!-- Eight-point impact burst behind the resting octopus. -->
  <path d="M0 -128 L18 -62 L66 -105 L57 -48 L126 -53 L75 -10
           L135 21 L65 32 L91 101 L29 56 L0 126 L-27 57
           L-93 97 L-62 30 L-132 13 L-73 -20 L-124 -67
           L-53 -53 L-68 -112 L-15 -64Z"
        fill="#8B5CFF" fill-opacity=".17" stroke="#030411" stroke-width="4"
        stroke-linejoin="miter"/>
  <path d="M0 -119 L13 -63 L60 -96 L52 -44 L115 -47 L68 -8
           L122 19 L59 26 L80 91 L25 49 L0 112 L-23 48
           L-81 88 L-52 24 L-119 11 L-66 -18 L-111 -62
           L-46 -49 L-59 -101 L-12 -58Z"
        fill="none" stroke="#FF3BC8" stroke-width="2.2" opacity=".72"
        stroke-linejoin="miter"/>
'''
    )

    octopus_arms = [
        # Four left and four right arms. Each is a filled, tapered ribbon rather
        # than a constant width line, with an individually curved silhouette.
        ("-12 3", "M0 -7 C-17 -19 -38 -20 -59 -12 C-82 -4 -108 8 -137 16 Q-149 20 -160 14 C-151 32 -137 32 -121 25 C-99 16 -81 4 -60 -1 C-38 -7 -17 2 0 7 Q-6 1 0 -7Z", "-22 -12 -42 -12 -62 -8 -82 0 -102 8 -122 14 -140 17", "-1.5 0 0;1.5 0 0;-1.5 0 0", "9.1s", "0s"),
        ("-17 9", "M0 -6 C-21 -14 -43 -9 -65 4 C-88 17 -110 30 -139 32 Q-151 34 -161 25 C-153 45 -137 46 -120 42 C-97 37 -78 25 -56 13 C-35 2 -16 1 0 7Z", "-22 0 -43 4 -64 13 -84 22 -104 28 -126 33 -146 36", "1.2 0 0;-1.2 0 0;1.2 0 0", "9.8s", ".6s"),
        ("-14 17", "M0 -5 C-17 -1 -34 15 -49 36 C-65 58 -91 70 -119 73 Q-132 74 -142 65 C-136 87 -119 87 -102 81 C-77 72 -57 59 -39 39 C-23 20 -10 9 0 7Z", "-14 10 -30 26 -47 43 -64 58 -83 68 -104 75 -124 78", "-1.1 0 0;1.4 0 0;-1.1 0 0", "10.4s", "1.2s"),
        ("-4 22", "M0 -6 C-8 5 -14 17 -17 32 C-20 47 -38 62 -60 70 Q-70 73 -79 66 C-75 84 -60 85 -47 78 C-28 67 -13 54 -7 38 C0 22 6 7 7 4Z", "-5 12 -14 32 -24 52 -35 72 -51 89 -70 102 -84 106", "1.3 0 0;-1.1 0 0;1.3 0 0", "9.6s", "1.7s"),
        ("12 3", "M0 -7 C17 -19 38 -20 59 -12 C82 -4 108 8 137 16 Q149 20 160 14 C151 32 137 32 121 25 C99 16 81 4 60 -1 C38 -7 17 2 0 7 Q6 1 0 -7Z", "22 -12 42 -12 62 -8 82 0 102 8 122 14 140 17", "1.4 0 0;-1.4 0 0;1.4 0 0", "9.3s", ".3s"),
        ("17 9", "M0 -6 C21 -14 43 -9 65 4 C88 17 110 30 139 32 Q151 34 161 25 C153 45 137 46 120 42 C97 37 78 25 56 13 C35 2 16 1 0 7Z", "22 0 43 4 64 13 84 22 104 28 126 33 146 36", "-1.1 0 0;1.3 0 0;-1.1 0 0", "10.1s", "1s"),
        ("14 17", "M0 -5 C17 -1 34 15 49 36 C65 58 91 70 119 73 Q132 74 142 65 C136 87 119 87 102 81 C77 72 57 59 39 39 C23 20 10 9 0 7Z", "14 10 30 26 47 43 64 58 83 68 104 75 124 78", "1.2 0 0;-1.5 0 0;1.2 0 0", "10.6s", "1.5s"),
        ("4 22", "M0 -6 C8 5 14 17 17 32 C20 47 38 62 60 70 Q70 73 79 66 C75 84 60 85 47 78 C28 67 13 54 7 38 C0 22 -6 7 -7 4Z", "5 12 14 32 24 52 35 72 51 89 70 102 84 106", "-1.2 0 0;1.1 0 0;-1.2 0 0", "9.9s", ".8s"),
    ]
    for arm_index, (translate, arm_path, sucker_coords, rotation, duration, begin) in enumerate(octopus_arms):
        pieces.append(
            f'''  <g transform="translate({translate})" data-octopus-arm="true"
       data-arm-index="{arm_index}">
    <animateTransform attributeName="transform" type="rotate" additive="sum"
      values="{rotation}" dur="{duration}" begin="{begin}"
      calcMode="spline" keySplines=".42 0 .58 1;.42 0 .58 1"
      repeatCount="indefinite"/>
    <path d="{arm_path}" fill="#030411" stroke="#02030B"
      stroke-width="4.2" stroke-linejoin="round"/>
    <path d="{arm_path}" fill="url(#octopus-arm)"
      stroke="#FFB5F0" stroke-opacity=".76" stroke-width="1.05"
      stroke-linejoin="round"/>
    <path d="{arm_path}" fill="none" stroke="url(#octopus-highlight)"
      stroke-width="1.7" stroke-linecap="round" opacity=".78"/>
'''
        )
        coordinates = sucker_coords.split()
        for index in range(0, len(coordinates), 2):
            sx, sy = coordinates[index], coordinates[index + 1]
            pieces.append(
                f'''    <g data-sucker="true" transform="translate({sx} {sy})">
      <ellipse rx="3.05" ry="2.3" fill="#08071A"
        stroke="#FFB5F0" stroke-width=".8"/>
      <ellipse rx="1.55" ry="1.05" fill="#00E5FF" opacity=".9"/>
      <circle cx="-.55" cy="-.55" r=".48" fill="#FFFFFF" opacity=".9"/>
    </g>
'''
            )
        pieces.append("  </g>\n")

    pieces.append(
        f'''  <g id="octopus-mantle">
    <animateTransform attributeName="transform" type="scale"
      values="1 1;1.025 .975;1 1" dur="5.8s" repeatCount="indefinite"/>
    <path d="M-26 -10 C-50 -24 -48 -60 -28 -82 C-12 -100 16 -99 35 -80
      C54 -61 50 -25 26 -9 C11 2 -12 4 -26 -10Z"
      fill="none" stroke="#030411" stroke-width="5.2" stroke-linejoin="miter"/>
    <path d="M-26 -10 C-50 -24 -48 -60 -28 -82 C-12 -100 16 -99 35 -80
      C54 -61 50 -25 26 -9 C11 2 -12 4 -26 -10Z"
      fill="url(#octopus-skin)" stroke="#FFB5F0" stroke-opacity=".96" stroke-width="1.8"
      stroke-linejoin="miter"/>
    <path id="octopus-head" d="M-38 -8 C-54 2 -52 28 -34 45 C-18 61 16 61 34 44
      C52 27 49 3 33 -9 C16 -24 -22 -24 -38 -8Z"
      fill="url(#octopus-arm)" stroke="#030411" stroke-width="5.2"
      stroke-linejoin="round"/>
    <path d="M-38 -8 C-54 2 -52 28 -34 45 C-18 61 16 61 34 44
      C52 27 49 3 33 -9"
      fill="none" stroke="#FFB5F0" stroke-width="1.6" stroke-opacity=".95"
      stroke-linecap="round"/>
    <path d="M-33 1 C-20 -10 10 -15 30 -3 L36 25
      C19 43 -13 50 -34 28Z"
      fill="url(#halftone-violet)" opacity=".28"/>
    <path d="M-31 28 Q-16 42 0 43 Q19 42 33 26"
      fill="none" stroke="#030411" stroke-width="2.5" stroke-opacity=".55"
      stroke-linecap="round"/>
    <path d="M-29 -1 Q-17 -10 -6 -5 M8 -5 Q20 -11 31 -1"
      fill="none" stroke="#FFFFFF" stroke-opacity=".58" stroke-width="1.4"
      stroke-linecap="round"/>
    <path d="M-21 51 Q-10 58 0 56 Q11 58 22 51"
      fill="none" stroke="#8B5CFF" stroke-width="2" stroke-linecap="round"/>
    <path d="M-34 -34 C-27 -70 -7 -86 18 -82 C38 -77 45 -48 30 -24
      C13 -10 -15 -13 -34 -34Z" fill="url(#octopus-blush)" opacity=".6"/>
    <g fill="#B5A2FF" opacity=".74">
      <circle cx="-18" cy="-13" r="2.1"><animate attributeName="opacity" values=".25;.65;.25" dur="8s" repeatCount="indefinite"/></circle>
      <circle cx="-8" cy="-25" r="1.5"><animate attributeName="opacity" values=".6;.22;.6" dur="7s" repeatCount="indefinite"/></circle>
      <circle cx="10" cy="-21" r="2"><animate attributeName="opacity" values=".25;.7;.25" dur="9s" repeatCount="indefinite"/></circle>
      <circle cx="22" cy="-8" r="1.4"><animate attributeName="opacity" values=".65;.2;.65" dur="7.8s" repeatCount="indefinite"/></circle>
      <circle cx="14" cy="12" r="1.7"><animate attributeName="opacity" values=".2;.6;.2" dur="8.5s" repeatCount="indefinite"/></circle>
    </g>
    <path d="M-28 -67 Q-9 -91 14 -84" fill="none"
      stroke="#FFFFFF" stroke-opacity=".78" stroke-width="3.1" stroke-linecap="round"/>
    <path d="M-36 -48 Q-24 -72 -8 -75 M23 -78 Q40 -60 40 -42"
      fill="none" stroke="#00E5FF" stroke-opacity=".72" stroke-width="1.7"
      stroke-linecap="round"/>
    <path d="M-34 -22 Q-13 -35 11 -30 Q27 -27 35 -16"
      fill="none" stroke="#030411" stroke-opacity=".48" stroke-width="2.8"
      stroke-linecap="round"/>
    <path d="M-33 -55 Q-26 -66 -17 -62 Q-13 -54 -22 -50Z
             M8 -70 Q18 -79 27 -69 Q28 -59 16 -58Z
             M29 -39 Q39 -45 42 -33 Q35 -26 28 -30Z"
      fill="#140B3D" opacity=".6"/>
    <path d="M-32 -39 Q-28 -47 -21 -42 M-5 -54 Q1 -60 7 -54
             M22 -48 Q29 -54 35 -48 M-20 -13 Q-13 -19 -7 -13"
      fill="none" stroke="#FFE3FA" stroke-opacity=".74" stroke-width="1.6"
      stroke-linecap="round"/>
    <ellipse cx="-17" cy="12" rx="7.5" ry="9.2" fill="#D8F7FF"
      stroke="#030411" stroke-width="2.1"/>
    <ellipse cx="17" cy="12" rx="7.5" ry="9.2" fill="#D8F7FF"
      stroke="#030411" stroke-width="2.1"/>
    <ellipse cx="-16" cy="12" rx="2.7" ry="6.6" fill="#08091C"/>
    <ellipse cx="18" cy="12" rx="2.7" ry="6.6" fill="#08091C"/>
    <circle cx="-17.2" cy="8.7" r="1.7" fill="#FFFFFF"/>
    <circle cx="16.8" cy="8.7" r="1.7" fill="#FFFFFF"/>
    <path d="M-27 1 Q-17 -7 -7 0 M7 0 Q18 -8 28 1"
      fill="none" stroke="#08091E" stroke-width="3.8" stroke-linecap="round"/>
    <path d="M-5 34 Q0 27 5 34 L1 42 Q-1 44 -3 41Z"
      fill="#16102F" stroke="#030411" stroke-width="1.8" stroke-linejoin="round"/>
    <path d="M-1 34 L1 40" fill="none" stroke="#FFB5F0" stroke-width="1"/>
  </g>
</g>

<!-- Bubble trails rise from the octopus den. -->
<g fill="none" stroke="#00E5FF" stroke-opacity=".82" stroke-width=".9">
  <circle cx="410" cy="458" r="2.3"><animate attributeName="cy" values="458;410;458" dur="8s" repeatCount="indefinite"/></circle>
  <circle cx="423" cy="466" r="1.3"><animate attributeName="cy" values="466;433;466" dur="9s" begin="1.4s" repeatCount="indefinite"/></circle>
  <circle cx="430" cy="448" r="1.6"><animate attributeName="cy" values="448;414;448" dur="7.5s" begin=".7s" repeatCount="indefinite"/></circle>
</g>
<g fill="none" stroke="#FF3BC8" stroke-opacity=".78" stroke-width=".8">
  <circle cx="706" cy="457" r="2"><animate attributeName="cy" values="457;418;457" dur="7.7s" begin=".8s" repeatCount="indefinite"/></circle>
  <circle cx="718" cy="465" r="1.2"><animate attributeName="cy" values="465;430;465" dur="8.8s" begin="2s" repeatCount="indefinite"/></circle>
  <circle cx="694" cy="447" r="1.4"><animate attributeName="cy" values="447;411;447" dur="7.1s" begin="1.2s" repeatCount="indefinite"/></circle>
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
