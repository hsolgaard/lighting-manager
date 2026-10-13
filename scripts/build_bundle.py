#!/usr/bin/env python3
"""Build frontend/lighting-manager-cards.js from the individual card files.

One script means one request and a deterministic load order: add it once as
a Lovelace resource (Settings > Dashboards > Resources) and every Lighting
Manager card is defined before the dashboard draws. Each card file is kept
as the source of truth and wrapped in its own block so top-level names
cannot clash. Run from the repo root:  python3 scripts/build_bundle.py
"""
from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "custom_components" / "lighting_manager" / "frontend"
OUTPUT = FRONTEND / "lighting-manager-cards.js"

# Order matters only for readability: the room card waits for the
# countdown card's definition at run time.
SOURCES = (
    "lighting-manager-countdown-card.js",
    "light-scheduler-list-card.js",
    "lighting-manager-scheduler-card.js",
    "lighting-manager-room-card.js",
)

GUARD_KEY = "__lightingManagerCardsLoaded"


def build() -> str:
    parts = [
        "/**\n"
        " * Lighting Manager cards - single bundle (GENERATED, do not edit).\n"
        " * Source: the individual *-card.js files in this folder;\n"
        " * rebuild with: python3 scripts/build_bundle.py\n"
        " * Loading it more than once (as a Lovelace resource AND by the\n"
        " * integration) is harmless: only the first load runs.\n"
        " */\n"
        f"if (!window.{GUARD_KEY}) {{\n"
        f"window.{GUARD_KEY} = true;\n"
    ]
    for name in SOURCES:
        source = (FRONTEND / name).read_text(encoding="utf-8").rstrip("\n")
        parts.append(f"\n/* ===== {name} ===== */\n{{\n{source}\n}}\n")
    parts.append("}\n")
    return "".join(parts)


if __name__ == "__main__":
    OUTPUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
