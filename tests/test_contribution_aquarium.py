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
    GRID_X,
    GRID_Y,
    LAUNCH_POINT,
    LEVEL_COLORS,
    PITCH,
    RESET_FADE,
    RESET_HOLD,
    calendar_weeks,
    cell_center,
    collect_days,
    date_window,
    flight_timeline,
    nearest_food_route,
    orthogonal_flight_points,
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

    def test_rolling_window_is_exactly_365_days(self) -> None:
        start, end = date_window(dt.date(2026, 11, 19))
        self.assertEqual((end - start).days + 1, 365)

    def test_collects_exact_dates_counts_and_levels(self) -> None:
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

    def test_first_food_is_nearest_by_manhattan_distance(self) -> None:
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
                abs(cell_center(item[0], item[1])[0] - LAUNCH_POINT[0])
                + abs(cell_center(item[0], item[1])[1] - LAUNCH_POINT[1]),
                item[2].isoformat(),
            ),
        )
        self.assertEqual(route[0], expected)

    def test_every_subsequent_food_is_nearest_remaining(self) -> None:
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
                    abs(cell_center(item[0], item[1])[0] - current[0])
                    + abs(cell_center(item[0], item[1])[1] - current[1]),
                    item[2].isoformat(),
                ),
            )
            self.assertEqual(actual, expected)
            current = cell_center(actual[0], actual[1])
            remaining.remove(actual)

        self.assertEqual(remaining, [])

    def test_route_visits_only_active_days_once(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        active = {date for date, item in self.days.items() if item["count"] > 0}
        self.assertEqual({item[2] for item in route}, active)
        self.assertEqual(len(route), len(active))
        self.assertEqual(len({item[2] for item in route}), len(route))

    def test_every_flight_segment_is_horizontal_or_vertical(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        points, stop_indices = orthogonal_flight_points(route)
        self.assertEqual(len(stop_indices), len(route))
        self.assertGreater(len(points), len(route))
        for first, second in zip(points, points[1:]):
            horizontal = math.isclose(first[1], second[1], abs_tol=1e-8)
            vertical = math.isclose(first[0], second[0], abs_tol=1e-8)
            self.assertTrue(horizontal or vertical, (first, second))
            self.assertFalse(horizontal and vertical, (first, second))

    def test_flight_timeline_pauses_for_each_meal_then_resets(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        timeline = flight_timeline(route)

        self.assertEqual(len(timeline["arrivals"]), len(route))
        self.assertAlmostEqual(
            timeline["cycle"] - timeline["returned_at"],
            RESET_HOLD + RESET_FADE,
        )
        for arrival, eaten in timeline["arrivals"].values():
            self.assertAlmostEqual(eaten - arrival, EAT_DWELL)

        key_points = [float(value) for value in timeline["key_points"].split(";")]
        key_times = [float(value) for value in timeline["key_times"].split(";")]
        self.assertEqual(len(key_points), len(key_times))
        self.assertTrue(all(b > a for a, b in zip(key_times, key_times[1:])))
        self.assertTrue(all(b >= a for a, b in zip(key_points, key_points[1:])))
        self.assertAlmostEqual(key_times[0], 0)
        self.assertAlmostEqual(key_times[-1], 1)

    def test_empty_activity_has_a_valid_idle_path(self) -> None:
        empty = {
            date: {"count": 0, "level": "NONE"}
            for date in self.days
        }
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, empty)
        timeline = flight_timeline(route)
        self.assertEqual(route, [])
        self.assertEqual(timeline["arrivals"], {})
        self.assertTrue(timeline["path"])

    def test_each_day_is_a_small_fish_with_clear_spacing(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        creatures = root.findall(".//*[@data-date]")
        self.assertEqual(root.attrib["viewBox"], "0 0 860 258")
        self.assertEqual(len(creatures), 14)
        self.assertTrue(all(item.tag.endswith("g") for item in creatures))
        self.assertTrue(all(item.find("{http://www.w3.org/2000/svg}ellipse") is not None for item in creatures))
        self.assertTrue(all(item.find("{http://www.w3.org/2000/svg}path") is not None for item in creatures))
        self.assertEqual(PITCH - CELL, 5)
        self.assertEqual(GRID_X, 64)
        self.assertEqual(GRID_Y, 94)
        self.assertNotIn("data-date=", svg[svg.find("<rect"):svg.find("</svg>")])

    def test_neon_food_palette_and_large_fish_are_present(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        self.assertIn("#07182B", svg)
        self.assertIn("#174BFF", svg)
        self.assertIn("#00A9FF", svg)
        self.assertIn("#00D9C2", svg)
        self.assertIn("#CE42FF", svg)
        self.assertIn("fish-body", svg)
        self.assertIn("fish-fin", svg)
        self.assertIn("animateMotion", svg)
        self.assertNotIn("rocket", svg.lower())
        self.assertNotIn("pacman", svg.lower())
        self.assertNotIn("ghost", svg.lower())

    def test_only_active_food_disappears_and_all_food_resets_together(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        creatures = root.findall(".//*[@data-date]")
        animations = [
            creature.find("svg:animate[@attributeName='opacity']", ns)
            for creature in creatures
        ]
        animated = [item for item in animations if item is not None]
        active_count = sum(item["count"] > 0 for item in self.days.values())
        self.assertEqual(len(animated), active_count)

        reset_times = {
            animation.attrib["keyTimes"].split(";")[-2]
            for animation in animated
        }
        self.assertEqual(len(reset_times), 1)

    def test_jaw_opens_and_closes_once_for_each_meal(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        jaw = root.findall(".//svg:path/svg:animate[@attributeName='d']", ns)
        self.assertEqual(len(jaw), 1)
        values = jaw[0].attrib["values"].split(";")
        key_times = jaw[0].attrib["keyTimes"].split(";")
        self.assertEqual(len(values), len(key_times))
        self.assertEqual(len(values), 2 * sum(item["count"] > 0 for item in self.days.values()) + 2)


if __name__ == "__main__":
    unittest.main()
