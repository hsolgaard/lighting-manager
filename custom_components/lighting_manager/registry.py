"""Discovery and registry helpers (PRD §11, §14, §15).

Everything here reads or writes Home Assistant's own entity/device/area/
label registries directly - Lighting Manager keeps no parallel copy of
this data (PRD §4.7 "no second reality").
"""
from __future__ import annotations

import asyncio
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


async def async_set_entity_icon(
    hass: HomeAssistant, entity_id: str, icon: str | None
) -> None:
    """Set (or clear, with icon=None) the entity registry's icon override.

    This is the same field as the "Icon" box in Home Assistant's own entity
    settings, so every tile (including the room card, which wraps the native
    tile) picks it up. Clearing it falls back to the integration's default.
    """
    registry = er.async_get(hass)
    if registry.async_get(entity_id) is None:
        raise ValueError(f"{entity_id} has no entity registry entry, so its icon cannot be set")
    registry.async_update_entity(entity_id, icon=icon)


def async_get_icon_override(hass: HomeAssistant, entity_id: str) -> str | None:
    """The icon the user has set in the entity registry, or None for the default."""
    entry = er.async_get(hass).async_get(entity_id)
    return entry.icon if entry is not None else None


# --- Light replacement (PRD §24) ---------------------------------------------
#
# A replacement is "the new light takes over the old light's entity ID", so
# every schedule, automation, scene, script and dashboard that names the old
# entity keeps working untouched. Home Assistant refuses to rename an entity
# onto an ID that is still registered, so the old entry is first moved out of
# the way (renamed to <id>_replaced), the new entry is renamed onto the freed
# ID, and only then is the old entry removed (dead) or disabled (still alive).

# How long to wait for Home Assistant to drop the old entity's state after it
# is renamed aside, before forcing it. A module constant so tests can shorten it.
STATE_CLEAR_TIMEOUT = 5.0


def async_is_dead(hass: HomeAssistant, entity_id: str) -> bool:
    """True if the entity has no state or is unavailable (a dead light)."""
    state = hass.states.get(entity_id)
    return state is None or state.state == "unavailable"


def _free_aside_id(hass: HomeAssistant, entity_id: str) -> str:
    registry = er.async_get(hass)
    domain, object_id = entity_id.split(".", 1)
    candidate = f"{domain}.{object_id}_replaced"
    n = 2
    while registry.async_is_registered(candidate) or hass.states.get(candidate) is not None:
        candidate = f"{domain}.{object_id}_replaced_{n}"
        n += 1
    return candidate


async def _async_wait_state_cleared(hass: HomeAssistant, entity_id: str) -> None:
    """Wait for the old ID's state to disappear; force it if Home Assistant is slow."""
    waited = 0.0
    while hass.states.get(entity_id) is not None and waited < STATE_CLEAR_TIMEOUT:
        await asyncio.sleep(0.05)
        waited += 0.05
    if hass.states.get(entity_id) is not None:
        hass.states.async_remove(entity_id)


SWITCH_AS_X_DOMAIN = "switch_as_x"
REPLACED_SUFFIX = " (replaced)"


def switch_as_x_wrapper_for(hass: HomeAssistant, switch_entity_id: str):
    """The Switch-as-X helper entry already wrapping this switch, or None."""
    entry = er.async_get(hass).async_get(switch_entity_id)
    wanted = {switch_entity_id}
    if entry is not None:
        wanted.add(entry.id)
    for config_entry in hass.config_entries.async_entries(SWITCH_AS_X_DOMAIN):
        if config_entry.options.get("entity_id") in wanted:
            return config_entry
    return None


def async_can_convert_switch(hass: HomeAssistant, switch_entity_id: str) -> str | None:
    """None if this switch can be turned into a light, else the plain-words reason."""
    if not switch_entity_id.startswith("switch."):
        return "Only a switch can be turned into a light."
    if er.async_get(hass).async_get(switch_entity_id) is None:
        return "This switch isn't registered in Home Assistant (it may be defined in YAML), so it can't be wrapped."
    if switch_as_x_wrapper_for(hass, switch_entity_id) is not None:
        return (
            "This switch is already wrapped by a Switch as X helper. "
            "Use the light that helper created instead."
        )
    return None


async def async_create_light_from_switch(
    hass: HomeAssistant, switch_entity_id: str, name: str | None
):
    """Wrap a switch in a light with Home Assistant's own "Switch as X" helper.

    Returns (light_entity_id, helper_config_entry). The switch is hidden
    (the helper does this itself) so only the light shows up. Raises
    ValueError, with nothing left behind, if the helper can't be created.
    """
    reason = async_can_convert_switch(hass, switch_entity_id)
    if reason:
        raise ValueError(reason)
    registry = er.async_get(hass)
    switch_entry = registry.async_get(switch_entity_id)
    was_hidden = switch_entry.hidden_by

    try:
        flow = await hass.config_entries.flow.async_init(
            SWITCH_AS_X_DOMAIN, context={"source": "user"}
        )
    except Exception as err:  # unknown handler: integration not available
        raise ValueError(
            "Home Assistant's \"Switch as X\" helper isn't available, so a light can't be created from this switch."
        ) from err

    keys = {str(k) for k in flow["data_schema"].schema}
    user_input = {"entity_id": switch_entity_id, "target_domain": "light"}
    if "name" in keys and name:
        user_input["name"] = name
    try:
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], user_input)
    except Exception as err:
        hass.config_entries.flow.async_abort(flow["flow_id"])
        raise ValueError(f"Could not create a light from this switch: {err}") from err
    if result.get("type") != "create_entry":
        hass.config_entries.flow.async_abort(flow["flow_id"])
        raise ValueError("Could not create a light from this switch.")

    config_entry = result["result"]
    light_id = None
    waited = 0.0
    while light_id is None and waited < STATE_CLEAR_TIMEOUT:
        for entity in er.async_entries_for_config_entry(registry, config_entry.entry_id):
            if entity.domain == "light":
                light_id = entity.entity_id
                break
        if light_id is None:
            await asyncio.sleep(0.05)
            waited += 0.05
    if light_id is None:
        await async_discard_switch_light(hass, config_entry, switch_entity_id, was_hidden)
        raise ValueError("Home Assistant did not create a light from this switch.")
    return light_id, config_entry


async def async_discard_switch_light(
    hass: HomeAssistant, config_entry, switch_entity_id: str, hidden_before
) -> None:
    """Undo async_create_light_from_switch: drop the helper and un-hide the switch."""
    await hass.config_entries.async_remove(config_entry.entry_id)
    registry = er.async_get(hass)
    entry = registry.async_get(switch_entity_id)
    if entry is not None and hidden_before is None and entry.hidden_by is not None:
        registry.async_update_entity(switch_entity_id, hidden_by=None)


async def async_take_over_entity_id(
    hass: HomeAssistant,
    old_entity_id: str,
    new_entity_id: str | None,
    *,
    name: str | None,
    icon: str | None,
    area_id: str | None,
    extra_labels: set[str],
    drop_type_labels: bool,
    convert_switch: bool = False,
) -> dict:
    """Move new_entity_id onto old_entity_id's ID and retire the old entry.

    With convert_switch=True, new_entity_id is a switch: after the old
    light has been renamed out of the way, a light is created from that
    switch (Home Assistant's "Switch as X" helper) and the new light is
    the one that takes over.

    The old entry is first renamed to "<name> (replaced)" and its ID to
    <id>_replaced, so the newcomer can take both the name and the ID.

    Returns {"old_entity_id_now", "outcome": "removed"|"disabled",
    "created_from": <switch id or None>}. Raises ValueError (after
    undoing everything done so far) if Home Assistant refuses a step.
    """
    registry = er.async_get(hass)
    old_entry = registry.async_get(old_entity_id)
    new_entry = registry.async_get(new_entity_id) if new_entity_id else None
    if old_entry is None or new_entry is None:
        raise ValueError("Both lights must have an entity registry entry")
    if convert_switch:
        if old_entity_id.split(".")[0] != "light" or new_entity_id.split(".")[0] != "switch":
            raise ValueError("A light can only be replaced by an entity of the same type (light/switch)")
        reason = async_can_convert_switch(hass, new_entity_id)
        if reason:
            raise ValueError(reason)
    elif old_entity_id.split(".")[0] != new_entity_id.split(".")[0]:
        raise ValueError("A light can only be replaced by an entity of the same type (light/switch)")

    dead = async_is_dead(hass, old_entity_id)
    aside_id = _free_aside_id(hass, old_entity_id)

    aside_changes: dict = {"new_entity_id": aside_id}
    if name:
        aside_changes["name"] = f"{name}{REPLACED_SUFFIX}"
    registry.async_update_entity(old_entity_id, **aside_changes)
    await _async_wait_state_cleared(hass, old_entity_id)

    def undo_aside() -> None:
        restore: dict = {"new_entity_id": old_entity_id}
        if name:
            restore["name"] = old_entry.name
        registry.async_update_entity(aside_id, **restore)

    created = None  # (config_entry, switch_id, hidden_before)
    created_from = None
    if convert_switch:
        switch_id = new_entity_id
        hidden_before = new_entry.hidden_by
        try:
            new_entity_id, config_entry = await async_create_light_from_switch(
                hass, switch_id, name
            )
        except Exception:
            undo_aside()
            raise
        created = (config_entry, switch_id, hidden_before)
        created_from = switch_id
        new_entry = registry.async_get(new_entity_id)

    changes: dict = {"new_entity_id": old_entity_id}
    if name:
        changes["name"] = name
    if icon:
        changes["icon"] = icon
    if area_id is not None:
        changes["area_id"] = area_id
    known = set(_label_id_cache(hass).values())
    labels = set(new_entry.labels)
    if drop_type_labels:
        labels -= known
    labels |= extra_labels
    if labels != set(new_entry.labels):
        changes["labels"] = labels
    try:
        registry.async_update_entity(new_entity_id, **changes)
    except ValueError:
        if created is not None:
            await async_discard_switch_light(hass, *created)
        undo_aside()
        raise

    if dead:
        registry.async_remove(aside_id)
        outcome = "removed"
    else:
        registry.async_update_entity(aside_id, disabled_by=er.RegistryEntryDisabler.USER)
        outcome = "disabled"
    return {
        "old_entity_id_now": aside_id,
        "outcome": outcome,
        "created_from": created_from,
    }


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


def async_get_device_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """The Home Assistant device an entity belongs to, if any.

    Used to link from Lighting Manager to the device's settings page
    (/config/devices/device/<id>). Home Assistant has no address that opens an
    entity's own settings dialog directly.
    """
    entry = er.async_get(hass).async_get(entity_id)
    return entry.device_id if entry is not None else None


def async_get_source_integration(hass: HomeAssistant, entity_id: str) -> str | None:
    """Diagnostic-only originating integration for an entity (PRD §11)."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return None
    return entry.platform
