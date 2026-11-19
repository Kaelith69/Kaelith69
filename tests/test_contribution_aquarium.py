from __future__ import annotations

import datetime as dt
import math
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_contribution_aquarium import (  # noqa: E402
    CELL,
    EAT_DWELL,
    FISH_NOSE_OFFSET,
    LAUNCH_POINT,
    LEVEL_COLORS,
    PITCH,
    RESET_FADE,
    SWIM_SPEED,
    calendar_weeks,
    cell_center,
    collect_days,
    date_window,
    fish_approach_points,
    flight_timeline,
    nearest_food_route,
    render_svg,
)


class ContributionAquariumTests(unittest.TestCase):
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

    def test_collects_dates_counts_and_intensity_levels(self) -> None:
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

    def test_calendar_grid_is_sunday_first(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        self.assertEqual(weeks[0][0].weekday(), 6)
        self.assertEqual(weeks[0][0], self.start)
        self.assertEqual(weeks[-1][-1], self.end)

    def test_fish_selects_the_nearest_food_first(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
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

    def test_each_next_food_is_the_nearest_unvisited_cell(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        remaining = [
            (column, row, date)
            for column, week in enumerate(weeks)
            for row, date in enumerate(week)
            if self.start <= date <= self.end and self.days[date]["count"] > 0
        ]
        current = LAUNCH_POINT

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

    def test_route_visits_every_active_day_once_and_no_empty_days(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        active_dates = {
            date for date, detail in self.days.items() if detail["count"] > 0
        }
        self.assertEqual({item[2] for item in route}, active_dates)
        self.assertEqual(len(route), len(active_dates))
        self.assertEqual(len({item[2] for item in route}), len(route))

    def test_fish_mouth_is_offset_to_reach_the_food_cell(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        points = fish_approach_points(route)
        self.assertEqual(len(points), len(route) + 2)

        for index, (column, row, _) in enumerate(route, start=1):
            target = cell_center(column, row)
            distance = math.dist(points[index], target)
            self.assertAlmostEqual(distance, FISH_NOSE_OFFSET, places=6)

    def test_timeline_pauses_at_every_meal_and_resets_after_return(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        timeline = flight_timeline(route)
        key_points = [float(value) for value in timeline["key_points"].split(";")]
        key_times = [float(value) for value in timeline["key_times"].split(";")]

        self.assertEqual(len(timeline["arrivals"]), len(route))
        self.assertEqual(len(key_points), 2 + 2 * len(route) + 1)
        self.assertTrue(all(b > a for a, b in zip(key_times, key_times[1:])))
        self.assertTrue(all(b >= a for a, b in zip(key_points, key_points[1:])))
        self.assertAlmostEqual(timeline["cycle"] - timeline["returned_at"], RESET_FADE)
        for arrival, eaten in timeline["arrivals"].values():
            self.assertAlmostEqual(eaten - arrival, EAT_DWELL)

    def test_empty_calendar_has_a_valid_stationary_fish_loop(self) -> None:
        empty_days = {
            date: {"count": 0, "level": "NONE"}
            for date in self.days
        }
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, empty_days)
        self.assertEqual(route, [])
        timeline = flight_timeline(route)
        self.assertTrue(timeline["path"])
        self.assertEqual(timeline["arrivals"], {})

    def test_svg_has_spaced_tiles_neon_palette_and_animated_fish(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        cells = root.findall(".//*[@data-date]")
        animated_cells = root.findall(".//svg:rect/svg:animate[@attributeName='opacity']", {
            "svg": "http://www.w3.org/2000/svg"
        })

        self.assertEqual(root.attrib["viewBox"], "0 0 860 244")
        self.assertEqual(len(cells), 14)
        self.assertEqual(len(animated_cells), sum(item["count"] > 0 for item in self.days.values()))
        self.assertIn("#07182A", svg)
        self.assertIn("#174BFF", svg)
        self.assertIn("#00A9FF", svg)
        self.assertIn("#FFFFFF", svg)
        self.assertIn("fish-body", svg)
        self.assertIn("animateMotion", svg)
        self.assertIn("repeatCount=\"indefinite\"", svg)
        self.assertNotIn("rocket", svg.lower())
        self.assertNotIn("pacman", svg.lower())
        self.assertNotIn("ghost", svg.lower())

        for cell in cells:
            self.assertEqual(float(cell.attrib["width"]), CELL)
            self.assertEqual(float(cell.attrib["height"]), CELL)
        self.assertEqual(PITCH - CELL, 5)

    def test_zero_count_cells_never_receive_eat_animation(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        animated = root.findall(".//svg:rect/svg:animate[@attributeName='opacity']", {
            "svg": "http://www.w3.org/2000/svg"
        })
        self.assertEqual(
            len(animated),
            sum(item["count"] > 0 for item in self.days.values()),
        )


if __name__ == "__main__":
    unittest.main()
