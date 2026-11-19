from __future__ import annotations

import datetime as dt
import math
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_contribution_maze import (  # noqa: E402
    LAUNCH_POINT,
    LEVEL_COLORS,
    calendar_weeks,
    cell_center,
    collect_days,
    date_window,
    nearest_neighbor_route,
    render_svg,
)


class NeonRocketContributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = dt.date(2026, 1, 4)
        self.end = dt.date(2026, 1, 17)
        self.raw_days = []
        for offset in range(14):
            date = self.start + dt.timedelta(days=offset)
            count = [0, 1, 2, 5, 9][offset % 5]
            level = (
                "NONE" if count == 0 else
                "FIRST_QUARTILE" if count == 1 else
                "SECOND_QUARTILE" if count == 2 else
                "THIRD_QUARTILE" if count == 5 else
                "FOURTH_QUARTILE"
            )
            self.raw_days.append({
                "date": date.isoformat(),
                "contributionCount": count,
                "contributionLevel": level,
            })
        self.calendar = {
            "weeks": [
                {"contributionDays": self.raw_days[:7]},
                {"contributionDays": self.raw_days[7:]},
            ]
        }
        self.days = collect_days(self.calendar, self.start, self.end)

    def test_date_window_is_exactly_365_days(self) -> None:
        start, end = date_window(dt.date(2026, 11, 19))
        self.assertEqual((end - start).days + 1, 365)

    def test_collects_dates_counts_and_levels(self) -> None:
        self.assertEqual(len(self.days), 14)
        self.assertEqual(self.days[self.start]["count"], 0)
        self.assertEqual(
            self.days[self.start + dt.timedelta(days=4)]["level"],
            "FOURTH_QUARTILE",
        )

    def test_rejects_incomplete_duplicate_negative_and_unknown_data(self) -> None:
        incomplete = {"weeks": [{"contributionDays": self.raw_days[:-1]}]}
        with self.assertRaisesRegex(RuntimeError, "contains"):
            collect_days(incomplete, self.start, self.end)

        duplicate = {
            "weeks": [
                {"contributionDays": self.raw_days},
                {"contributionDays": [self.raw_days[0]]},
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "Duplicate contribution day"):
            collect_days(duplicate, self.start, self.end)

        negative = [dict(item) for item in self.raw_days]
        negative[1]["contributionCount"] = -1
        with self.assertRaisesRegex(RuntimeError, "Negative contribution count"):
            collect_days({"weeks": [{"contributionDays": negative}]}, self.start, self.end)

        unknown = [dict(item) for item in self.raw_days]
        unknown[1]["contributionLevel"] = "UNKNOWN"
        with self.assertRaisesRegex(RuntimeError, "Unknown contribution level"):
            collect_days({"weeks": [{"contributionDays": unknown}]}, self.start, self.end)

    def test_calendar_is_sunday_first(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        self.assertEqual(weeks[0][0].weekday(), 6)
        self.assertEqual(weeks[0][0], self.start)
        self.assertEqual(weeks[-1][-1], self.end)

    def test_nearest_neighbor_starts_at_the_closest_active_cell(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_neighbor_route(weeks, self.start, self.end, self.days)
        active = [
            (column, row, date)
            for column, week in enumerate(weeks)
            for row, date in enumerate(week)
            if self.start <= date <= self.end and self.days[date]["count"] > 0
        ]
        expected = min(
            active,
            key=lambda item: (
                (cell_center(item[0], item[1])[0] - LAUNCH_POINT[0]) ** 2
                + (cell_center(item[0], item[1])[1] - LAUNCH_POINT[1]) ** 2,
                item[2].isoformat(),
            ),
        )
        self.assertEqual(route[0], expected)

    def test_every_next_stop_is_the_nearest_unvisited_active_cell(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_neighbor_route(weeks, self.start, self.end, self.days)
        remaining = [
            (column, row, date)
            for column, week in enumerate(weeks)
            for row, date in enumerate(week)
            if self.start <= date <= self.end and self.days[date]["count"] > 0
        ]
        current = LAUNCH_POINT
        self.assertEqual(len(route), len(remaining))

        for actual in route:
            expected = min(
                remaining,
                key=lambda item: (
                    (cell_center(item[0], item[1])[0] - current[0]) ** 2
                    + (cell_center(item[0], item[1])[1] - current[1]) ** 2,
                    item[2].isoformat(),
                ),
            )
            self.assertEqual(actual, expected)
            current = cell_center(actual[0], actual[1])
            remaining.remove(actual)

        self.assertEqual(remaining, [])

    def test_route_visits_only_active_dates_exactly_once(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_neighbor_route(weeks, self.start, self.end, self.days)
        active_dates = {
            date for date, detail in self.days.items() if detail["count"] > 0
        }
        self.assertEqual({item[2] for item in route}, active_dates)
        self.assertEqual(len(route), len(active_dates))
        self.assertEqual(len({item[2] for item in route}), len(route))

    def test_svg_is_valid_and_uses_neon_rocket_art_direction(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        cells = root.findall(".//*[@data-date]")
        active = root.findall(".//*[@data-count and @data-level]")

        self.assertEqual(root.attrib["viewBox"], "0 0 860 226")
        self.assertEqual(len(cells), 14)
        self.assertEqual(len(active), 14)
        self.assertEqual(
            sum(int(cell.attrib["data-count"]) for cell in cells),
            total,
        )
        self.assertEqual(
            len(root.findall(".//*[@data-level='FOURTH_QUARTILE']")),
            sum(item["level"] == "FOURTH_QUARTILE" for item in self.days.values()),
        )
        self.assertIn("#030711", svg)
        self.assertIn("#1748FF", svg)
        self.assertIn("#FFFFFF", svg)
        self.assertIn("#238CFF", svg)
        self.assertIn("rocket-shell", svg)
        self.assertIn("NEAREST", svg.upper()) is False
        self.assertNotIn("Pac-Man", svg)
        self.assertNotIn("GHOST", svg.upper())

    def test_rocket_and_active_cells_have_indefinite_animation(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        namespace = {"svg": "http://www.w3.org/2000/svg"}

        self.assertEqual(len(root.findall(".//svg:animateMotion", namespace)), 1)
        fill_animations = root.findall(
            ".//svg:animate[@attributeName='opacity']",
            namespace,
        )
        self.assertEqual(
            len(fill_animations),
            sum(item["count"] > 0 for item in self.days.values()),
        )
        self.assertIn('repeatCount="indefinite"', svg)


if __name__ == "__main__":
    unittest.main()
