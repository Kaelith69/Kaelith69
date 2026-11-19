from __future__ import annotations

import datetime as dt
import math
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_contribution_aquarium import (
    CELL,
    EAT_DWELL,
    GRID_X,
    GRID_Y,
    LAUNCH_POINT,
    LEVEL_COLORS,
    PITCH,
    RESET_FADE,
    approach_anchor,
    build_flight_plan,
    calendar_weeks,
    cell_center,
    collect_days,
    date_window,
    manhattan_distance,
    nearest_food_route,
    orthogonal_leg,
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

    def test_grid_uses_sunday_first_columns(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        self.assertEqual(weeks[0][0].weekday(), 6)
        self.assertEqual(weeks[0][0], self.start)
        self.assertEqual(weeks[-1][-1], self.end)

    def test_orthogonal_leg_never_returns_diagonal_segments(self) -> None:
        cases = [
            ((0, 0), (12, 9), "x"),
            ((0, 0), (12, 9), "y"),
            ((10, 20), (4, 7), "x"),
            ((10, 20), (4, 7), "y"),
            ((0, 0), (0, 9), "y"),
            ((0, 0), (12, 0), "x"),
        ]
        for start, end, axis in cases:
            points = [start, *orthogonal_leg(start, end, axis)]
            self.assertEqual(points[-1], end)
            for first, second in zip(points, points[1:]):
                self.assertTrue(
                    first[0] == second[0] or first[1] == second[1],
                    (start, end, axis, first, second),
                )

    def test_first_food_is_the_nearest_manhattan_target(self) -> None:
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
                manhattan_distance(LAUNCH_POINT, cell_center(item[0], item[1])),
                item[2].isoformat(),
            ),
        )
        self.assertEqual(route[0], expected)

    def test_each_next_food_is_nearest_from_the_fish_mouth_position(self) -> None:
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
                    manhattan_distance(current, cell_center(item[0], item[1])),
                    item[2].isoformat(),
                ),
            )
            self.assertEqual(actual, expected)
            current, _ = approach_anchor(current, cell_center(actual[0], actual[1]))
            remaining.remove(actual)

        self.assertEqual(remaining, [])

    def test_route_covers_every_active_date_once(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        active = {date for date, item in self.days.items() if item["count"] > 0}
        self.assertEqual({item[2] for item in route}, active)
        self.assertEqual(len(route), len(active))
        self.assertEqual(len({item[2] for item in route}), len(route))

    def test_flight_plan_contains_only_axis_aligned_segments(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        plan = build_flight_plan(route)
        self.assertGreater(len(plan["points"]), 2)
        for first, second in zip(plan["points"], plan["points"][1:]):
            self.assertTrue(
                first[0] == second[0] or first[1] == second[1],
                (first, second),
            )

    def test_flight_plan_has_a_pause_for_every_meal_and_a_full_reset(self) -> None:
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, self.days)
        plan = build_flight_plan(route)
        self.assertEqual(len(plan["arrivals"]), len(route))
        self.assertAlmostEqual(plan["cycle"] - plan["returned_at"], RESET_FADE)

        for arrival, eaten in plan["arrivals"].values():
            self.assertAlmostEqual(eaten - arrival, EAT_DWELL * .56)

        key_points = [float(value) for value in plan["key_points"].split(";")]
        key_times = [float(value) for value in plan["key_times"].split(";")]
        self.assertEqual(len(key_points), len(key_times))
        self.assertTrue(all(b > a for a, b in zip(key_times, key_times[1:])))
        self.assertTrue(all(b >= a for a, b in zip(key_points, key_points[1:])))
        self.assertAlmostEqual(key_times[0], 0)
        self.assertAlmostEqual(key_times[-1], 1)

    def test_empty_activity_has_a_valid_idle_loop(self) -> None:
        empty_days = {
            date: {"count": 0, "level": "NONE"}
            for date in self.days
        }
        weeks = calendar_weeks(self.start, self.end)
        route = nearest_food_route(weeks, self.start, self.end, empty_days)
        plan = build_flight_plan(route)
        self.assertEqual(route, [])
        self.assertEqual(plan["arrivals"], {})
        self.assertTrue(plan["path"])

    def test_svg_uses_creature_food_and_spaced_neon_colors(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        creatures = root.findall(".//*[@data-date]")
        self.assertEqual(root.attrib["viewBox"], "0 0 860 252")
        self.assertEqual(len(creatures), 14)
        self.assertEqual(
            sum(int(item.attrib["data-count"]) for item in creatures),
            total,
        )
        self.assertIn("#071B2D", svg)
        self.assertIn("#246BFF", svg)
        self.assertIn("#00B9FF", svg)
        self.assertIn("#00E0C8", svg)
        self.assertIn("#D34DFF", svg)
        self.assertIn("fish-body", svg)
        self.assertIn("fish-fin", svg)
        self.assertIn("animateMotion", svg)
        self.assertNotIn("rocket", svg.lower())
        self.assertNotIn("pacman", svg.lower())
        self.assertNotIn("ghost", svg.lower())
        self.assertEqual(PITCH - CELL, 5)
        self.assertEqual(GRID_X, 62)
        self.assertEqual(GRID_Y, 99)

    def test_only_active_creatures_disappear_and_all_share_the_reset(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        creatures = root.findall(".//*[@data-date]")
        animated = root.findall(".//svg:g/svg:animate[@attributeName='opacity']", ns)

        active_count = sum(item["count"] > 0 for item in self.days.values())
        self.assertEqual(len(animated), active_count)

        reset_values = {
            animation.attrib["keyTimes"].split(";")[-2]
            for animation in animated
        }
        self.assertEqual(len(reset_values), 1)

    def test_fish_jaw_uses_per_meal_keyframes(self) -> None:
        total = sum(item["count"] for item in self.days.values())
        svg = render_svg(self.start, self.end, total, self.days)
        root = ET.fromstring(svg)
        ns = {"svg": "http://www.w3.org/2000/svg"}
        jaw = root.findall(".//svg:path/svg:animate[@attributeName='d']", ns)
        self.assertEqual(len(jaw), 1)
        self.assertGreater(len(jaw[0].attrib["keyTimes"].split(";")), 2)


if __name__ == "__main__":
    unittest.main()
