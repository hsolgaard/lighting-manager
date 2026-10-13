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
    "lighting-manager-overview-card.js",
)

TAGS = (
    "lighting-manager-countdown-card",
    "light-scheduler-list-card",
    "lighting-manager-scheduler-card",
    "lighting-manager-room-card",
    "lighting-manager-overview-card",
)


def build() -> str:
    parts = [
        "/**\n"
        " * Lighting Manager cards - single bundle (GENERATED, do not edit).\n"
        " * Source: the individual *-card.js files in this folder;\n"
        " * rebuild with: python3 scripts/build_bundle.py\n"
        " * Loading it more than once (as a Lovelace resource AND by the\n"
        " * integration) is harmless: every card only defines itself if its\n"
        " * element name is not already defined. Each card loads in its own\n"
        " * protected section, so one failure cannot stop the others, and the\n"
        " * last line reports which cards ended up defined.\n"
        " */\n"
    ]
    for name in SOURCES:
        source = (FRONTEND / name).read_text(encoding="utf-8").rstrip("\n")
        parts.append(
            f"\n/* ===== {name} ===== */\n"
            "try {\n{\n"
            f"{source}\n"
            "}\n} catch (error) {\n"
            f"  console.error('Lighting Manager: {name} failed to load:', error);\n"
            "}\n"
        )
    tags = ", ".join(f"'{t}'" for t in TAGS)
    parts.append(
        "\n/* ===== load report ===== */\n"
        f"console.info('LIGHTING-MANAGER-CARDS bundle ran:', [{tags}]"
        ".map(n => n + (customElements.get(n) ? ' ok' : ' MISSING')).join(', '));\n"
    )
    return "".join(parts)


if __name__ == "__main__":
    OUTPUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
