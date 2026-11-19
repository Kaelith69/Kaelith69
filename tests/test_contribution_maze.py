from __future__ import annotations

import datetime as dt
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_contribution_maze import (  # noqa: E402
    collect_days,
    grid_dates,
    render_svg,
)


class ContributionMazeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = dt.date(2026, 1, 4)  # Sunday
        self.end = dt.date(2026, 1, 17)   # Saturday, two full weeks
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

    def test_collects_exact_days_and_levels(self) -> None:
        calendar = {
            "weeks": [
                {"contributionDays": self.days[:7]},
                {"contributionDays": self.days[7:]},
            ]
        }
        result = collect_days(calendar, self.start, self.end)
        self.assertEqual(len(result), 14)
        self.assertEqual(result[self.start]["count"], 0)
        self.assertEqual(result[self.start + dt.timedelta(days=4)]["level"], "FOURTH_QUARTILE")

    def test_rejects_incomplete_calendar(self) -> None:
        calendar = {"weeks": [{"contributionDays": self.days[:-1]}]}
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            collect_days(calendar, self.start, self.end)

    def test_grid_is_sunday_first_and_complete(self) -> None:
        weeks = grid_dates(self.start, self.end)
        self.assertEqual(len(weeks), 2)
        self.assertEqual(len(weeks[0]), 7)
        self.assertEqual(weeks[0][0], self.start)
        self.assertEqual(weeks[1][6], self.end)

    def test_svg_is_valid_and_contains_all_contribution_cells(self) -> None:
        calendar = {
            "weeks": [
                {"contributionDays": self.days[:7]},
                {"contributionDays": self.days[7:]},
            ]
        }
        result = collect_days(calendar, self.start, self.end)
        svg = render_svg(self.start, self.end, 48, result)
        root = ET.fromstring(svg)
        namespace = {"svg": "http://www.w3.org/2000/svg"}

        cells = root.findall(".//*[@data-date]")
        self.assertEqual(len(cells), 14)
        self.assertEqual(root.attrib["viewBox"], "0 0 860 214")
        self.assertEqual(
            len(root.findall(".//svg:animateMotion", namespace)),
            1,
        )
        self.assertIn("CONTRIBUTION MAZE", svg)
        self.assertIn(">48<", svg)
        self.assertIn("repeatCount=\"indefinite\"", svg)
        self.assertIn("FOURTH_QUARTILE", svg)


if __name__ == "__main__":
    unittest.main()
