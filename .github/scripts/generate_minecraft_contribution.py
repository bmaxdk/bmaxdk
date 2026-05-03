#!/usr/bin/env python3
"""Generate a Minecraft-themed contribution SVG using REAL GitHub data.
Fetches the user's contribution calendar via GitHub's GraphQL API, maps daily
commit counts to Minecraft block types, and writes a self-contained animated
SVG with Steve walking across mining the blocks.
"""
import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ---------- Layout (matches GitHub contribution graph exactly) ----------
COLS = 53
ROWS = 7
CELL = 10
GAP = 3
PITCH = CELL + GAP            # 13
GRID_W = COLS * PITCH - GAP   # 686
GRID_H = ROWS * PITCH - GAP   # 88
WIDTH = 882
HEIGHT = 170
GRID_LEFT = (WIDTH - GRID_W) // 2  # 98
GRID_TOP = 50

# ---------- Animation timing ----------
TOTAL_DUR = 45.0
WALK_DUR = 42.0
START_X = -25
END_X = WIDTH + 5
STEVE_Y = GRID_TOP - 26

# ---------- GraphQL ----------
GRAPHQL_QUERY = """
query($username: String!) {
  user(login: $username) {
    contributionsCollection {
      contributionCalendar {
        weeks {
          contributionDays {
            contributionCount
            date
          }
        }
      }
    }
  }
}
"""

def fetch_contributions(username: str, token: str):
    """Return a 53x7 grid of (count, date) tuples from GitHub's GraphQL API."""
    payload = json.dumps({
        "query": GRAPHQL_QUERY,
        "variables": {"username": username},
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "minecraft-contribution-action",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    # Build a (col, row) -> count, date map. Grid columns = weeks, rows = day-of-week.
    grid = {}
    for c, week in enumerate(weeks):
        for day in week["contributionDays"]:
            d = datetime.strptime(day["date"], "%Y-%m-%d").date()
            r = (d.weekday() + 1) % 7  # GitHub puts Sunday on top (row 0)
            grid[(c, r)] = (day["contributionCount"], day["date"])
    return grid

def block_for_count(count: int) -> str | None:
    """Map a daily commit count to a Minecraft block type.

    GitHub itself uses 5 levels: 0 / 1-3 / 4-6 / 7-9 / 10+. We follow that, but
    add a 6th tier — 'ore' — for very heavy days, because diamond > diamond.
    """
    if count == 0:  return None        # empty cell (GitHub light gray)
    if count < 4:   return "grass1"
    if count < 7:   return "grass2"
    if count < 12:  return "grass3"
    if count < 20:  return "iron"
    if count < 30:  return "gold"
    return "diamond"

# ---------- SVG patterns (10x10, GitHub-cell-sized) ----------
DEFS = """
<defs>
  <pattern id="grass1" width="10" height="10" patternUnits="userSpaceOnUse">
    <rect width="10" height="10" fill="#3a6b2a"/>
    <rect x="0" y="0" width="10" height="2" fill="#4d8a3a"/>
    <rect x="0" y="2" width="10" height="1" fill="#2e5621"/>
    <rect x="3" y="6" width="1" height="1" fill="#4d8a3a"/>
    <rect x="7" y="7" width="1" height="1" fill="#2e5621"/>
  </pattern>
  <pattern id="grass2" width="10" height="10" patternUnits="userSpaceOnUse">
    <rect width="10" height="10" fill="#5DAB3F"/>
    <rect x="0" y="0" width="10" height="2" fill="#7BC85A"/>
    <rect x="0" y="2" width="10" height="1" fill="#3F7026"/>
    <rect x="2" y="5" width="1" height="1" fill="#7BC85A"/>
    <rect x="7" y="7" width="1" height="1" fill="#3F7026"/>
  </pattern>
  <pattern id="grass3" width="10" height="10" patternUnits="userSpaceOnUse">
    <rect width="10" height="10" fill="#7AC34F"/>
    <rect x="0" y="0" width="10" height="2" fill="#9BD96B"/>
    <rect x="0" y="2" width="10" height="1" fill="#5A9C36"/>
    <rect x="3" y="6" width="1" height="1" fill="#9BD96B"/>
    <rect x="7" y="7" width="1" height="1" fill="#5A9C36"/>
  </pattern>
  <pattern id="iron" width="10" height="10" patternUnits="userSpaceOnUse">
    <rect width="10" height="10" fill="#6D6D6D"/>
    <rect x="2" y="2" width="2" height="2" fill="#B8896A"/>
    <rect x="2" y="2" width="1" height="1" fill="#D4A88A"/>
    <rect x="6" y="6" width="2" height="2" fill="#B8896A"/>
  </pattern>
  <pattern id="gold" width="10" height="10" patternUnits="userSpaceOnUse">
    <rect width="10" height="10" fill="#6D6D6D"/>
    <rect x="2" y="2" width="2" height="2" fill="#D4A82A"/>
    <rect x="2" y="2" width="1" height="1" fill="#F0C84A"/>
    <rect x="6" y="6" width="2" height="2" fill="#D4A82A"/>
  </pattern>
  <pattern id="diamond" width="10" height="10" patternUnits="userSpaceOnUse">
    <rect width="10" height="10" fill="#6D6D6D"/>
    <rect x="2" y="2" width="2" height="2" fill="#4FBED1"/>
    <rect x="2" y="2" width="1" height="1" fill="#8FE0EE"/>
    <rect x="6" y="6" width="2" height="2" fill="#4FBED1"/>
  </pattern>
</defs>
"""

# Empty-cell color matching GitHub's contribution graph (light theme)
EMPTY_FILL = "#ebedf0"

PATTERN_FOR = {k: f"url(#{k})" for k in
               ["grass1", "grass2", "grass3", "iron", "gold", "diamond"]}

def build_svg(grid: dict) -> str:
    """grid is a dict (col, row) -> (count, date)."""
    blocks = []

    # Layer 1: ALL cells get a solid empty-style fill
    # Filled cells will draw on top of these in layer 2.
    for c in range(COLS):
        for r in range(ROWS):
            x = GRID_LEFT + c * PITCH
            y = GRID_TOP + r * PITCH
            blocks.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
                f'rx="2" fill="{EMPTY_FILL}"/>'
            )

    # Layer 2: filled blocks with mining animation, timed by Steve's column.
    rect_count = 0
    for c in range(COLS):
        for r in range(ROWS):
            count, _date = grid.get((c, r), (0, None))
            t = block_for_count(count)
            if t is None:
                continue
            x = GRID_LEFT + c * PITCH
            y = GRID_TOP + r * PITCH
            progress = (x + CELL/2 - START_X) / (END_X - START_X)
            mine_at = max(0.05, progress * WALK_DUR - 0.3)
            fade_dur = 0.5
            k1 = mine_at / TOTAL_DUR
            k2 = (mine_at + fade_dur) / TOTAL_DUR
            blocks.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" '
                f'fill="{PATTERN_FOR[t]}">'
                f'<animate attributeName="opacity" values="1;1;0;0" '
                f'keyTimes="0;{k1:.4f};{k2:.4f};1" dur="{TOTAL_DUR}s" repeatCount="indefinite"/>'
                f'<animate attributeName="y" values="{y};{y};{y+3};{y+3}" '
                f'keyTimes="0;{k1:.4f};{k2:.4f};1" dur="{TOTAL_DUR}s" repeatCount="indefinite"/>'
                f'</rect>'
            )
            rect_count += 1

    cells_svg = "<g id=\"grid\">" + "\n".join(blocks) + "</g>"

    ground_y = GRID_TOP + GRID_H + 2
    ground = (
        f'<g><rect x="0" y="{ground_y}" width="{WIDTH}" height="3" fill="#5C3E1F"/>'
        f'<rect x="0" y="{ground_y+3}" width="{WIDTH}" height="1" fill="#3D2817"/></g>'
    )

    steve = f'''<g id="steve"><g><g>
<rect x="2" y="0" width="9" height="2" fill="#2D1B0E"/>
<rect x="1" y="2" width="11" height="1" fill="#2D1B0E"/>
<rect x="1" y="3" width="11" height="6" fill="#E8B080"/>
<rect x="3" y="5" width="2" height="2" fill="#FFFFFF"/>
<rect x="4" y="5" width="1" height="2" fill="#3060B0"/>
<rect x="8" y="5" width="2" height="2" fill="#FFFFFF"/>
<rect x="9" y="5" width="1" height="2" fill="#3060B0"/>
<rect x="4" y="8" width="5" height="1" fill="#7B4F2C"/>
<rect x="1" y="9" width="11" height="7" fill="#2C9DAF"/>
<rect x="1" y="9" width="11" height="1" fill="#1F7888"/>
<rect x="1" y="16" width="11" height="1" fill="#3A2810"/>
<rect x="1" y="17" width="4" height="5" fill="#2D2570"/>
<rect x="8" y="17" width="4" height="5" fill="#2D2570"/>
<rect x="-1" y="9" width="2" height="7" fill="#E8B080"/>
<rect x="-1" y="9" width="2" height="1" fill="#1F7888"/>
<g><rect x="12" y="9" width="2" height="7" fill="#E8B080"/>
<rect x="12" y="9" width="2" height="1" fill="#1F7888"/>
<g transform="translate(14,12)">
<rect x="0" y="-1" width="1" height="9" fill="#7B4F2C"/>
<rect x="-2" y="-2" width="5" height="2" fill="#B8B8B8"/>
<rect x="-2" y="-2" width="5" height="1" fill="#D8D8D8"/>
<animateTransform attributeName="transform" type="rotate"
  values="-35 14 12; 25 14 12; -35 14 12" dur="0.6s" repeatCount="indefinite"/>
</g></g>
<animateTransform attributeName="transform" type="translate"
  values="0,0; 0,-1; 0,0" dur="0.6s" repeatCount="indefinite"/>
</g></g>
<animateTransform attributeName="transform" type="translate"
  values="{START_X},{STEVE_Y}; {END_X},{STEVE_Y}"
  dur="{WALK_DUR}s" repeatCount="indefinite"/></g>'''

    sparks = f'''<g id="sparks" opacity="0.75"><g>
<rect x="0" y="0" width="1" height="1" fill="#FFD060"/>
<rect x="2" y="-1" width="1" height="1" fill="#FFFFFF"/>
<rect x="-2" y="1" width="1" height="1" fill="#FFC040"/>
<animate attributeName="opacity"
  values="0;0.9;0;0;0.9;0;0;0.9;0" dur="{TOTAL_DUR}s" repeatCount="indefinite"/>
<animateTransform attributeName="transform" type="translate"
  values="{START_X+15},{STEVE_Y+11}; {END_X+15},{STEVE_Y+11}"
  dur="{WALK_DUR}s" repeatCount="indefinite"/>
</g></g>'''

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" '
        f'aria-label="Minecraft contribution graph">'
        f'<title>Minecraft contribution graph</title>'
        f'{DEFS}{cells_svg}{ground}{sparks}{steve}</svg>'
    )

def main():
    username = os.environ.get("GH_USERNAME")
    token = os.environ.get("GITHUB_TOKEN")
    out_path = os.environ.get("OUT_PATH", "minecraft-contribution.svg")
    if not username or not token:
        print("ERROR: GH_USERNAME and GITHUB_TOKEN must be set", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching contributions for {username}...")
    grid = fetch_contributions(username, token)
    filled = sum(1 for v in grid.values() if v[0] > 0)
    total = sum(v[0] for v in grid.values())
    print(f"  {filled}/{len(grid)} active days, {total} total contributions")

    svg = build_svg(grid)
    Path(out_path).write_text(svg)
    print(f"Wrote {out_path} ({len(svg):,} bytes)")

if __name__ == "__main__":
    main()
