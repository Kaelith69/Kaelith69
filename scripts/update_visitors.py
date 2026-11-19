#!/usr/bin/env python3
"""Refresh the numeric visitor value embedded in assets/visitors.svg."""

from __future__ import annotations

import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "assets" / "visitors.svg"
BADGE_URL = os.environ.get(
    "VISITOR_BADGE_URL",
    "https://komarev.com/ghpvc/?username=Kaelith69&style=flat&label=VISITS&color=6C3D87",
)


def fetch_count() -> str:
    # Do not impersonate github-camo here. The counter only records a view
    # when its request User-Agent begins with github-camo.
    request = urllib.request.Request(
        BADGE_URL,
        headers={"User-Agent": "Kaelith69-visitor-svg-sync/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError("The visitor service did not return valid SVG.") from exc

    text_values = [
        "".join(element.itertext()).strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    ]

    # Komarev's flat badge renders its label twice, followed by the value twice.
    # The last text node is therefore the numeric view count.
    if not text_values:
        raise RuntimeError("Could not find text nodes in the visitor badge SVG.")

    candidate = text_values[-1]
    if not re.fullmatch(r"[0-9][0-9,]*", candidate):
        raise RuntimeError(
            f"Visitor badge returned an unexpected count value: {candidate!r}"
        )

    return f"{int(candidate.replace(',', '')):,}"


def update_svg(count: str) -> bool:
    source = SVG_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(<text\b[^>]*\bid=["\\']visitor-count["\\'][^>]*>)(.*?)(</text>)',
        re.DOTALL,
    )
    updated, replacements = pattern.subn(
        lambda match: f"{match.group(1)}{count}{match.group(3)}",
        source,
        count=1,
    )
    if replacements != 1:
        raise RuntimeError("Could not locate the visitor-count text in assets/visitors.svg.")

    if updated == source:
        print(f"Visitor count is already current: {count}")
        return False

    SVG_PATH.write_text(updated, encoding="utf-8")
    print(f"Updated embedded visitor count to {count}")
    return True


if __name__ == "__main__":
    try:
        update_svg(fetch_count())
    except Exception as error:  # Surface service or parsing failures in Actions logs.
        print(f"Visitor count update failed: {error}", file=sys.stderr)
        raise
