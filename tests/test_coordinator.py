"""Tests for coordinator.py: the aggregation layer (PRD §29).

Covers the third real issue found during the build (see the Feasibility
Review's "Build kickoff" section): promoted switches being marked
adopted in storage but never actually appearing in discovery, because
discovery only scanned the light.* domain.
"""
from __future__ import annotations

import pytest
from homeassistant.core import CoreState, EVENT_HOMEASSISTANT_STARTED, HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.lighting_manager import registry
from custom_components.lighting_manager.const import (
    INBOX_AREA_MISMATCH,
    INBOX_NEW,
    INBOX_UNASSIGNED,
)
from custom_components.lighting_manager.coordinator import LightingManagerCoordinator


@pytest.fixture
async def coordinator(hass: HomeAssistant):
    coord = LightingManagerCoordinator(hass)
    await coord.async_setup()
    yield coord
    await coord.async_unload()


def _register_light(hass: HomeAssistant, entity_id: str, unique_id: str) -> str:
    """Give a test light a real entity-registry entry.

    Rename/move (registry.async_rename_entity / async_set_entity_area)
    call entity_registry.async_update_entity() directly, which requires
    a pre-existing registry entry - true for any real Zigbee-backed
    light (which is what every test in this file is modelling, and what
    Hans's live testing used), but notably NOT handled gracefully for
    the registry-less entities stable_key_for_entity's own docstring
    acknowledges exist (template/YAML lights) - see the "known
    limitation" note in the Feasibility Review's automated-testing
    section.
    """
    domain, object_id = entity_id.split(".", 1)
    entry = er.async_get(hass).async_get_or_create(domain, "test_platform", unique_id)
    hass.states.async_set(entry.entity_id, "off")
    return entry.entity_id


async def test_list_lights_includes_unadopted_discovered_lights(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    hass.states.async_set("light.kitchen", "on")

    views = coordinator.async_list_lights()

    assert any(v.entity_id == "light.kitchen" and not v.adopted for v in views)


async def test_adopt_light_persists_name_area_and_type(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    from homeassistant.helpers import area_registry as ar

    entity_id = _register_light(hass, "light.office_spots", "office-spots")
    area = ar.async_get(hass).async_create("Living Room")

    await coordinator.async_adopt_light(
        entity_id,
        name="Test Light",
        area_id=area.id,
        light_type="spotlights",
    )

    view = next(
        v for v in coordinator.async_list_lights() if v.entity_id == entity_id
    )
    assert view.adopted is True
    assert view.area_id == area.id
    assert view.light_type == "spotlights"
    assert view.status == "ok"
    # LightView.name is read live from hass.states (see storage.py's "HA
    # is the source of truth" note) rather than from the registry
    # directly - a real entity reconciles its friendly_name from a
    # registry rename automatically; this bare test state doesn't have a
    # live entity behind it to do that, so the rename itself is verified
    # against the registry, which is what async_rename_entity actually
    # controls (also covered directly in test_registry.py).
    assert er.async_get(hass).async_get(entity_id).name == "Test Light"


async def test_unadopted_light_has_needs_setup_status(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    hass.states.async_set("light.new_light", "off")

    view = next(
        v for v in coordinator.async_list_lights() if v.entity_id == "light.new_light"
    )

    assert view.status == "needs_setup"


async def test_ignored_light_not_in_inbox(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    hass.states.async_set("light.attic", "off")

    await coordinator.async_ignore_light("light.attic")

    inbox_entity_ids = {item.entity_id for item in coordinator.async_build_inbox()}
    assert "light.attic" not in inbox_entity_ids


async def test_unadopted_light_appears_as_new_in_inbox(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    hass.states.async_set("light.new_light", "off")

    inbox = coordinator.async_build_inbox()

    matching = [item for item in inbox if item.entity_id == "light.new_light"]
    assert len(matching) == 1
    assert matching[0].condition == INBOX_NEW


async def test_promoted_switch_appears_in_discovery_and_inbox(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    """Regression test for the missing-promoted-switch discovery gap."""
    hass.states.async_set("switch.wall_lamp", "off")

    await coordinator.async_promote_switch("switch.wall_lamp")

    entity_ids = {v.entity_id for v in coordinator.async_list_lights()}
    assert "switch.wall_lamp" in entity_ids

    inbox_entity_ids = {item.entity_id for item in coordinator.async_build_inbox()}
    # Promoted but not yet adopted -> should surface as new, same as a light.
    assert "switch.wall_lamp" in inbox_entity_ids


async def test_promoted_switch_not_double_counted_after_adoption(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    entity_id = _register_light(hass, "switch.wall_lamp", "wall-lamp")
    await coordinator.async_promote_switch(entity_id)
    await coordinator.async_adopt_light(entity_id, name="Wall Lamp")

    entity_ids = [v.entity_id for v in coordinator.async_list_lights()]
    assert entity_ids.count(entity_id) == 1


async def test_unignore_light_clears_ignored_flag(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    """Added alongside the real Inbox UI: Ignore needs a way back."""
    hass.states.async_set("light.attic", "off")
    await coordinator.async_ignore_light("light.attic")
    assert "light.attic" not in {
        item.entity_id for item in coordinator.async_build_inbox()
    }

    await coordinator.async_unignore_light("light.attic")

    inbox = coordinator.async_build_inbox()
    matching = [item for item in inbox if item.entity_id == "light.attic"]
    assert len(matching) == 1
    assert matching[0].condition == INBOX_NEW


async def test_unassigned_light_appears_in_inbox_once_adopted(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    hass.states.async_set("light.no_area", "off")
    await coordinator.async_adopt_light("light.no_area")

    inbox = coordinator.async_build_inbox()

    matching = [item for item in inbox if item.entity_id == "light.no_area"]
    assert any(item.condition == INBOX_UNASSIGNED for item in matching)


async def test_area_mismatch_appears_in_inbox_once_adopted(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="fake_platform")
    entry.add_to_hass(hass)

    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_area = area_registry.async_create("Hans Office")
    entity_area = area_registry.async_create("Lounge Big")
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("fake_platform", "mismatch-dev")}
    )
    device_registry.async_update_device(device.id, area_id=device_area.id)
    reg_entry = entity_registry.async_get_or_create(
        "light", "test_platform", "mismatch-light", device_id=device.id
    )
    entity_registry.async_update_entity(reg_entry.entity_id, area_id=entity_area.id)
    hass.states.async_set(reg_entry.entity_id, "off")

    await coordinator.async_adopt_light(reg_entry.entity_id)

    inbox = coordinator.async_build_inbox()
    matching = [
        item
        for item in inbox
        if item.entity_id == reg_entry.entity_id and item.condition == INBOX_AREA_MISMATCH
    ]
    assert len(matching) == 1
    assert matching[0].detail == {"device_area": device_area.id, "entity_area": entity_area.id}


async def test_move_light_updates_effective_area(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    from homeassistant.helpers import area_registry as ar

    entity_id = _register_light(hass, "light.spots", "spots-move")
    area = ar.async_get(hass).async_create("New Area")
    await coordinator.async_adopt_light(entity_id)

    await coordinator.async_move_light(entity_id, area.id)

    view = next(v for v in coordinator.async_list_lights() if v.entity_id == entity_id)
    assert view.area_id == area.id


async def test_rename_light_updates_view_name(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    entity_id = _register_light(hass, "light.spots", "spots-rename")
    await coordinator.async_adopt_light(entity_id)

    await coordinator.async_rename_light(entity_id, "Renamed")

    # See the note in test_adopt_light_persists_name_area_and_type on why
    # this asserts against the registry rather than LightView.name.
    assert er.async_get(hass).async_get(entity_id).name == "Renamed"


async def test_set_dashboard_included_persists(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    hass.states.async_set("light.spots", "off")
    await coordinator.async_adopt_light("light.spots")

    await coordinator.async_set_dashboard_included("light.spots", False)

    view = next(v for v in coordinator.async_list_lights() if v.entity_id == "light.spots")
    assert view.dashboard_included is False


async def test_scheduler_detected_after_late_ha_start(hass: HomeAssistant) -> None:
    """Regression test for the real 0-schedules-for-everyone bug (2026-09-14).

    Checking niels_faber.is_available() inline during Lighting Manager's
    own async_setup() only caught the Scheduler Component if its
    switch.schedule_* entities already existed at that exact moment -
    not guaranteed on a cold HA boot, since custom_components load order
    isn't ordered relative to each other. A false negative there was
    permanent for the rest of the HA run (checked once, never retried),
    which is exactly what Hans hit live: confirmed real switch.schedule_*
    entities existed, correctly hex-formatted, yet every light showed 0
    schedules. Simulates that race here: HA isn't "started" yet when the
    coordinator sets up, the scheduler entity doesn't exist until after,
    and detection should still succeed once HA finishes starting.
    """
    hass.set_state(CoreState.not_running)
    coord = LightingManagerCoordinator(hass)
    await coord.async_setup()
    assert coord.scheduler is None  # not detected yet - HA hasn't started

    # The Scheduler Component's entity only appears "later", before HA
    # finishes starting (matching a real cold-boot ordering).
    hass.states.async_set(
        "switch.schedule_abc123",
        "on",
        {"entities": ["light.kitchen"], "weekdays": [], "timeslots": [], "actions": []},
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert coord.scheduler is not None
    assert len(coord.scheduler.async_schedules_for_entity("light.kitchen")) == 1

    await coord.async_unload()


async def test_list_promoted_switch_entity_ids_excludes_unrelated_and_other_areas(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    """Regression test for the promoted-switch safety gap (PRD Revision §4.1).

    A naive area-targeted switch.turn_off would also hit unrelated
    switches sharing the Area (e.g. Hans's real switch.hob_power,
    switch.hob_child_lock, switch.dk_vpn). This must return only
    switches that are both promoted AND adopted in the requested
    Area(s) - never a promoted-but-not-adopted switch, an ignored one,
    or one in a different Area.
    """
    from homeassistant.helpers import area_registry as ar

    kitchen = ar.async_get(hass).async_create("Kitchen")
    lounge = ar.async_get(hass).async_create("Lounge")

    lamp_switch = _register_light(hass, "switch.counter_lamp", "counter-lamp")
    await coordinator.async_promote_switch(lamp_switch)
    await coordinator.async_adopt_light(lamp_switch, area_id=kitchen.id)

    not_yet_adopted = _register_light(hass, "switch.new_lamp", "new-lamp")
    await coordinator.async_promote_switch(not_yet_adopted)
    await registry.async_set_entity_area(hass, not_yet_adopted, kitchen.id)

    unrelated_switch = _register_light(hass, "switch.hob_power", "hob-power")
    await registry.async_set_entity_area(hass, unrelated_switch, kitchen.id)

    other_area_lamp = _register_light(hass, "switch.lounge_lamp", "lounge-lamp")
    await coordinator.async_promote_switch(other_area_lamp)
    await coordinator.async_adopt_light(other_area_lamp, area_id=lounge.id)

    result = coordinator.async_list_promoted_switch_entity_ids([kitchen.id])

    assert result == [lamp_switch]


async def test_light_view_exposes_countdown_expires_at(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    """Backs the countdown card's client-side ticking (PRD Revision §4.3) -
    a client needs the absolute expiry, not just a remaining-seconds
    snapshot that goes stale the moment it's read."""
    hass.states.async_set("light.countdown_test", "on")

    view = next(
        v for v in coordinator.async_list_lights() if v.entity_id == "light.countdown_test"
    )
    assert view.countdown_expires_at is None

    await coordinator.countdown.async_start(
        "light.countdown_test", minutes=5, turn_on_first=False
    )

    view = next(
        v for v in coordinator.async_list_lights() if v.entity_id == "light.countdown_test"
    )
    assert view.countdown_expires_at is not None

    await coordinator.countdown.async_cancel("light.countdown_test")


async def test_list_area_lights_only_includes_adopted_non_ignored(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    from homeassistant.helpers import area_registry as ar

    lounge = ar.async_get(hass).async_create("Big Lounge")

    adopted = _register_light(hass, "light.lounge_ceiling", "lounge-ceiling")
    await coordinator.async_adopt_light(adopted, area_id=lounge.id)

    ignored = _register_light(hass, "light.lounge_spare", "lounge-spare")
    await coordinator.async_adopt_light(ignored, area_id=lounge.id)
    await coordinator.async_ignore_light(ignored)

    unadopted = _register_light(hass, "light.lounge_new", "lounge-new")
    await registry.async_set_entity_area(hass, unadopted, lounge.id)

    result = [v.entity_id for v in coordinator.async_list_area_lights(lounge.id)]

    assert result == [adopted]


async def test_list_area_lights_respects_manual_order_with_fallback(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    """Regression test for the "stale/partial override never hides a light" contract.

    A light present when the override was saved keeps its explicit
    position; a light adopted afterwards (never part of the override)
    falls back to alphabetical, appended after the explicitly-ordered
    entries rather than being dropped.
    """
    from homeassistant.helpers import area_registry as ar

    lounge = ar.async_get(hass).async_create("Big Lounge")

    a = _register_light(hass, "light.aaa", "aaa")
    b = _register_light(hass, "light.bbb", "bbb")
    c = _register_light(hass, "light.ccc", "ccc")
    for entity_id in (a, b, c):
        await coordinator.async_adopt_light(entity_id, area_id=lounge.id)

    await coordinator.async_set_area_light_order(lounge.id, [c, a])

    result = [v.entity_id for v in coordinator.async_list_area_lights(lounge.id)]

    assert result == [c, a, b]


async def test_rename_on_registry_less_entity_raises(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    """Documents a real, current gap (not something this test suite papers over).

    stable_key_for_entity's own docstring acknowledges entities with no
    entity-registry entry exist (template/YAML lights). Storage identity
    has a fallback for that case, but rename/move do not: they call
    entity_registry.async_update_entity() directly, which raises KeyError
    for an entity that was never registered. Recorded here as a known
    limitation worth a product decision (silent no-op? friendly error?)
    rather than left to surface as an unhandled crash the first time a
    user tries to rename a registry-less light.
    """
    hass.states.async_set("light.template_light", "off")

    with pytest.raises(KeyError):
        await coordinator.async_rename_light("light.template_light", "New Name")


# -- light replacement (PRD §24) -------------------------------------------


@pytest.fixture(autouse=True)
def _fast_state_clear(monkeypatch):
    monkeypatch.setattr(registry, "STATE_CLEAR_TIMEOUT", 0.2)


async def _adopted_old_light(hass, coordinator, *, state="unavailable"):
    from homeassistant.helpers import area_registry as ar

    area = ar.async_get(hass).async_create("Kitchen")
    old = _register_light(hass, "light.kitchen_lamp", "old-1")
    await coordinator.async_adopt_light(old, name="Kitchen lamp", area_id=area.id, light_type="table_lamp")
    await coordinator.async_set_tile_width(old, "full")
    await coordinator.async_set_brightness_slider(old, True)
    await coordinator.async_set_scheduling_included(old, False)
    await coordinator.async_set_icon(old, "mdi:floor-lamp")
    hass.states.async_set(old, state)
    return old, area


async def test_replace_dead_light_takes_over_id_and_settings(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, area = await _adopted_old_light(hass, coordinator)
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    ent_reg = er.async_get(hass)

    result = await coordinator.async_replace_light(old, new)

    assert result["entity_id"] == old
    assert result["outcome"] == "removed"
    # The new entry now owns the old entity ID, and nothing is left aside.
    entry = ent_reg.async_get(old)
    assert entry.unique_id == "new-1"
    assert ent_reg.async_get(new) is None
    assert ent_reg.async_get(result["old_entity_id_now"]) is None
    # Registry settings carried over.
    assert entry.name == "Kitchen lamp"
    assert entry.icon == "mdi:floor-lamp"
    assert entry.area_id == area.id
    assert registry.get_light_type(hass, old) == "table_lamp"
    # Lighting Manager settings carried over; old record gone.
    record = coordinator.store.get_light("test_platform:new-1")
    assert record.adopted and not record.ignored
    assert record.tile_width == "full"
    assert record.brightness_slider is True
    assert record.scheduling_included is False
    assert "test_platform:old-1" not in coordinator.store.data.lights


async def test_replace_working_light_disables_old_one(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    ent_reg = er.async_get(hass)

    result = await coordinator.async_replace_light(old, new)

    assert result["outcome"] == "disabled"
    assert result["old_entity_id_now"] == f"{old}_replaced"
    aside = ent_reg.async_get(result["old_entity_id_now"])
    assert aside is not None
    assert aside.unique_id == "old-1"
    assert aside.disabled_by == er.RegistryEntryDisabler.USER
    assert ent_reg.async_get(old).unique_id == "new-1"


async def test_replace_keeps_new_lights_own_area_when_same(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, area = await _adopted_old_light(hass, coordinator)
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    await registry.async_set_entity_area(hass, new, area.id)

    await coordinator.async_replace_light(old, new)

    assert registry.async_get_effective_area(hass, old) == area.id


async def test_replace_cancels_countdown_and_clears_inbox_entry(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    from pytest_homeassistant_custom_component.common import async_mock_service

    async_mock_service(hass, "light", "turn_on")
    await coordinator.countdown.async_start(old, 30)
    assert coordinator.store.get_countdown(old) is not None

    await coordinator.async_replace_light(old, new)

    assert coordinator.store.get_countdown(old) is None
    # (A real integration moves its own state when the entity is renamed;
    # these bare test states have no integration behind them.)
    hass.states.async_remove(new)
    assert not any(i.condition == INBOX_NEW for i in coordinator.async_build_inbox())


async def test_replace_merges_type_label_without_duplicating(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, _ = await _adopted_old_light(hass, coordinator)
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")
    await registry.async_set_light_type(hass, new, "ceiling")

    await coordinator.async_replace_light(old, new)

    labels = er.async_get(hass).async_get(old).labels
    known = set(registry._label_id_cache(hass).values())
    assert len(set(labels) & known) == 1
    assert registry.get_light_type(hass, old) == "table_lamp"


async def test_replace_rejects_bad_requests(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, _ = await _adopted_old_light(hass, coordinator)
    new = _register_light(hass, "light.zigbee_bulb_7", "new-1")

    with pytest.raises(ValueError):
        await coordinator.async_replace_light(old, old)
    with pytest.raises(ValueError):
        await coordinator.async_replace_light(old, "light.no_such_light")
    # An already-managed light cannot be the "new" one: its own schedules would break.
    await coordinator.async_adopt_light(new)
    with pytest.raises(ValueError):
        await coordinator.async_replace_light(old, new)
    # Cross-domain replacement can't keep the entity ID.
    sw = _register_light(hass, "switch.plug", "sw-1")
    other = _register_light(hass, "light.other_new", "new-2")
    with pytest.raises(ValueError):
        await coordinator.async_replace_light(other, sw)
    # Nothing was changed by the failed attempts.
    assert er.async_get(hass).async_get(old).unique_id == "old-1"


# -- replacing a light with a switch (a plug): the light is created for you ----


async def _promoted_plug(hass, coordinator, entity_id="switch.plug", unique_id="plug-1"):
    plug = _register_light(hass, entity_id, unique_id)
    await coordinator.async_promote_switch(plug)
    return plug


async def test_replace_with_switch_requires_explicit_conversion(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, _ = await _adopted_old_light(hass, coordinator)
    plug = await _promoted_plug(hass, coordinator)

    with pytest.raises(ValueError, match="same type"):
        await coordinator.async_replace_light(old, plug)
    # Nothing was created or renamed.
    assert hass.config_entries.async_entries("switch_as_x") == []
    assert er.async_get(hass).async_get(old).unique_id == "old-1"


async def test_replace_light_with_switch_creates_light_with_old_name_and_id(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, area = await _adopted_old_light(hass, coordinator, state="on")
    plug = await _promoted_plug(hass, coordinator)
    ent_reg = er.async_get(hass)

    result = await coordinator.async_replace_light(old, plug, convert_switch=True)
    await hass.async_block_till_done()

    assert result["converted"] is True
    assert result["created_from"] == plug
    assert result["outcome"] == "disabled"
    # The new light owns the old entity ID, name, icon and room...
    new_light = ent_reg.async_get(old)
    assert new_light.platform == "switch_as_x"
    assert new_light.name == "Kitchen lamp"
    assert new_light.icon == "mdi:floor-lamp"
    assert new_light.area_id == area.id
    assert registry.get_light_type(hass, old) == "table_lamp"
    # ...the old light was renamed first and disabled, not deleted...
    aside = ent_reg.async_get(result["old_entity_id_now"])
    assert aside.unique_id == "old-1"
    assert aside.name == "Kitchen lamp (replaced)"
    assert aside.disabled_by == er.RegistryEntryDisabler.USER
    # ...the plug is hidden behind the new light, and not listed twice.
    assert ent_reg.async_get(plug).hidden_by is not None
    record = coordinator.store.get_light(registry.stable_key_for_entity(hass, old))
    assert record.adopted and record.tile_width == "full"
    assert "test_platform:plug-1" not in coordinator.store.data.lights
    assert "test_platform:old-1" not in coordinator.store.data.lights


async def test_replace_dead_light_with_switch_removes_old_one(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, _ = await _adopted_old_light(hass, coordinator)
    plug = await _promoted_plug(hass, coordinator)

    result = await coordinator.async_replace_light(old, plug, convert_switch=True)

    assert result["outcome"] == "removed"
    assert er.async_get(hass).async_get(result["old_entity_id_now"]) is None
    assert er.async_get(hass).async_get(old).platform == "switch_as_x"


async def test_switch_that_is_already_wrapped_cannot_be_converted_again(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old, _ = await _adopted_old_light(hass, coordinator)
    plug = await _promoted_plug(hass, coordinator)
    other_old = _register_light(hass, "light.hall", "old-2")
    await coordinator.async_adopt_light(other_old, name="Hall")
    await coordinator.async_replace_light(old, plug, convert_switch=True)
    await hass.async_block_till_done()

    with pytest.raises(ValueError, match="already wrapped"):
        await coordinator.async_replace_light(other_old, plug, convert_switch=True)
    assert er.async_get(hass).async_get(other_old).name is None or (
        er.async_get(hass).async_get(other_old).name == "Hall"
    )


async def test_switch_cannot_replace_a_switch_via_conversion(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator
) -> None:
    old_plug = await _promoted_plug(hass, coordinator, "switch.old_plug", "plug-0")
    await coordinator.async_adopt_light(old_plug, name="Old plug")
    new_plug = await _promoted_plug(hass, coordinator)
    # Two switches of the same type simply replace each other; no helper needed.
    result = await coordinator.async_replace_light(old_plug, new_plug, convert_switch=True)
    assert result["converted"] is False
    assert hass.config_entries.async_entries("switch_as_x") == []


async def test_failed_light_creation_leaves_old_light_untouched(
    hass: HomeAssistant, coordinator: LightingManagerCoordinator, monkeypatch
) -> None:
    old, _ = await _adopted_old_light(hass, coordinator, state="on")
    plug = await _promoted_plug(hass, coordinator)

    async def _boom(*_args, **_kwargs):
        raise ValueError("Home Assistant's \"Switch as X\" helper isn't available")

    monkeypatch.setattr(registry, "async_create_light_from_switch", _boom)

    with pytest.raises(ValueError, match="isn't available"):
        await coordinator.async_replace_light(old, plug, convert_switch=True)

    entry = er.async_get(hass).async_get(old)
    assert entry is not None and entry.unique_id == "old-1"
    assert entry.name == "Kitchen lamp"
    assert er.async_get(hass).async_get(f"{old}_replaced") is None
    assert coordinator.store.get_light("test_platform:old-1").adopted
    assert coordinator.store.get_light("test_platform:plug-1").promoted_switch
