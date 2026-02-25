from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parents[1]
JOKE_FILE = ROOT / "joke.svg"
TIME_FILE = ROOT / "time-region.svg"
RECENT_WORK_FILE = ROOT / "recent-work.svg"
USERNAME = "Kaelith69"

IST = timezone(timedelta(hours=5, minutes=30))

JOKES = [
    ("Why do programmers hate nature?", "It has too many bugs."),
    ("Why was the JavaScript file sad?", "It did not know how to Node."),
    ("Why did the dev go broke?", "Used up all cache."),
    ("Why do Python devs wear glasses?", "Because they cannot C."),
    ("Why did the function return early?", "It had a base case."),
    ("Why did the CSS developer panic?", "Specificity got out of hand."),
    ("Why was the terminal calm?", "It had shell control."),
    ("Why did the repo smile?", "It finally got a clean commit."),
]


def fetch_json(url: str) -> list[dict] | dict:
    req = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Kaelith69-profile-widget-updater",
        },
    )
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def replace_once(text: str, pattern: str, replacement: str) -> str:
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise ValueError(f"Pattern not found exactly once: {pattern}")
    return new_text


def season_for_month(month: int) -> tuple[str, str]:
    if month in (6, 7, 8, 9):
        return "🌧️ Monsoon", "Kerala Monsoon Season"
    if month in (10, 11):
        return "🌦️ Humid", "Kerala Retreating Monsoon"
    if month in (12, 1, 2):
        return "🌤️ Mild", "Kerala Mild Winter"
    return "☀️ Warm", "Kerala Summer Season"


def update_joke(now_ist: datetime) -> None:
    content = JOKE_FILE.read_text(encoding="utf-8")
    joke, punchline = JOKES[now_ist.timetuple().tm_yday % len(JOKES)]
    joke = html.escape(joke)
    punchline = html.escape(punchline)
    stamp = now_ist.strftime("%Y-%m-%d")

    content = replace_once(
        content,
        r"(<!-- Joke line -->\s*<text[^>]*>\s*)(.*?)(\s*</text>)",
        rf"\g<1>{joke}\g<3>",
    )
    content = replace_once(
        content,
        r"(<!-- Punchline -->\s*<text[^>]*>\s*)(.*?)(\s*</text>)",
        rf"\g<1>{punchline}\g<3>",
    )
    content = replace_once(
        content,
        r"(<text x=\"700\" y=\"105\"[^>]*>)(.*?)(</text>)",
        rf"\g<1>// rotates daily · {stamp}\g<3>",
    )

    JOKE_FILE.write_text(content, encoding="utf-8")


def update_time_region(now_ist: datetime) -> None:
    content = TIME_FILE.read_text(encoding="utf-8")

    date_text = now_ist.strftime("%d %b %Y").upper()
    tz_text = "IST · UTC+05:30"

    content = replace_once(
        content,
        r"(<text id=\"date-text\"[^>]*>)(.*?)(</text>)",
        rf"\g<1>{date_text}\g<3>",
    )
    content = replace_once(
        content,
        r"(<text id=\"tz-text\"[^>]*>)(.*?)(</text>)",
        rf"\g<1>{tz_text}\g<3>",
    )

    TIME_FILE.write_text(content, encoding="utf-8")


def update_recent_work(now_ist: datetime) -> None:
    repos: list[dict] = []
    commits: list[dict] = []

    try:
        repos_data = fetch_json(
            f"https://api.github.com/users/{USERNAME}/repos?sort=updated&per_page=6"
        )
        if isinstance(repos_data, list):
            for repo in repos_data:
                if repo.get("fork"):
                    continue
                repos.append(
                    {
                        "name": repo.get("name", "unknown"),
                        "stars": repo.get("stargazers_count", 0),
                        "updated": repo.get("updated_at", "")[:10],
                    }
                )
                if len(repos) == 3:
                    break
    except (URLError, HTTPError, TimeoutError, ValueError):
        repos = []

    try:
        events_data = fetch_json(
            f"https://api.github.com/users/{USERNAME}/events/public?per_page=30"
        )
        if isinstance(events_data, list):
            for event in events_data:
                if event.get("type") != "PushEvent":
                    continue
                repo_name = (event.get("repo") or {}).get("name", USERNAME)
                event_commits = (event.get("payload") or {}).get("commits", [])
                for commit in event_commits:
                    message = (commit.get("message") or "commit").split("\n", 1)[0]
                    commits.append(
                        {
                            "repo": repo_name.split("/", 1)[-1],
                            "msg": message,
                        }
                    )
                    if len(commits) == 3:
                        break
                if len(commits) == 3:
                    break
    except (URLError, HTTPError, TimeoutError, ValueError):
        commits = []

    if not repos:
        repos = [
            {"name": "No repo data", "stars": 0, "updated": "--"},
            {"name": "Check API rate", "stars": 0, "updated": "--"},
            {"name": "Will auto-refresh", "stars": 0, "updated": "--"},
        ]

    if not commits:
        commits = [
            {"repo": "No commit data", "msg": "Could not fetch recent push events"},
            {"repo": "Tip", "msg": "This widget refreshes daily via GitHub Actions"},
            {"repo": "Status", "msg": "Waiting for next successful run"},
        ]

    ts = now_ist.strftime("%Y-%m-%d %H:%M IST")

    repo_lines = []
    y = 66
    for index, repo in enumerate(repos[:3], start=1):
        name = html.escape(truncate(repo["name"], 20))
        stars = int(repo["stars"])
        updated = html.escape(repo["updated"])
        repo_lines.append(
            f"<text x=\"24\" y=\"{y}\" font-family=\"'Courier New',monospace\" font-size=\"12\" fill=\"#e6edf3\">{index}. {name}</text>"
        )
        repo_lines.append(
            f"<text x=\"330\" y=\"{y}\" font-family=\"'Courier New',monospace\" font-size=\"10\" fill=\"#00e5ff\" text-anchor=\"end\">★ {stars} · {updated}</text>"
        )
        y += 24

    commit_lines = []
    y = 66
    for item in commits[:3]:
        repo = html.escape(truncate(item["repo"], 15))
        msg = html.escape(truncate(item["msg"], 47))
        commit_lines.append(
            f"<text x=\"390\" y=\"{y}\" font-family=\"'Courier New',monospace\" font-size=\"10\" fill=\"#39ff14\">[{repo}]</text>"
        )
        commit_lines.append(
            f"<text x=\"470\" y=\"{y}\" font-family=\"'Courier New',monospace\" font-size=\"12\" fill=\"#e6edf3\">{msg}</text>"
        )
        y += 24

    svg = f"""<svg width=\"720\" height=\"150\" viewBox=\"0 0 720 150\" xmlns=\"http://www.w3.org/2000/svg\">
  <defs>
    <linearGradient id=\"neon-line\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"0\">
      <stop offset=\"0%\" stop-color=\"#00e5ff\" stop-opacity=\"0\"/>
      <stop offset=\"50%\" stop-color=\"#00e5ff\" stop-opacity=\"0.8\"/>
      <stop offset=\"100%\" stop-color=\"#39ff14\" stop-opacity=\"0\"/>
    </linearGradient>
  </defs>
  <rect width=\"720\" height=\"150\" fill=\"#0a0f1f\" rx=\"10\"/>
  <rect width=\"720\" height=\"150\" fill=\"none\" stroke=\"#21262d\" stroke-width=\"1\" rx=\"10\"/>
  <rect x=\"0\" y=\"0\" width=\"720\" height=\"34\" fill=\"#11172a\" rx=\"10\"/>
  <rect x=\"0\" y=\"24\" width=\"720\" height=\"10\" fill=\"#11172a\"/>
  <circle cx=\"20\" cy=\"17\" r=\"5\" fill=\"#f78166\"/>
  <circle cx=\"36\" cy=\"17\" r=\"5\" fill=\"#ffa657\"/>
  <circle cx=\"52\" cy=\"17\" r=\"5\" fill=\"#39ff14\"/>
  <text x=\"360\" y=\"22\" font-family=\"'Courier New',monospace\" font-size=\"11\" fill=\"#8b949e\" text-anchor=\"middle\" letter-spacing=\"2\">LATEST REPOS + COMMITS</text>
  <text x=\"696\" y=\"22\" font-family=\"'Courier New',monospace\" font-size=\"9\" fill=\"#00e5ff\" text-anchor=\"end\">{html.escape(ts)}</text>

  <text x=\"24\" y=\"44\" font-family=\"'Courier New',monospace\" font-size=\"10\" fill=\"#8b949e\" letter-spacing=\"1.5\">RECENT REPOSITORIES</text>
  <text x=\"390\" y=\"44\" font-family=\"'Courier New',monospace\" font-size=\"10\" fill=\"#8b949e\" letter-spacing=\"1.5\">RECENT COMMITS</text>

  <rect x=\"360\" y=\"40\" width=\"1\" height=\"96\" fill=\"#21262d\"/>
  <rect x=\"20\" y=\"138\" width=\"680\" height=\"1\" fill=\"url(#neon-line)\"/>

  {''.join(repo_lines)}
  {''.join(commit_lines)}
</svg>
"""

    RECENT_WORK_FILE.write_text(svg, encoding="utf-8")


def main() -> None:
    now_ist = datetime.now(IST)
    update_joke(now_ist)
    update_time_region(now_ist)
    update_recent_work(now_ist)
    print("Updated joke.svg, time-region.svg, and recent-work.svg")


if __name__ == "__main__":
    main()
