"""Tests for registry.py: discovery, effective Area, and the Labels taxonomy.

These exercise the code paths that produced the first real bug found
during the build (LabelRegistry.async_create() rejecting a caller-chosen
label_id) - see the Feasibility Review's "Build kickoff" section.
"""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lighting_manager import registry
from custom_components.lighting_manager.const import LIGHT_TYPES


@pytest.fixture
def config_entry_id(hass: HomeAssistant) -> str:
    """A real, hass-registered config entry id devices can link to."""
    entry = MockConfigEntry(domain="fake_platform")
    entry.add_to_hass(hass)
    return entry.entry_id


async def test_list_eligible_lights_only_light_domain(hass: HomeAssistant) -> None:
    hass.states.async_set("light.kitchen", "on")
    hass.states.async_set("light.hallway", "off")
    hass.states.async_set("switch.kettle", "off")

    lights = registry.async_list_eligible_lights(hass)

    assert set(lights) == {"light.kitchen", "light.hallway"}


async def test_list_promotable_switches_is_switch_domain(hass: HomeAssistant) -> None:
    hass.states.async_set("switch.kettle", "off")
    hass.states.async_set("switch.wall_lamp", "off")
    hass.states.async_set("light.kitchen", "on")

    switches = registry.async_list_promotable_switches(hass)

    assert set(switches) == {"switch.kettle", "switch.wall_lamp"}


async def test_stable_key_prefers_unique_id(hass: HomeAssistant) -> None:
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-abc"
    )

    key = registry.stable_key_for_entity(hass, entry.entity_id)

    assert key == "test_platform:unique-abc"


async def test_stable_key_falls_back_to_entity_id(hass: HomeAssistant) -> None:
    # No registry entry at all for this entity_id (e.g. a template light).
    key = registry.stable_key_for_entity(hass, "light.no_registry_entry")

    assert key == "entity_id:light.no_registry_entry"


async def test_effective_area_prefers_entity_override(
    hass: HomeAssistant, config_entry_id: str
) -> None:
    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_area = area_registry.async_create("Device Area")
    entity_area = area_registry.async_create("Entity Area")
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry_id,
        identifiers={("fake_platform", "device-1")},
    )
    device_registry.async_update_device(device.id, area_id=device_area.id)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-1", device_id=device.id
    )
    entity_registry.async_update_entity(entry.entity_id, area_id=entity_area.id)

    effective = registry.async_get_effective_area(hass, entry.entity_id)

    assert effective == entity_area.id


async def test_effective_area_falls_back_to_device(
    hass: HomeAssistant, config_entry_id: str
) -> None:
    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_area = area_registry.async_create("Device Only Area")
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry_id,
        identifiers={("fake_platform", "device-2")},
    )
    device_registry.async_update_device(device.id, area_id=device_area.id)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-2", device_id=device.id
    )

    effective = registry.async_get_effective_area(hass, entry.entity_id)

    assert effective == device_area.id


async def test_area_mismatch_detected_when_entity_overrides_device(
    hass: HomeAssistant, config_entry_id: str
) -> None:
    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_area = area_registry.async_create("Hans Office")
    entity_area = area_registry.async_create("Lounge Big")
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry_id,
        identifiers={("fake_platform", "device-3")},
    )
    device_registry.async_update_device(device.id, area_id=device_area.id)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-3", device_id=device.id
    )
    entity_registry.async_update_entity(entry.entity_id, area_id=entity_area.id)

    mismatch = registry.async_get_area_mismatch(hass, entry.entity_id)

    assert mismatch == (device_area.id, entity_area.id)


async def test_no_mismatch_when_entity_only_inherits_device_area(
    hass: HomeAssistant, config_entry_id: str
) -> None:
    area_registry = ar.async_get(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_area = area_registry.async_create("Only Device Area")
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry_id,
        identifiers={("fake_platform", "device-4")},
    )
    device_registry.async_update_device(device.id, area_id=device_area.id)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-4", device_id=device.id
    )

    mismatch = registry.async_get_area_mismatch(hass, entry.entity_id)

    assert mismatch is None


async def test_rename_entity_sets_registry_name(hass: HomeAssistant) -> None:
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-5"
    )

    await registry.async_rename_entity(hass, entry.entity_id, "New Name")

    updated = entity_registry.async_get(entry.entity_id)
    assert updated.name == "New Name"


async def test_ensure_type_labels_creates_full_controlled_set(
    hass: HomeAssistant,
) -> None:
    """Regression test for the LabelRegistry.async_create(label_id=...) bug.

    The original code assumed a caller-chosen label_id ("lighting-manager:
    <type>") was accepted; the real API generates the id from `name` and
    raises TypeError on an unexpected label_id kwarg. This just calling
    the real (test) LabelRegistry is enough to catch that class of bug
    without any mocking.
    """
    label_registry = lr.async_get(hass)

    await registry.async_ensure_type_labels_exist(hass)

    created_names = {label.name for label in label_registry.async_list_labels()}
    for light_type in LIGHT_TYPES:
        expected_name = f"Lighting: {light_type.replace('_', ' ').title()}"
        assert expected_name in created_names


async def test_ensure_type_labels_is_idempotent(hass: HomeAssistant) -> None:
    label_registry = lr.async_get(hass)

    await registry.async_ensure_type_labels_exist(hass)
    await registry.async_ensure_type_labels_exist(hass)

    names = [label.name for label in label_registry.async_list_labels()]
    assert len(names) == len(set(names)) == len(LIGHT_TYPES)


async def test_set_light_type_applies_single_label(hass: HomeAssistant) -> None:
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-6"
    )

    await registry.async_set_light_type(hass, entry.entity_id, "table_lamp")

    assert registry.get_light_type(hass, entry.entity_id) == "table_lamp"


async def test_set_light_type_enforces_single_label(hass: HomeAssistant) -> None:
    """Changing type should remove the previous type label, not stack them."""
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-7"
    )

    await registry.async_set_light_type(hass, entry.entity_id, "table_lamp")
    await registry.async_set_light_type(hass, entry.entity_id, "ceiling")

    updated = entity_registry.async_get(entry.entity_id)
    label_registry = lr.async_get(hass)
    type_label_names = {
        label_registry.async_get_label(label_id).name for label_id in updated.labels
    }
    assert type_label_names == {"Lighting: Ceiling"}
    assert registry.get_light_type(hass, entry.entity_id) == "ceiling"


async def test_set_light_type_none_clears_label(hass: HomeAssistant) -> None:
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-8"
    )

    await registry.async_set_light_type(hass, entry.entity_id, "outdoor")
    await registry.async_set_light_type(hass, entry.entity_id, None)

    assert registry.get_light_type(hass, entry.entity_id) is None


async def test_set_light_type_rejects_unknown_type(hass: HomeAssistant) -> None:
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create(
        "light", "test_platform", "unique-9"
    )

    try:
        await registry.async_set_light_type(hass, entry.entity_id, "not_a_real_type")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for an unknown light type")
