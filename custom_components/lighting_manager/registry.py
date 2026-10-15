"""Discovery and registry helpers (PRD §11, §14, §15).

Everything here reads or writes Home Assistant's own entity/device/area/
label registries directly - Lighting Manager keeps no parallel copy of
this data (PRD §4.7 "no second reality").
"""
from __future__ import annotations

import asyncio
import logging
import re

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


def _set_area_for_new_light(hass: HomeAssistant, entity_id: str, area_id: str) -> None:
    """Put the new light in the old light's room, in the way Home Assistant allows.

    An entity that has no name of its own (a light made from a plug takes its
    device's name) may not have an area of its own: Home Assistant only lets
    you set the area on its device. So: do nothing if it is already in that
    room, try the entity first, and fall back to the device. A failure here
    never undoes the replace; the light just stays in whatever room it was in.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return
    if async_get_effective_area(hass, entity_id) == area_id:
        return
    try:
        registry.async_update_entity(entity_id, area_id=area_id)
        return
    except ValueError:
        pass
    if entry.device_id:
        try:
            dr.async_get(hass).async_update_device(entry.device_id, area_id=area_id)
        except ValueError:
            _LOGGER.warning("Could not move %s to its old light's room", entity_id)
    else:
        _LOGGER.warning("Could not move %s to its old light's room", entity_id)


async def async_take_over_entity_id(
    hass: HomeAssistant,
    old_entity_id: str,
    new_entity_id: str | None,
    *,
    old_name: str | None,
    icon: str | None,
    area_id: str | None,
    extra_labels: set[str],
    drop_type_labels: bool,
    convert_switch: bool = False,
    keep_new_id: bool = False,
    new_name: str | None = None,
) -> dict:
    """Move new_entity_id onto old_entity_id's ID and retire the old entry.

    With convert_switch=True, new_entity_id is a switch: after the old
    light has been renamed out of the way, a light is created from that
    switch (Home Assistant's "Switch as X" helper) and the new light is
    the one that takes over.

    With new_name set, the newcomer is also given that friendly name (used
    to carry the old light's name over when it keeps its own entity ID).

    With keep_new_id=True the newcomer keeps its own entity ID instead of
    taking the old one (the caller is then responsible for re-pointing
    whatever referred to the old ID).

    The old entry is first renamed to "<old_name> (replaced)" and its ID
    to <id>_replaced, so the newcomer can take the ID. The newcomer keeps
    its own name; a light created from a switch gets the switch's name from
    the Switch as X helper itself, which also puts it on the switch's device.

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
    if old_name:
        aside_changes["name"] = f"{old_name}{REPLACED_SUFFIX}"
    registry.async_update_entity(old_entity_id, **aside_changes)
    await _async_wait_state_cleared(hass, old_entity_id)

    def undo_aside() -> None:
        restore: dict = {"new_entity_id": old_entity_id}
        if old_name:
            restore["name"] = old_entry.name
        registry.async_update_entity(aside_id, **restore)

    created = None  # (config_entry, switch_id, hidden_before)
    created_from = None
    if convert_switch:
        switch_id = new_entity_id
        hidden_before = new_entry.hidden_by
        try:
            new_entity_id, config_entry = await async_create_light_from_switch(
                hass, switch_id, None
            )
        except Exception:
            undo_aside()
            raise
        created = (config_entry, switch_id, hidden_before)
        created_from = switch_id
        new_entry = registry.async_get(new_entity_id)

    changes: dict = {} if keep_new_id else {"new_entity_id": old_entity_id}
    if icon:
        changes["icon"] = icon
    if new_name:
        changes["name"] = new_name
    known = set(_label_id_cache(hass).values())
    labels = set(new_entry.labels)
    if drop_type_labels:
        labels -= known
    labels |= extra_labels
    if labels != set(new_entry.labels):
        changes["labels"] = labels
    try:
        if changes:
            registry.async_update_entity(new_entity_id, **changes)
    except ValueError:
        if created is not None:
            await async_discard_switch_light(hass, *created)
        undo_aside()
        raise

    final_new_id = new_entity_id if keep_new_id else old_entity_id
    if area_id is not None:
        _set_area_for_new_light(hass, final_new_id, area_id)

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
        "new_entity_id_now": new_entity_id if keep_new_id else old_entity_id,
    }


# --- Removing a replaced light for good -------------------------------------


def _device_config_entry_ids(device) -> set[str]:
    """Config entries that own a device, across Home Assistant versions.

    Newer Home Assistant gives a device exactly one config entry
    (config_entry_id); older versions keep a set (config_entries).
    """
    single = getattr(device, "config_entry_id", None)
    if single:
        return {single}
    return set(getattr(device, "config_entries", None) or ())


async def async_remove_device_if_unused(hass: HomeAssistant, device_id: str) -> tuple[bool, str | None]:
    """Remove a device from Home Assistant if nothing else uses it.

    Returns (removed, reason_if_not). Follows what Settings > Devices does:
    the owning integration must allow the removal (its
    async_remove_config_entry_device hook), otherwise Home Assistant itself
    refuses to delete the device and so do we.
    """
    from homeassistant import loader

    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    if device is None:
        return True, None

    entity_registry = er.async_get(hass)
    others = er.async_entries_for_device(entity_registry, device_id, include_disabled_entities=True)
    if others:
        names = ", ".join(sorted(e.entity_id for e in others)[:5])
        return False, f"The device is still used by other entities ({names}), so it was kept."

    entry_ids = _device_config_entry_ids(device)
    entries = []
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            continue
        if not getattr(entry, "supports_remove_device", None):
            return False, (
                f"Home Assistant can't remove this device itself: the {entry.domain} integration "
                "doesn't allow it. Remove it where it is managed (for example in that integration's "
                "own app or settings page)."
            )
        entries.append(entry)

    try:
        for entry in entries:
            integration = await loader.async_get_integration(hass, entry.domain)
            component = await integration.async_get_component()
            if not await component.async_remove_config_entry_device(hass, entry, device):
                return False, f"The {entry.domain} integration refused to remove this device."
        if getattr(device, "config_entry_id", None) is None and hasattr(device, "config_entries"):
            for entry in entries:  # older Home Assistant: detach each owner; the last one deletes it
                if device_registry.async_get(device_id):
                    device_registry.async_update_device(device_id, remove_config_entry_id=entry.entry_id)
        elif device_registry.async_get(device_id):
            device_registry.async_remove_device(device_id)
    except Exception as err:  # an integration hook can raise anything
        _LOGGER.warning("Could not remove device %s: %s", device_id, err)
        return False, f"Home Assistant could not remove the device: {err}"
    if device_registry.async_get(device_id) is not None:
        return False, "The device is still registered after the removal was attempted."
    return True, None


async def async_remove_replaced_entity_and_device(
    hass: HomeAssistant,
    *,
    domain: str,
    platform: str | None,
    unique_id: str | None,
    device_id: str | None,
) -> dict:
    """Delete a replaced (old) entity and, when unused, its device.

    The entity is looked up by platform/unique_id because its entity ID was
    renamed aside at replacement time. Returns
    {"entity_removed": bool, "device_removed": bool, "notes": [str]}.
    """
    entity_registry = er.async_get(hass)
    notes: list[str] = []
    entity_removed = False
    if platform and unique_id:
        entity_id = entity_registry.async_get_entity_id(domain, platform, unique_id)
        if entity_id is not None:
            entity_registry.async_remove(entity_id)
            await asyncio.sleep(0)
            entity_removed = True
    device_removed = False
    if device_id:
        device_removed, reason = await async_remove_device_if_unused(hass, device_id)
        if reason:
            notes.append(reason)
    return {"entity_removed": entity_removed, "device_removed": device_removed, "notes": notes}


# --- Changing an entity ID ---------------------------------------------------

_ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def async_validate_new_entity_id(hass: HomeAssistant, entity_id: str, new_entity_id: str) -> None:
    """Raise ValueError (plain words) if entity_id can't be renamed to new_entity_id."""
    registry = er.async_get(hass)
    if registry.async_get(entity_id) is None:
        raise ValueError(f"{entity_id} has no entity registry entry, so it can't be renamed")
    if not _ENTITY_ID_RE.match(new_entity_id or ""):
        raise ValueError("An entity ID is lower-case letters, digits and underscores, like light.kitchen_lamp")
    if new_entity_id.split(".")[0] != entity_id.split(".")[0]:
        raise ValueError(f"The entity ID must stay a {entity_id.split('.')[0]}. entity (the type can't change)")
    if new_entity_id == entity_id:
        raise ValueError("That is already this light's entity ID")
    if registry.async_is_registered(new_entity_id) or hass.states.get(new_entity_id) is not None:
        raise ValueError(f"{new_entity_id} is already in use")


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
