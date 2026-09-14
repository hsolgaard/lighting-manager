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
