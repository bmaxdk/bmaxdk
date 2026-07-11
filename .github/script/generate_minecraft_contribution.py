#!/usr/bin/env python3
"""Generate a Minecraft-themed contribution SVG using REAL GitHub data.

Designed to run inside a GitHub Action. Reads:
  GITHUB_TOKEN   - auto-provided by Actions
  GH_USERNAME    - set in workflow env
  OUT_PATH       - output file path (default: ./minecraft-contribution.svg)
  GITHUB_RUN_ID  - used as the random seed, so every refresh rolls a new
                   weapon for Steve (the background and tiles stay fixed)

Fetches the user's contribution calendar via GitHub's GraphQL API, maps daily
commit counts to Minecraft block tiers, and writes a self-contained animated
SVG: Steve walks into the grid and mines every contribution block ONE BY ONE
(sweeping each row left to right) with a randomly-chosen weapon — each block
cracks, pops, and drops — then the world regenerates when the loop wraps.
"""
import os
import sys
import json
import random
import time
import urllib.request
import urllib.error
from datetime import datetime, date
from pathlib import Path

# =+= Layout (matches GitHub contribution graph exactly) =+=
COLS = 53
ROWS = 7
CELL = 10
GAP = 3
PITCH = CELL + GAP            # 13
GRID_W = COLS * PITCH - GAP   # 686
GRID_H = ROWS * PITCH - GAP   # 88

GRID_LEFT = 30
GRID_TOP = 36                 # room above for Steve
RIGHT_PAD = 30
WIDTH = GRID_LEFT + GRID_W + RIGHT_PAD       # 746
GROUND_Y = GRID_TOP + GRID_H + 2             # 126
HEIGHT = GROUND_Y + 24                       # 150 (ground strip + footer row)

# =+= Animation timing (per-block mining, like the original) =+=
TIME_ENTRY = 1.0      # Steve walks in from off-left
DWELL = 0.35          # time spent mining each block
HOP = 0.35            # slide from one block to the next
TIME_EXIT = 1.0       # walk out off-right after the last block

# =+= GraphQL =+=
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
    grid = {}
    for c, week in enumerate(weeks):
        for day in week["contributionDays"]:
            d = datetime.strptime(day["date"], "%Y-%m-%d").date()
            r = (d.weekday() + 1) % 7  # GitHub puts Sunday on top (row 0)
            grid[(c, r)] = (day["contributionCount"], day["date"])
    return grid

def tier_for_count(count: int):
    """Map a daily commit count to a block tier 0..5 (None = empty cell)."""
    if count == 0:  return None
    if count < 4:   return 0
    if count < 7:   return 1
    if count < 12:  return 2
    if count < 20:  return 3
    if count < 30:  return 4
    return 5

# +++++++======
# Pixel helpers
# +++++++======

def px(x, y, w, h, color, opacity=None):
    o = f' opacity="{opacity}"' if opacity is not None else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}"{o}/>'

def bevel(light, dark):
    """Minecraft directional light: bright top/left edge, dark bottom/right."""
    return (px(0, 0, 10, 1, light, 0.8) + px(0, 1, 1, 9, light, 0.45) +
            px(0, 9, 10, 1, dark, 0.8) + px(9, 1, 1, 8, dark, 0.45))

def tile(pid, inner):
    """A 10x10 texture that aligns to each cell's own bounding box."""
    return (f'<pattern id="{pid}" width="1" height="1" '
            f'patternUnits="objectBoundingBox">{inner}</pattern>')

def speckles(rng, colors, n, y_min=1, y_max=8):
    out = []
    for _ in range(n):
        out.append(px(rng.randint(1, 8), rng.randint(y_min, y_max), 1, 1,
                      rng.choice(colors)))
    return "".join(out)

# =+= generic texture builders (rng => textures differ every run) ----

def t_speckled(pid, rng, base, light, dark, speck, n=6):
    """Plain block: base color, random speckles, beveled edges."""
    return tile(pid, px(0, 0, 10, 10, base) + speckles(rng, speck, n)
                + bevel(light, dark))

ORE_SLOTS = [(1, 1), (5, 1), (2, 4), (6, 4), (1, 7), (5, 7), (7, 6), (3, 6)]

def t_ore(pid, rng, base, light, dark, speck, ore, ore_light, ore_dark, blobs=4):
    """Classic ore texture: stone base with 2x2 ore blobs + facet highlights."""
    inner = [px(0, 0, 10, 10, base), speckles(rng, speck, 4)]
    for bx, by in rng.sample(ORE_SLOTS, blobs):
        inner.append(px(bx, by, 2, 2, ore))
        inner.append(px(bx, by, 1, 1, ore_light))
        inner.append(px(bx + 1, by + 1, 1, 1, ore_dark))
    inner.append(bevel(light, dark))
    return tile(pid, "".join(inner))

def t_grass_side(pid, rng, grass, grass_light, grass_dark,
                 dirt, dirt_light, dirt_dark):
    """Grass block side view: green cap with jagged fringe over dirt."""
    inner = [px(0, 0, 10, 10, dirt),
             speckles(rng, [dirt_light, dirt_dark], 5, y_min=4),
             px(0, 0, 10, 3, grass), px(0, 0, 10, 1, grass_light)]
    for x in range(10):
        if rng.random() < 0.5:
            inner.append(px(x, 3, 1, 1, grass_dark))
    inner.append(bevel(grass_light, dirt_dark))
    return tile(pid, "".join(inner))

# +++++++======
# Biome themes - tier order is 0 (lightest activity) .. 5 (heaviest)
# +++++++======

STONE = dict(base="#7F7F7F", light="#9C9C9C", dark="#5B5B5B",
             speck=["#6E6E6E", "#909090"])
DEEPSLATE = dict(base="#47474F", light="#5E5E68", dark="#2E2E34",
                 speck=["#3A3A40", "#585862"])
NETHERRACK = dict(base="#6B2F2C", light="#82443F", dark="#471A17",
                  speck=["#58211E", "#7E403C"])

def _ore(pid, rng, stone, ore, ore_light, ore_dark, blobs=4):
    return t_ore(pid, rng, stone["base"], stone["light"], stone["dark"],
                 stone["speck"], ore, ore_light, ore_dark, blobs)

def tiles_overworld(rng, v):
    return [
        # dirt
        t_speckled(f"t0{v}", rng, "#7A5636", "#8D6844", "#4A3018",
                   ["#5C3E22", "#96714A"], 7),
        # grass block (side)
        t_grass_side(f"t1{v}", rng, "#4F8A30", "#61A83C", "#3B6C22",
                     "#7A5636", "#96714A", "#5C3E22"),
        # grass (top view, full green)
        t_speckled(f"t2{v}", rng, "#6BBF45", "#8ADF60", "#4E9A2F",
                   ["#5AAB38", "#7ED456"], 8),
        _ore(f"t3{v}", rng, STONE, "#D8AF93", "#EDD1BC", "#A9765C"),        # iron
        _ore(f"t4{v}", rng, STONE, "#FBD23F", "#FEF08A", "#C9930B"),        # gold
        _ore(f"t5{v}", rng, STONE, "#4AEDE0", "#A5F9F1", "#1FB8A9", 5),     # diamond
    ]

def tiles_deepslate(rng, v):
    return [
        t_speckled(f"t0{v}", rng, DEEPSLATE["base"], DEEPSLATE["light"],
                   DEEPSLATE["dark"], DEEPSLATE["speck"], 7),
        _ore(f"t1{v}", rng, DEEPSLATE, "#2C2C30", "#4A4A50", "#131316"),    # coal
        _ore(f"t2{v}", rng, DEEPSLATE, "#C1765A", "#E39B7B", "#8E4E36"),    # copper
        _ore(f"t3{v}", rng, DEEPSLATE, "#E33B2E", "#FF7A6B", "#9E1F14"),    # redstone
        _ore(f"t4{v}", rng, DEEPSLATE, "#35D07A", "#7FF0AE", "#1B9450"),    # emerald
        _ore(f"t5{v}", rng, DEEPSLATE, "#4AEDE0", "#A5F9F1", "#1FB8A9", 5), # diamond
    ]

def t_magma(pid, rng):
    inner = [px(0, 0, 10, 10, "#3A1A12"), speckles(rng, ["#2A100A", "#4A2418"], 4)]
    for _ in range(3):
        x, y = rng.randint(1, 6), rng.randint(2, 8)
        inner.append(px(x, y, 3, 1, "#E85D1F"))
        inner.append(px(x + 1, y, 1, 1, "#FFD07A"))
    inner.append(bevel("#5C2E1E", "#1E0C08"))
    return tile(pid, "".join(inner))

def t_glowstone(pid, rng):
    inner = [px(0, 0, 10, 10, "#C99A3C"),
             speckles(rng, ["#F5C96B", "#FBE39A", "#9E7426", "#E0B04E"], 14)]
    inner.append(bevel("#FBE39A", "#8A6420"))
    return tile(pid, "".join(inner))

def t_debris(pid, rng):
    inner = [px(0, 0, 10, 10, "#4E3B31"), speckles(rng, ["#3C2C24", "#5E483C"], 5)]
    for bx, by in rng.sample(ORE_SLOTS, 3):
        inner.append(px(bx, by, 2, 1, "#8A6A4F"))
        inner.append(px(bx, by + 1, 1, 1, "#6B5140"))
    inner.append(bevel("#66503F", "#2E211A"))
    return tile(pid, "".join(inner))

def tiles_nether(rng, v):
    return [
        t_speckled(f"t0{v}", rng, NETHERRACK["base"], NETHERRACK["light"],
                   NETHERRACK["dark"], NETHERRACK["speck"], 7),
        t_magma(f"t1{v}", rng),
        _ore(f"t2{v}", rng, NETHERRACK, "#E8E0D8", "#FBF7F2", "#B8AFA4"),   # quartz
        _ore(f"t3{v}", rng, NETHERRACK, "#FBD23F", "#FEF08A", "#C9930B"),   # nether gold
        t_debris(f"t4{v}", rng),                                            # ancient debris
        t_glowstone(f"t5{v}", rng),
    ]

def t_chorus(pid, rng):
    inner = [px(0, 0, 10, 10, "#9B6BAD"), speckles(rng, ["#7E4E92", "#B588C4"], 5)]
    for bx, by in rng.sample(ORE_SLOTS, 3):
        inner.append(px(bx, by, 2, 2, "#E8DFF0"))
        inner.append(px(bx + 1, by + 1, 1, 1, "#C4A8D8"))
    inner.append(bevel("#C49AD4", "#6B3F7C"))
    return tile(pid, "".join(inner))

def t_ender_eye(pid, rng):
    inner = [px(0, 0, 10, 10, "#1F4A42"), speckles(rng, ["#163832", "#2A5C52"], 4)]
    for bx, by in rng.sample(ORE_SLOTS, 4):
        inner.append(px(bx, by, 2, 2, "#6FE05A"))
        inner.append(px(bx, by, 1, 1, "#B4F5A6"))
    inner.append(bevel("#3A7A6A", "#0E2A24"))
    return tile(pid, "".join(inner))

def t_crystal(pid, rng):
    inner = [px(0, 0, 10, 10, "#F0E4FA"),
             speckles(rng, ["#FFFFFF", "#E39BE3", "#D9C2EE"], 10)]
    for bx, by in rng.sample(ORE_SLOTS, 3):
        inner.append(px(bx, by, 2, 2, "#FF7AD9"))
        inner.append(px(bx, by, 1, 1, "#FFFFFF"))
    inner.append(bevel("#FFFFFF", "#C9A7E0"))
    return tile(pid, "".join(inner))

def tiles_end(rng, v):
    return [
        t_speckled(f"t0{v}", rng, "#1A1226", "#322046", "#0A0610",
                   ["#2E2140", "#0D0916", "#4A2E6B"], 6),                   # obsidian
        t_speckled(f"t1{v}", rng, "#D6D29C", "#EDEBC2", "#A5A26C",
                   ["#B9B57F", "#EAE7B8"], 7),                              # end stone
        t_speckled(f"t2{v}", rng, "#A272A2", "#C09AC0", "#744874",
                   ["#8A5A8A", "#BC8FBC"], 6),                              # purpur
        t_chorus(f"t3{v}", rng),
        t_ender_eye(f"t4{v}", rng),
        t_crystal(f"t5{v}", rng),
    ]

# =+= skies, decorations, grounds =+==+==+=--

def gradient(gid, top, bottom):
    return (f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{top}"/>'
            f'<stop offset="100%" stop-color="{bottom}"/></linearGradient>')

def sky_rect(gid):
    return f'<rect x="0" y="0" width="{WIDTH}" height="{GROUND_Y}" fill="url(#{gid})"/>'

def twinkles(rng, n, colors, y_max=28):
    out = []
    for _ in range(n):
        sx, sy = rng.randint(6, WIDTH - 6), rng.randint(2, y_max)
        dur = rng.uniform(2.8, 5.5)
        out.append(f'<rect x="{sx}" y="{sy}" width="1" height="1" '
                   f'fill="{rng.choice(colors)}">'
                   f'<animate attributeName="opacity" values="0.25;1;0.25" '
                   f'dur="{dur:.1f}s" repeatCount="indefinite"/></rect>')
    return "".join(out)

def cloud_shape():
    return (px(0, 2, 6, 2, "#FFFFFF") + px(2, 0, 8, 2, "#FFFFFF") +
            px(0, 4, 14, 2, "#FFFFFF") + px(4, 6, 8, 2, "#FFFFFF") +
            px(2, 6, 2, 2, "#E6E6F0") + px(12, 4, 2, 2, "#E6E6F0"))

def sky_overworld(rng):
    defs = gradient("sky", "#0B1A3A", "#1E2F55")
    body = [sky_rect("sky"), twinkles(rng, 20, ["#FFFFFF", "#D8E2FF"])]
    # crescent moon, top-right
    mx, my = WIDTH - 28, 5
    body.append(
        px(mx + 4, my, 12, 2, "#E8E8F0") + px(mx + 2, my + 2, 16, 2, "#E8E8F0") +
        px(mx, my + 4, 20, 12, "#E8E8F0") + px(mx + 2, my + 16, 16, 2, "#E8E8F0") +
        px(mx + 4, my + 18, 12, 2, "#E8E8F0") +
        px(mx + 8, my, 8, 2, "#1E2F55") + px(mx + 8, my + 2, 10, 2, "#1E2F55") +
        px(mx + 8, my + 4, 12, 10, "#1E2F55") +
        px(mx + 4, my + 6, 2, 2, "#FFFFFF") + px(mx + 2, my + 10, 2, 2, "#FFFFFF"))
    # drifting pixel clouds
    for i in range(3):
        cy = rng.randint(3, 20)
        dur = rng.randint(55, 105)
        begin = -rng.randint(0, dur)
        body.append(
            f'<g opacity="{rng.uniform(0.6, 0.9):.2f}" transform="translate(0,{cy})">'
            f'<g>{cloud_shape()}'
            f'<animateTransform attributeName="transform" type="translate" '
            f'values="-30,0; {WIDTH + 30},0" dur="{dur}s" begin="{begin}s" '
            f'repeatCount="indefinite"/></g></g>')
    return defs, "".join(body)

def sky_deepslate(rng):
    defs = gradient("sky", "#15151B", "#26262E")
    body = [sky_rect("sky"),
            twinkles(rng, 12, ["#B57EDC", "#C9A7F5", "#8FD0E0"], y_max=26)]
    # hanging stalactites along the top edge
    for _ in range(9):
        sx = rng.randint(10, WIDTH - 14)
        body.append(px(sx, 0, 4, rng.randint(2, 4), "#3C3C44") +
                    px(sx + 1, 3, 2, rng.randint(2, 3), "#34343C") +
                    px(sx + 1, 5, 1, rng.randint(1, 3), "#2C2C33"))
    return defs, "".join(body)

def sky_nether(rng):
    defs = gradient("sky", "#2A0C0A", "#4A1610")
    body = [sky_rect("sky")]
    # glowstone cluster, top-right
    gx = WIDTH - 34
    body.append(px(gx, 0, 14, 4, "#C99A3C") + px(gx + 2, 4, 10, 3, "#E0B04E") +
                px(gx + 4, 7, 5, 2, "#F5C96B") + px(gx + 3, 2, 3, 2, "#FBE39A"))
    # rising embers
    for _ in range(12):
        ex = rng.randint(8, WIDTH - 8)
        dur = rng.uniform(5.5, 9.5)
        begin = -rng.uniform(0, dur)
        rise = rng.randint(60, GROUND_Y - 14)
        c = rng.choice(["#FF9A3C", "#FFC96B", "#FF6A00"])
        body.append(
            f'<g><rect x="{ex}" y="{GROUND_Y - 4}" width="1" height="1" fill="{c}">'
            f'<animate attributeName="opacity" values="0;0.9;0.7;0" '
            f'keyTimes="0;0.15;0.7;1" dur="{dur:.1f}s" begin="{begin:.1f}s" '
            f'repeatCount="indefinite"/></rect>'
            f'<animateTransform attributeName="transform" type="translate" '
            f'values="0,0; {rng.randint(-8, 8)},{-rise}" dur="{dur:.1f}s" '
            f'begin="{begin:.1f}s" repeatCount="indefinite"/></g>')
    return defs, "".join(body)

def sky_end(rng):
    defs = gradient("sky", "#0D0716", "#1C1128")
    body = [sky_rect("sky")]
    for _ in range(3):  # faint nebula wisps
        nx, ny = rng.randint(20, WIDTH - 90), rng.randint(4, 22)
        body.append(px(nx, ny, rng.randint(40, 80), rng.randint(6, 12),
                       "#6B3F9E", 0.07))
    body.append(twinkles(rng, 26, ["#FFFFFF", "#C9A7F5", "#E39BE3"], y_max=30))
    return defs, "".join(body)

def ground_overworld(rng):
    out = [px(0, GROUND_Y, WIDTH, 3, "#5C3E1F"), px(0, GROUND_Y + 3, WIDTH, 1, "#3D2817")]
    for _ in range(24):
        out.append(px(rng.randint(0, WIDTH - 2), GROUND_Y, 2, 1, "#4D8A3A"))
    return "".join(out)

def ground_deepslate(rng):
    out = [px(0, GROUND_Y, WIDTH, 3, "#33333B"), px(0, GROUND_Y + 3, WIDTH, 1, "#1E1E24")]
    for _ in range(18):
        out.append(px(rng.randint(0, WIDTH - 2), GROUND_Y + 1, 1, 1, "#4A4A54"))
    return "".join(out)

def ground_nether(rng):
    out = [px(0, GROUND_Y, WIDTH, 3, "#3A1410"), px(0, GROUND_Y + 3, WIDTH, 1, "#200A08")]
    for _ in range(14):  # lava seams glowing through the netherrack
        lx = rng.randint(0, WIDTH - 4)
        out.append(f'<rect x="{lx}" y="{GROUND_Y + 1}" width="{rng.randint(2, 4)}" '
                   f'height="1" fill="#FF7A2A">'
                   f'<animate attributeName="opacity" values="0.3;1;0.3" '
                   f'dur="{rng.uniform(1.4, 3.2):.1f}s" repeatCount="indefinite"/></rect>')
    return "".join(out)

def ground_end(rng):
    out = [px(0, GROUND_Y, WIDTH, 3, "#3A3450"), px(0, GROUND_Y + 3, WIDTH, 1, "#232038")]
    for _ in range(16):
        out.append(px(rng.randint(0, WIDTH - 2), GROUND_Y + 1, 1, 1, "#5A5278"))
    return "".join(out)

THEMES = {
    "overworld": dict(display="Overworld", tiles=tiles_overworld,
                      sky=sky_overworld, ground=ground_overworld,
                      empty=("#9098B0", "0.22"),
                      sparks=["#FFD060", "#FFFFFF", "#FFC040"]),
    "deepslate": dict(display="Deepslate Caves", tiles=tiles_deepslate,
                      sky=sky_deepslate, ground=ground_deepslate,
                      empty=("#9898A4", "0.20"),
                      sparks=["#C9A7F5", "#FFFFFF", "#B57EDC"]),
    "nether":    dict(display="The Nether", tiles=tiles_nether,
                      sky=sky_nether, ground=ground_nether,
                      empty=("#B89090", "0.20"),
                      sparks=["#FF9A3C", "#FFE08A", "#FF6A00"]),
    "end":       dict(display="The End", tiles=tiles_end,
                      sky=sky_end, ground=ground_end,
                      empty=("#A492C0", "0.20"),
                      sparks=["#E39BE3", "#FFFFFF", "#B57EDC"]),
}

# +++++++======
# Weapons - drawn in Steve's hand frame (origin = grip, pivot for swing)
# +++++++======

WOOD, WOOD_L = "#8B5A2B", "#A06A38"
MATS = {
    "iron":      dict(main="#C8C8C8", light="#EDEDED", dark="#8F8F8F"),
    "gold":      dict(main="#F2D63F", light="#FBF08A", dark="#C9971B"),
    "diamond":   dict(main="#3EE1CF", light="#9FF7EC", dark="#1FA396"),
    "netherite": dict(main="#5A525C", light="#8B8290", dark="#3A343C"),
}

def weapon_pickaxe(m):
    return (px(0, -2, 2, 10, WOOD) + px(0, -2, 1, 10, WOOD_L) +
            px(-3, -4, 8, 3, m["main"]) + px(-3, -4, 8, 1, m["light"]) +
            px(-4, -3, 1, 2, m["main"]) + px(5, -3, 1, 2, m["main"]) +
            px(-5, -3, 1, 1, m["dark"]) + px(6, -3, 1, 1, m["dark"]))

def weapon_axe(m):
    return (px(0, -3, 2, 11, WOOD) + px(0, -3, 1, 11, WOOD_L) +
            px(-1, -5, 6, 2, m["main"]) + px(-1, -5, 6, 1, m["light"]) +
            px(3, -3, 2, 3, m["main"]) + px(4, -3, 1, 3, m["dark"]) +
            px(3, 0, 2, 1, m["dark"]))

def weapon_sword(m):
    parts = [px(-2, 0, 5, 1, "#4A3B2A"),                 # guard
             px(0, 1, 2, 4, "#6B4226"), px(0, 4, 2, 1, "#513018")]  # grip + pommel
    for i in range(6):                                   # diagonal blade
        parts.append(px(1 + i, -2 - i, 2, 2, m["main"]))
        parts.append(px(1 + i, -2 - i, 1, 1, m["light"]))
    parts.append(px(7, -8, 1, 1, m["light"]))            # tip
    return "".join(parts)

def weapon_trident():
    main, light = "#33B5A6", "#7FE3D6"
    return (px(0, -2, 2, 10, "#2E8B77") + px(0, -2, 1, 10, "#3FA98F") +
            px(-2, -3, 6, 1, main) +
            px(-2, -6, 1, 3, main) + px(3, -6, 1, 3, main) +
            px(0, -7, 2, 4, main) + px(0, -7, 1, 4, light) +
            px(-2, -6, 1, 1, light) + px(3, -6, 1, 1, light))

def enchant_glint():
    return ('<g>' + px(2, -4, 1, 1, "#D9A7FF") + px(-1, -2, 1, 1, "#B57EDC") +
            px(4, -6, 1, 1, "#E8C9FF") + px(1, -7, 1, 1, "#D9A7FF") +
            '<animate attributeName="opacity" values="0.15;0.95;0.15" '
            'dur="1.4s" repeatCount="indefinite"/></g>')

SWING = {  # (rotate keyframes, swing duration)
    "pick":    ("-35;25;-35", 0.75),
    "sword":   ("-55;35;-55", 0.55),
    "trident": ("-25;35;-25", 0.60),
}

WEAPONS = {
    "iron_pickaxe":    ("Iron Pickaxe",    lambda: weapon_pickaxe(MATS["iron"]),      "pick"),
    "diamond_pickaxe": ("Diamond Pickaxe", lambda: weapon_pickaxe(MATS["diamond"]),   "pick"),
    "golden_axe":      ("Golden Axe",      lambda: weapon_axe(MATS["gold"]),          "pick"),
    "netherite_axe":   ("Netherite Axe",   lambda: weapon_axe(MATS["netherite"]),     "pick"),
    "diamond_sword":   ("Diamond Sword",   lambda: weapon_sword(MATS["diamond"]),     "sword"),
    "netherite_sword": ("Netherite Sword", lambda: weapon_sword(MATS["netherite"]),   "sword"),
    "golden_sword":    ("Golden Sword",    lambda: weapon_sword(MATS["gold"]),        "sword"),
    "trident":         ("Trident",         weapon_trident,                            "trident"),
}

def crack_pattern(rng):
    """Destroy-stage crack overlay: a web of dark pixels radiating outward."""
    pts = {(5, 5), (4, 4), (3, 3), (6, 6), (7, 7), (6, 4), (7, 3),
           (4, 6), (3, 7), (5, 2), (5, 8), (2, 5), (8, 5)}
    for _ in range(4):
        pts.add((rng.randint(1, 8), rng.randint(1, 8)))
    inner = "".join(px(x, y, 1, 1, "#111111", 0.85) for x, y in sorted(pts))
    return tile("crackpat", inner)

def steve_svg(weapon_inner, swing_values, swing_dur, walk_values, walk_keytimes,
              total_dur):
    return f'''<g id="steve"><g><g>
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
<g transform="translate(14,12)"><g>
{weapon_inner}
<animateTransform attributeName="transform" type="rotate"
  values="{swing_values}" dur="{swing_dur}s" repeatCount="indefinite"/>
</g></g></g>
<animateTransform attributeName="transform" type="translate"
  values="0,0; 0,-1; 0,0" dur="{swing_dur}s" repeatCount="indefinite"/>
</g></g>
<animateTransform attributeName="transform" type="translate"
  values="{walk_values}" keyTimes="{walk_keytimes}"
  dur="{total_dur}s" repeatCount="indefinite"/></g>'''

# +++++++======
# SVG assembly
# +++++++======

def choose_style(rng):
    # Biome is pinned to the classic night Overworld so the background looks
    # the same every refresh; only the weapon (and enchantment) rolls fresh.
    weapon_key = rng.choice(sorted(WEAPONS))
    enchanted = rng.random() < 0.3
    return dict(theme="overworld", weapon=weapon_key, enchanted=enchanted)

def build_svg(grid: dict, style: dict, rng: random.Random) -> str:
    """grid: (col, row) -> (count, date). style: from choose_style()."""
    theme = THEMES[style["theme"]]
    weapon_name, weapon_fn, swing_key = WEAPONS[style["weapon"]]
    if style["enchanted"]:
        weapon_name = f"Enchanted {weapon_name}"
    swing_values, swing_dur = SWING[swing_key]

    # Environment (tiles, sky, ground, cracks) uses a FIXED seed so the
    # background renders identically on every refresh; the run-seeded rng
    # above only influenced the weapon roll in choose_style().
    rng = random.Random("minecraft-env-v1")

    # --- tile textures: two random variants per tier, rolled fresh each run
    tier_defs, tier_pids = [], []
    for v in ("a", "b"):
        variant = theme["tiles"](rng, v)
        tier_defs.extend(variant)
    tier_pids = [[f"t{t}a", f"t{t}b"] for t in range(6)]

    sky_defs, sky_body = theme["sky"](rng)
    defs = "<defs>" + "".join(tier_defs) + crack_pattern(rng) + sky_defs + "</defs>"

    empty_fill, empty_op = theme["empty"]

    # --- Layer 1: faint base cells (visible on light AND dark GitHub themes)
    blocks = []
    for c in range(COLS):
        for r in range(ROWS):
            x = GRID_LEFT + c * PITCH
            y = GRID_TOP + r * PITCH
            blocks.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
                          f'rx="2" fill="{empty_fill}" opacity="{empty_op}"/>')

    # --- Collect filled cells in ROW-MAJOR order: Steve visits every
    #     contribution one by one, sweeping each row left->right.
    filled = []
    for r in range(ROWS):
        for c in range(COLS):
            count, _d = grid.get((c, r), (0, None))
            t = tier_for_count(count)
            if t is not None:
                filled.append((c, r, t))
    n = len(filled)

    # Steve stands just left of a block, weapon arm reaching into it
    # (pickaxe pivot is local (14,12); body height-center is local y=11).
    def steve_pos(c, r):
        bx = GRID_LEFT + c * PITCH
        by = GRID_TOP + r * PITCH
        return (bx - 17, by + CELL // 2 - 11)

    off_left = (-30, GRID_TOP + 3 * PITCH - 11)
    off_right = (WIDTH + 20, GRID_TOP + 3 * PITCH - 11)

    if n == 0:
        # nothing to mine - Steve just crosses the grid once
        total_dur = 4.0
        path = [off_left, off_right]
        times = [0.0, 1.0]
    else:
        total_dur = TIME_ENTRY + n * DWELL + (n - 1) * HOP + TIME_EXIT
        path, times = [off_left], [0.0]
        for i in range(n):
            arrive = TIME_ENTRY + i * (DWELL + HOP)
            xy = steve_pos(filled[i][0], filled[i][1])
            path.append(xy); times.append(arrive / total_dur)
            path.append(xy); times.append((arrive + DWELL) / total_dur)
        path.append(off_right); times.append(1.0)

    respawn_k = 1.0 - (TIME_EXIT * 0.5) / total_dur   # world regrows on exit

    # --- Layer 2: filled blocks - block i cracks, pops, and drops during
    #     Steve's visit i, then regenerates just before the loop wraps.
    for i, (c, r, t) in enumerate(filled):
        x = GRID_LEFT + c * PITCH
        y = GRID_TOP + r * PITCH
        pid = rng.choice(tier_pids[t])
        arrive = TIME_ENTRY + i * (DWELL + HOP)
        kc = (arrive + 0.02) / total_dur            # crack starts building
        k1 = (arrive + DWELL * 0.6) / total_dur     # block pops
        k2 = (arrive + DWELL - 0.02) / total_dur    # gone before Steve hops away
        k1b = min(k1 + 0.002, (k1 + k2) / 2)        # crack vanishes with the pop
        blocks.append(
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" '
            f'fill="url(#{pid})">'
            f'<animate attributeName="opacity" values="1;1;0;0;1" '
            f'keyTimes="0;{k1:.5f};{k2:.5f};{respawn_k:.5f};1" dur="{total_dur}s" '
            f'repeatCount="indefinite"/>'
            f'<animate attributeName="y" values="{y};{y};{y + 3};{y + 3};{y}" '
            f'keyTimes="0;{k1:.5f};{k2:.5f};{respawn_k:.5f};1" dur="{total_dur}s" '
            f'repeatCount="indefinite"/></rect>'
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" '
            f'fill="url(#crackpat)" opacity="0">'
            f'<animate attributeName="opacity" values="0;0;0.9;0;0" '
            f'keyTimes="0;{kc:.5f};{k1:.5f};{k1b:.5f};1" dur="{total_dur}s" '
            f'repeatCount="indefinite"/></rect>')

    cells_svg = '<g id="grid">' + "".join(blocks) + "</g>"
    ground = theme["ground"](rng)

    # --- Steve + weapon, hopping cell to cell along the visit path
    weapon_inner = weapon_fn()
    if style["enchanted"]:
        weapon_inner += enchant_glint()
    walk_values = "; ".join(f"{vx},{vy}" for vx, vy in path)
    walk_keytimes = ";".join(f"{k:.5f}" for k in times)
    steve = steve_svg(weapon_inner, swing_values, swing_dur,
                      walk_values, walk_keytimes, total_dur)

    # impact sparks pulsing at the weapon's strike point, in theme colors
    # (follows the same visit path, offset to where the weapon lands)
    s1, s2, s3 = theme["sparks"]
    spark_values = "; ".join(f"{vx + 20},{vy + 11}" for vx, vy in path)
    sparks = (f'<g opacity="0.8"><g>'
              f'{px(0, 0, 1, 1, s1)}{px(2, -1, 1, 1, s2)}{px(-2, 1, 1, 1, s3)}'
              f'<animate attributeName="opacity" values="0;0.9;0" '
              f'dur="{swing_dur}s" repeatCount="indefinite"/></g>'
              f'<animateTransform attributeName="transform" type="translate" '
              f'values="{spark_values}" keyTimes="{walk_keytimes}" '
              f'dur="{total_dur}s" repeatCount="indefinite"/></g>')

    # --- Footer: contribution count + biome/weapon caption + legend
    total = sum(v[0] for v in grid.values())
    caption = (f'<text x="{GRID_LEFT}" y="{GROUND_Y + 16}" font-family="monospace" '
               f'font-size="8" letter-spacing="0.5" fill="#8b949e">'
               f'{total:,} contributions in the last year &#183; '
               f'Weapon: {weapon_name}</text>')

    legend_tiles_end = WIDTH - 62
    legend_x0 = legend_tiles_end - (6 * PITCH - GAP)
    legend = [f'<text x="{legend_x0 - 6}" y="{GROUND_Y + 16}" font-family="monospace" '
              f'font-size="8" fill="#8b949e" text-anchor="end">Less</text>']
    for t in range(6):
        lx = legend_x0 + t * PITCH
        legend.append(f'<rect x="{lx}" y="{GROUND_Y + 8}" width="{CELL}" '
                      f'height="{CELL}" rx="2" fill="url(#t{t}a)"/>')
    legend.append(f'<text x="{legend_tiles_end + 6}" y="{GROUND_Y + 16}" '
                  f'font-family="monospace" font-size="8" fill="#8b949e">More</text>')
    legend = "".join(legend)

    label = f"Minecraft contribution graph - {weapon_name}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{label}">'
            f'<title>{label}</title>'
            f'{defs}{sky_body}{cells_svg}{ground}{sparks}{steve}{caption}{legend}</svg>')

def main():
    username = os.environ.get("GH_USERNAME")
    token = os.environ.get("GITHUB_TOKEN")
    out_path = os.environ.get("OUT_PATH", "minecraft-contribution.svg")
    if not username or not token:
        print("ERROR: GH_USERNAME and GITHUB_TOKEN must be set", file=sys.stderr)
        sys.exit(1)

    # New seed every workflow run => new biome, weapon, and tile textures.
    seed = f"{os.environ.get('GITHUB_RUN_ID', time.time_ns())}-{date.today()}"
    rng = random.Random(seed)
    style = choose_style(rng)

    print(f"Fetching contributions for {username}...")
    grid = fetch_contributions(username, token)
    filled = sum(1 for v in grid.values() if v[0] > 0)
    total = sum(v[0] for v in grid.values())
    print(f"  {filled}/{len(grid)} active days, {total} total contributions")
    print(f"  Weapon: "
          f"{'Enchanted ' if style['enchanted'] else ''}{WEAPONS[style['weapon']][0]}")

    svg = build_svg(grid, style, rng)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    Path(out_path).write_text(svg, encoding="utf-8")
    print(f"Wrote {out_path} ({len(svg):,} bytes)")

if __name__ == "__main__":
    main()
