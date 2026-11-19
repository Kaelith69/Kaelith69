#!/usr/bin/env python3
"""Build an animated Pac Man contribution calendar from GitHub GraphQL data."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "contributions-pacman.svg"
LOGIN = os.environ.get("GITHUB_PROFILE_LOGIN", "Kaelith69")
API_URL = os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")
ANIMATION_SECONDS = 54

QUERY = """
query ContributionMaze($login: String!, $from: DateTime!, $to: DateTime!) {
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
    "NONE": ("#141D33", "#2C3B56"),
    "FIRST_QUARTILE": ("#244858", "#38677A"),
    "SECOND_QUARTILE": ("#286C78", "#3C9094"),
    "THIRD_QUARTILE": ("#48A995", "#65C9B0"),
    "FOURTH_QUARTILE": ("#F4C653", "#FFF0D1"),
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
            "User-Agent": "Kaelith69-contribution-maze/1.0",
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
    *,
    allow_empty: bool = False,
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
            if start <= date <= end:
                result[date] = {
                    "count": count,
                    "level": level if count else "NONE",
                }

    if not allow_empty:
        missing = [
            (start + dt.timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
            if start + dt.timedelta(days=offset) not in result
        ]
        if missing:
            sample = ", ".join(missing[:5])
            raise RuntimeError(
                f"Contribution calendar is incomplete ({len(missing)} days missing; "
                f"first missing: {sample}). Refusing to publish a partial graph."
            )

    return result


def grid_dates(start: dt.date, end: dt.date) -> list[list[dt.date]]:
    """Return Sunday-first weeks, matching GitHub's contribution graph layout."""
    first_sunday = start - dt.timedelta(days=(start.weekday() + 1) % 7)
    last_sunday = end - dt.timedelta(days=(end.weekday() + 1) % 7)
    column_count = ((last_sunday - first_sunday).days // 7) + 1

    return [
        [first_sunday + dt.timedelta(days=column * 7 + row) for row in range(7)]
        for column in range(column_count)
    ]


def render_svg(
    start: dt.date,
    end: dt.date,
    total: int | None,
    days: dict[dt.date, dict[str, Any]],
) -> str:
    weeks = grid_dates(start, end)
    columns = len(weeks)
    cell = 9
    gap = 4
    pitch = cell + gap
    grid_x = 88
    grid_y = 76
    loop_seconds = ANIMATION_SECONDS

    route: list[tuple[int, int]] = []
    for row in range(7):
        column_order = range(columns) if row % 2 == 0 else range(columns - 1, -1, -1)
        for column in column_order:
            route.append((column, row))

    route_index = {position: index for index, position in enumerate(route)}
    path_points = [
        (
            grid_x + column * pitch + cell / 2,
            grid_y + row * pitch + cell / 2,
        )
        for column, row in route
    ]
    motion_path = "M " + " L ".join(
        f"{x:.1f},{y:.1f}" for x, y in path_points
    )

    pieces: list[str] = []
    pieces.append(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="214"
 viewBox="0 0 860 214" role="img" aria-labelledby="maze-title maze-desc">
<title id="maze-title">GitHub contribution maze for {html.escape(LOGIN)}</title>
<desc id="maze-desc">A Sunday-first 365-day contribution heatmap. An animated Pac Man travels through the weeks and dims each cell after passing it. Total contributions: {total if total is not None else "sync pending"}.</desc>
<defs>
  <linearGradient id="maze-bg" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#080D1D"/><stop offset=".55" stop-color="#101A34"/><stop offset="1" stop-color="#17213E"/>
  </linearGradient>
  <radialGradient id="maze-nebula">
    <stop stop-color="#6C3D87" stop-opacity=".32"/><stop offset="1" stop-color="#6C3D87" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="maze-gold" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#FFF0D1"/><stop offset=".5" stop-color="#F4C653"/><stop offset="1" stop-color="#D7C3A3"/>
  </linearGradient>
  <filter id="maze-glow" x="-100%" y="-100%" width="300%" height="300%">
    <feGaussianBlur stdDeviation="2.5" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="maze-clip"><rect width="860" height="214" rx="14"/></clipPath>
</defs>
<g clip-path="url(#maze-clip)">
  <rect width="860" height="214" rx="14" fill="url(#maze-bg)"/>
  <ellipse cx="480" cy="84" rx="430" ry="190" fill="url(#maze-nebula)" opacity=".6"/>
  <g fill="#FFF0D1">
    <circle cx="24" cy="58" r=".8" opacity=".45"/>
    <circle cx="244" cy="34" r=".7" opacity=".3"/>
    <circle cx="474" cy="24" r=".9" opacity=".4"/>
    <circle cx="830" cy="48" r=".8" opacity=".5"/>
    <circle cx="824" cy="180" r=".7" opacity=".3"/>
    <circle cx="33" cy="184" r=".8" opacity=".35"/>
  </g>
  <path d="M18 49 Q220 30 430 49 T842 49" fill="none" stroke="#55C9C0" stroke-opacity=".15"/>
  <text x="25" y="29" font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="25" letter-spacing="1.6" fill="#FFF0D1"
        stroke="#080D1D" stroke-width="1.8" paint-order="stroke">CONTRIBUTION MAZE</text>
  <text x="27" y="45" font-family="Trebuchet MS, Arial, sans-serif"
        font-size="9" letter-spacing="2" fill="#8DA6BC">LAST 365 DAYS</text>
  <text x="667" y="27" text-anchor="end"
        font-family="Bangers, Impact, 'Arial Black', sans-serif"
        font-size="26" letter-spacing=".8" fill="#F4C653"
        stroke="#080D1D" stroke-width="1.4" paint-order="stroke">{html.escape(f"{total:,}" if total is not None else "SYNC")}</text>
  <text x="670" y="42" text-anchor="end"
        font-family="Trebuchet MS, Arial, sans-serif"
        font-size="8" letter-spacing="1.6" fill="#8DA6BC">CONTRIBUTIONS</text>
  <g transform="translate(817 28)" fill="none">
    <circle r="15" stroke="#8DA6BC" stroke-opacity=".3"/>
    <circle r="11" stroke="#F4C653" stroke-opacity=".7" stroke-width="1"
            stroke-dasharray="2 4">
      <animateTransform attributeName="transform" type="rotate"
                        from="0" to="360" dur="32s" repeatCount="indefinite"/>
    </circle>
    <path d="M0 -7V0L5 -2" stroke="#FFF0D1" stroke-width="1.5"
          stroke-linecap="round"/>
    <circle r="2" fill="#F4C653" filter="url(#maze-glow)"/>
  </g>
  <g font-family="Trebuchet MS, Arial, sans-serif" font-size="8"
     fill="#A5B7CE" letter-spacing=".2">'''
    )

    # Month labels are anchored to the week containing the first of each month.
    month_positions: list[tuple[int, str]] = [(0, start.strftime("%b").upper())]
    seen_months = {(weeks[0][0].year, weeks[0][0].month)}
    for column, week in enumerate(weeks):
        for date in week:
            key = (date.year, date.month)
            if key not in seen_months and date.day == 1:
                month_positions.append((column, date.strftime("%b").upper()))
                seen_months.add(key)
    for column, month in month_positions:
        x = grid_x + column * pitch
        pieces.append(f'<text x="{x}" y="65">{month}</text>')

    # Day initials keep the graph legible without adding a heavy axis.
    day_labels = ["S", "M", "T", "W", "T", "F", "S"]
    for row, label in enumerate(day_labels):
        y = grid_y + row * pitch + cell - 1
        pieces.append(f'<text x="66" y="{y}" text-anchor="middle">{label}</text>')
    pieces.append("</g>")

    # Contribution cells retain the real dates, counts and GitHub intensity levels.
    for column, week in enumerate(weeks):
        for row, date in enumerate(week):
            detail = days.get(date, {"count": 0, "level": "NONE"})
            count = int(detail["count"])
            level = str(detail["level"])
            fill, stroke = LEVEL_COLORS.get(level, LEVEL_COLORS["NONE"])
            x = grid_x + column * pitch
            y = grid_y + row * pitch
            order = route_index[(column, row)]
            begin = order / len(route) * loop_seconds
            tooltip = html.escape(
                f"{date.isoformat()}: {count} contribution"
                f"{'' if count == 1 else 's'}"
            )
            pieces.append(
                f'''<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2"
 data-date="{date.isoformat()}" data-count="{count}" data-level="{html.escape(level)}"
 fill="{fill}" stroke="{stroke}" stroke-width=".7" opacity="1">
 <title>{tooltip}</title>
 <animate attributeName="opacity" values="1;.17;.17"
          keyTimes="0;.004;1" dur="{loop_seconds}s"
          begin="{begin:.3f}s" repeatCount="indefinite"/>
</rect>'''
            )

    # Pac Man snakes across the exact same cells that fade behind him.
    pieces.append(
        f'''<g filter="url(#maze-glow)">
  <g>
    <animateMotion path="{motion_path}" dur="{loop_seconds}s"
                   calcMode="paced" rotate="auto" repeatCount="indefinite"/>
    <path fill="#F4C653" stroke="#FFF0D1" stroke-width=".75"
          d="M 0 0 L 10 -6 A 12 12 0 1 1 10 6 Z">
      <animate attributeName="d"
        values="M 0 0 L 10 -6 A 12 12 0 1 1 10 6 Z;M 0 0 L 12 0 A 12 12 0 1 1 -12 0 A 12 12 0 1 1 12 0 Z;M 0 0 L 10 -6 A 12 12 0 1 1 10 6 Z"
        dur=".42s" repeatCount="indefinite"/>
    </path>
    <circle cx="2.4" cy="-6.1" r="1.55" fill="#17213E"/>
  </g>
</g>'''
    )

    # A pair of small ghosts patrols the quiet margin, away from the real data.
    ghost_y = 116
    for x, color, delay, duration in (
        (808, "#6C3D87", "0s", "3.8s"),
        (834, "#55C9C0", "1.1s", "4.4s"),
    ):
        pieces.append(
            f'''<g transform="translate({x} {ghost_y})">
  <animateTransform attributeName="transform" type="translate"
    values="{x} {ghost_y};{x} {ghost_y - 3};{x} {ghost_y}"
    dur="{duration}" begin="{delay}" repeatCount="indefinite"/>
  <path d="M-7 7V-1A7 7 0 0 1 7 -1V7L3 4L0 7L-3 4Z"
        fill="{color}" stroke="#FFF0D1" stroke-opacity=".55" stroke-width=".6"/>
  <circle cx="-2.5" cy="-1" r="1.6" fill="#FFF0D1"/>
  <circle cx="3" cy="-1" r="1.6" fill="#FFF0D1"/>
  <circle cx="-2.1" cy="-1" r=".65" fill="#101A34"/>
  <circle cx="3.4" cy="-1" r=".65" fill="#101A34"/>
</g>'''
        )

    range_label = f"{start.strftime('%b %d, %Y').upper()}  •  {end.strftime('%b %d, %Y').upper()}"
    pieces.append(
        f'''<path d="M24 177H836" stroke="#526487" stroke-opacity=".45"/>
  <text x="26" y="195" font-family="Trebuchet MS, Arial, sans-serif"
        font-size="9" letter-spacing="1.2" fill="#8DA6BC">{range_label}</text>
  <g font-family="Trebuchet MS, Arial, sans-serif" font-size="9"
     fill="#A5B7CE">
    <text x="565" y="195">LESS</text>'''
    )

    legend_x = 600
    legend_y = 187
    legend_levels = [
        ("#141D33", "#2C3B56"),
        ("#244858", "#38677A"),
        ("#286C78", "#3C9094"),
        ("#48A995", "#65C9B0"),
        ("#F4C653", "#FFF0D1"),
    ]
    for index, (fill, stroke) in enumerate(legend_levels):
        x = legend_x + index * 17
        pieces.append(
            f'<rect x="{x}" y="{legend_y}" width="10" height="10" rx="2" fill="{fill}" stroke="{stroke}" stroke-width=".7"/>'
        )
    pieces.append(
        '''<text x="692" y="195">MORE</text>
  </g>
</g>
<rect x=".7" y=".7" width="858.6" height="212.6" rx="13.3"
      fill="none" stroke="#526487" stroke-opacity=".85"/>
</svg>'''
    )

    return "\n".join(pieces)


def build_placeholder(start: dt.date, end: dt.date) -> str:
    """Create an honest, neutral preview until the first Actions sync succeeds."""
    return render_svg(start, end, None, {})


def main() -> None:
    start, end = date_window()
    calendar = fetch_calendar(start, end)
    total = int(calendar.get("totalContributions", -1))
    if total < 0:
        raise RuntimeError("GitHub returned an invalid totalContributions value.")

    days = collect_days(calendar, start, end)
    day_total = sum(item["count"] for item in days.values())
    if day_total != total:
        print(
            f"Warning: day sum ({day_total}) differs from GitHub calendar total "
            f"({total}); displaying GitHub's official calendar total.",
            file=sys.stderr,
        )

    svg = render_svg(start, end, total, days)
    # Validate the complete SVG before replacing the last known good artwork.
    import xml.etree.ElementTree as ET

    ET.fromstring(svg)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    print(
        f"Rendered {total:,} contributions for {LOGIN} "
        f"from {start.isoformat()} to {end.isoformat()} "
        f"across {len(grid_dates(start, end))} weeks."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Contribution maze update failed: {error}", file=sys.stderr)
        raise
