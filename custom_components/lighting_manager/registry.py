"""Discovery and registry helpers (PRD §11, §14, §15).

Everything here reads or writes Home Assistant's own entity/device/area/
label registries directly - Lighting Manager keeps no parallel copy of
this data (PRD §4.7 "no second reality").
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)

from .const import DOMAIN, LIGHT_DOMAIN, LIGHT_TYPES, SWITCH_DOMAIN

_LOGGER = logging.getLogger(__name__)


def stable_key_for_entity(hass: HomeAssistant, entity_id: str) -> str:
    """Return the identifier Lighting Manager should persist metadata under.

    Prefers the entity registry's unique_id (survives entity_id renames);
    falls back to entity_id for entities with no registry entry at all
    (e.g. some template/YAML-defined lights). See storage.py's module
    docstring and PRD §25.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is not None and entry.unique_id:
        return f"{entry.platform}:{entry.unique_id}"
    return f"entity_id:{entity_id}"


def async_list_eligible_lights(hass: HomeAssistant) -> list[str]:
    """All light.* entity_ids currently known to Home Assistant (PRD §11).

    Visibility and adoption are separate concepts (PRD §12): this
    returns every eligible entity_id, not just adopted ones. Callers
    combine this with the storage layer to know which are adopted.
    """
    return [
        state.entity_id
        for state in hass.states.async_all(LIGHT_DOMAIN)
    ]


def async_list_promotable_switches(hass: HomeAssistant) -> list[str]:
    """switch.* entities that *could* be promoted to managed lighting (PRD §11.1).

    Deliberately returns everything in the switch domain rather than
    trying to guess which ones are lamps - PRD §11.1 requires the user
    to explicitly promote/classify, so silent inference isn't wanted
    here. A future P2 "suggest likely lamp switches" heuristic can build
    on top of this list without changing its contract.
    """
    return [state.entity_id for state in hass.states.async_all(SWITCH_DOMAIN)]


def async_get_effective_area(hass: HomeAssistant, entity_id: str) -> str | None:
    """Resolve the effective Area for an entity: entity override, else device (PRD §14)."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id is not None:
        return entry.area_id
    if entry.device_id is not None:
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(entry.device_id)
        if device is not None:
            return device.area_id
    return None


def async_get_area_mismatch(hass: HomeAssistant, entity_id: str) -> tuple[str, str] | None:
    """Return (device_area_id, entity_area_id) if they disagree, else None (PRD §14, §28).

    Both an entity-level override and a device area must actually be
    set for this to be a "mismatch" worth surfacing - a light with only
    a device area (the common case) is not a mismatch, it's just
    inheriting normally.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or entry.area_id is None or entry.device_id is None:
        return None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get(entry.device_id)
    if device is None or device.area_id is None:
        return None

    if device.area_id != entry.area_id:
        return (device.area_id, entry.area_id)
    return None


async def async_set_entity_area(
    hass: HomeAssistant, entity_id: str, area_id: str | None
) -> None:
    """Set (or clear, with area_id=None) an entity-level Area override.

    This writes the *entity* registry override, independent of the
    underlying device's Area - required for PRD §14's Area-mismatch
    resolution ("Use Big Lounge" / "Use Hallway") where the user is
    explicitly choosing one of two disagreeing values. Moving a light
    from the Rooms view drag/drop should normally go through this too,
    since it is the effective Area, not the device Area, that Lighting
    Manager treats as a light's Room (PRD §14 "Room means the effective
    Home Assistant Area").
    """
    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, area_id=area_id)


async def async_rename_entity(
    hass: HomeAssistant, entity_id: str, name: str
) -> None:
    """Rename a light's friendly name via the entity registry (PRD §13).

    Uses the registry's `name` override rather than editing device name
    or entity_id - matches the PRD's "simple Name field" as the default
    UX, with entity ID / device name left to the Advanced section (not
    implemented by this helper; see the future light-detail websocket
    commands for that path, which must carry the "external automations
    or templates may reference it" warning PRD §13 requires).
    """
    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, name=name)


# --- Light-type taxonomy via HA Labels (PRD §15) ----------------------------
#
# Open technical investigation #2 (PRD §38): "determine the cleanest
# naming/namespace convention ... and whether exactly one type Label
# should be enforced per managed light."
#
# Verified against the real label_registry API (2026-09): LabelRegistry
# .async_create() does not accept a caller-chosen id - HA generates
# label_id itself from `name` (LabelRegistry._generate_id), and the
# correct way to find an existing label is async_get_label_by_name(),
# not a guessed id. A "lighting-manager:<type>" id scheme (this
# module's first draft) does not work: async_create() would raise
# TypeError on the unexpected `label_id` kwarg. The namespacing this
# investigation asked for is therefore done via the human-readable
# name ("Lighting: Ceiling", etc.) instead, with the resulting opaque
# label_id cached per Home Assistant run so entity labels can still be
# matched back to a light type without re-querying by name every time.

_LABEL_NAME_PREFIX = "Lighting: "


def _label_name_for_type(light_type: str) -> str:
    return f"{_LABEL_NAME_PREFIX}{light_type.replace('_', ' ').title()}"


def _label_id_cache(hass: HomeAssistant) -> dict[str, str]:
    """type -> label_id cache for this HA run (PRD §15 single-label enforcement)."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault("light_type_label_ids", {})


async def async_ensure_type_labels_exist(hass: HomeAssistant) -> None:
    """Create the controlled set of type Labels if they don't exist yet."""
    registry = lr.async_get(hass)
    cache = _label_id_cache(hass)
    for light_type in LIGHT_TYPES:
        name = _label_name_for_type(light_type)
        label = registry.async_get_label_by_name(name)
        if label is None:
            label = registry.async_create(name=name, icon="mdi:lightbulb-multiple")
        cache[light_type] = label.label_id


async def async_set_light_type(
    hass: HomeAssistant, entity_id: str, light_type: str | None
) -> None:
    """Apply (or clear, with light_type=None) the controlled type Label.

    Removes any other Lighting Manager type label first so at most one
    is ever present, per the single-label-enforced decision above.
    """
    if light_type is not None and light_type not in LIGHT_TYPES:
        raise ValueError(f"Unknown light type: {light_type!r}")

    cache = _label_id_cache(hass)
    if light_type is not None and light_type not in cache:
        # Labels weren't ensured yet (e.g. this was called before
        # coordinator setup ran) - create them now rather than fail.
        await async_ensure_type_labels_exist(hass)

    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get(entity_id)
    if entry is None:
        return

    known_label_ids = set(cache.values())
    current_labels = {label for label in entry.labels if label not in known_label_ids}
    if light_type is not None:
        current_labels.add(cache[light_type])

    entity_registry.async_update_entity(entity_id, labels=current_labels)


def get_light_type(hass: HomeAssistant, entity_id: str) -> str | None:
    """Read the current controlled type Label for an entity, if any."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return None
    reverse = {label_id: light_type for light_type, label_id in _label_id_cache(hass).items()}
    for label in entry.labels:
        if label in reverse:
            return reverse[label]
    return None


def async_get_source_integration(hass: HomeAssistant, entity_id: str) -> str | None:
    """Diagnostic-only originating integration for an entity (PRD §11)."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return None
    return entry.platform
