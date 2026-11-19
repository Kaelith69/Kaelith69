from __future__ import annotations

import datetime as dt
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_contribution_maze import (  # noqa: E402
    LEVEL_COLORS,
    arcade_route,
    calendar_weeks,
    collect_days,
    date_window,
    render_svg,
)


class ContributionArcadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = dt.date(2026, 1, 4)
        self.end = dt.date(2026, 1, 17)
        self.days = []
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
            self.days.append({
                "date": date.isoformat(),
                "contributionCount": count,
                "contributionLevel": level,
            })
        self.calendar = {
            "weeks": [
                {"contributionDays": self.days[:7]},
                {"contributionDays": self.days[7:]},
            ]
        }
        self.collected = collect_days(self.calendar, self.start, self.end)

    def test_date_window_is_exactly_365_days(self) -> None:
        start, end = date_window(dt.date(2026, 11, 19))
        self.assertEqual((end - start).days, 364)
        self.assertEqual((end - start).days + 1, 365)

    def test_collects_exact_days_and_levels(self) -> None:
        self.assertEqual(len(self.collected), 14)
        self.assertEqual(self.collected[self.start]["count"], 0)
        self.assertEqual(
            self.collected[self.start + dt.timedelta(days=4)]["level"],
            "FOURTH_QUARTILE",
        )

    def test_rejects_incomplete_or_duplicate_calendar(self) -> None:
        incomplete = {"weeks": [{"contributionDays": self.days[:-1]}]}
        with self.assertRaisesRegex(RuntimeError, "contains"):
            collect_days(incomplete, self.start, self.end)

        duplicate = {
            "weeks": [
                {"contributionDays": self.days},
                {"contributionDays": [self.days[0]]},
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "contains"):
            collect_days(duplicate, self.start, self.end)

    def test_rejects_unknown_level_and_negative_count(self) -> None:
        unknown = [dict(item) for item in self.days]
        unknown[1]["contributionLevel"] = "UNKNOWN"
        with self.assertRaisesRegex(RuntimeError, "Unknown contribution level"):
            collect_days({"weeks": [{"contributionDays": unknown}]}, self.start, self.end)

        negative = [dict(item) for item in self.days]
        negative[1]["contributionCount"] = -1
        with self.assertRaisesRegex(RuntimeError, "Negative contribution count"):
            collect_days({"weeks": [{"contributionDays": negative}]}, self.start, self.end)

    def test_calendar_grid_is_sunday_first(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        self.assertEqual(len(weeks), 2)
        self.assertEqual(weeks[0][0].weekday(), 6)
        self.assertEqual(weeks[0][0], self.start)
        self.assertEqual(weeks[1][6], self.end)

    def test_arcade_route_visits_each_day_once_and_stays_connected(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = arcade_route(weeks, self.start, self.end)
        self.assertEqual(len(route), 14)
        self.assertEqual({item[2] for item in route}, set(self.collected))

        for first, second in zip(route, route[1:]):
            column_delta = abs(first[0] - second[0])
            row_delta = abs(first[1] - second[1])
            self.assertEqual(column_delta + row_delta, 1)

        self.assertEqual(route[0][:2], (0, 0))
        self.assertEqual(route[-1][:2], (1, 6))

    def test_svg_is_valid_and_contains_only_real_date_cells(self) -> None:
        total = sum(item["count"] for item in self.collected.values())
        svg = render_svg(self.start, self.end, total, self.collected)
        root = ET.fromstring(svg)
        cells = root.findall(".//*[@data-date]")

        self.assertEqual(root.attrib["viewBox"], "0 0 860 226")
        self.assertEqual(len(cells), 14)
        self.assertEqual(cells[0].attrib["data-date"], self.start.isoformat())
        self.assertEqual(cells[-1].attrib["data-date"], self.end.isoformat())
        self.assertEqual(
            sum(int(cell.attrib["data-count"]) for cell in cells),
            total,
        )
        self.assertEqual(
            set(cell.attrib["data-level"] for cell in cells),
            set(LEVEL_COLORS),
        )

    def test_character_and_active_cells_animate(self) -> None:
        total = sum(item["count"] for item in self.collected.values())
        svg = render_svg(self.start, self.end, total, self.collected)
        root = ET.fromstring(svg)
        namespace = {"svg": "http://www.w3.org/2000/svg"}

        self.assertEqual(len(root.findall(".//svg:animateMotion", namespace)), 1)
        self.assertEqual(
            len(root.findall(".//svg:animate[@attributeName='fill-opacity']", namespace)),
            sum(item["count"] > 0 for item in self.collected.values()),
        )
        self.assertIn("PAC", svg.upper())
        self.assertIn("CONTRIBUTIONS", svg)
        self.assertIn(">42<", svg)
        self.assertNotIn("CONTRIBUTION MAZE", svg)
        self.assertNotIn("ghost", svg.lower())


if __name__ == "__main__":
    unittest.main()
