"""Constants for the Lighting Manager integration.

Section references (e.g. "PRD §20") point at the sections of
Home_Assistant_Lighting_Manager_PRD_v0.3.docx this code implements.
"""
from __future__ import annotations

DOMAIN = "lighting_manager"
PLATFORMS: list[str] = []  # Lighting Manager has no entity platforms of its own (PRD §28).

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}/store"

# --- Discovery (PRD §11) -----------------------------------------------
LIGHT_DOMAIN = "light"
SWITCH_DOMAIN = "switch"

# --- Managed-light metadata fields (PRD §25, §34 in v0.2 / §25 in v0.3) -
ATTR_ADOPTED = "adopted"
ATTR_IGNORED = "ignored"
ATTR_LIGHT_TYPE_LABEL = "light_type_label"
ATTR_DASHBOARD_INCLUDED = "dashboard_included"
ATTR_PROMOTED_SWITCH = "promoted_switch"

# Controlled light-type taxonomy (PRD §15). Exposed as HA Labels named
# "Lighting: <Type>" (see registry.py's _label_name_for_type / label_id
# caching) rather than free-form text, per the "controlled taxonomy over
# free-form labels" rule.
LIGHT_TYPES: list[str] = [
    "ceiling",
    "pendant",
    "spotlights",
    "wall_light",
    "table_lamp",
    "floor_lamp",
    "cabinet_light",
    "outdoor",
    "lamp_plug",
    "decorative",
    "other",
]

# --- Native countdown engine (PRD §20) ----------------------------------
COUNTDOWN_PRESETS_MIN = [15, 30, 60, 120]

# --- Scheduler provider (PRD §18) ---------------------------------------
SCHEDULER_COMPONENT_ENTITY_PREFIX = "switch.schedule_"

# --- Inbox conditions (PRD §16, §40 decision table) ---------------------
INBOX_NEW = "new_light"
INBOX_UNASSIGNED = "unassigned"
INBOX_MISSING = "missing"
INBOX_AREA_MISMATCH = "area_mismatch"
INBOX_BROKEN_SCHEDULE_REF = "broken_schedule_reference"
INBOX_PROVIDER_UNAVAILABLE = "scheduler_provider_unavailable"

# How long a light must be unavailable before it is treated as "missing"
# rather than a transient blip (PRD §24 does not pin a number; this is an
# implementation default, easy to make configurable later).
MISSING_AFTER_SECONDS = 24 * 60 * 60
