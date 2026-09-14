"""Tests for the Niels Faber Scheduler reverse-index adapter (PRD §18)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.lighting_manager.providers.scheduler.niels_faber import (
    NielsFaberSchedulerProvider,
)


def _set_schedule(
    hass: HomeAssistant, entity_id: str, *, entities: list[str], enabled: bool = True
) -> None:
    hass.states.async_set(
        entity_id,
        "on" if enabled else "off",
        {
            "entities": entities,
            "weekdays": ["mon", "tue"],
            "timeslots": ["18:30"],
            "actions": [{"service": "light.turn_on"}],
        },
    )


async def test_is_available_false_with_no_schedules(hass: HomeAssistant) -> None:
    provider = NielsFaberSchedulerProvider(hass)
    assert provider.is_available() is False


async def test_is_available_true_once_a_schedule_entity_exists(
    hass: HomeAssistant,
) -> None:
    _set_schedule(hass, "switch.schedule_0a2f7e", entities=["light.kitchen"])

    provider = NielsFaberSchedulerProvider(hass)
    assert provider.is_available() is True


async def test_schedules_for_entity_reads_flat_entities_attribute(
    hass: HomeAssistant,
) -> None:
    _set_schedule(
        hass, "switch.schedule_0a2f7e", entities=["light.kitchen", "light.hallway"]
    )
    provider = NielsFaberSchedulerProvider(hass)
    await provider.async_setup()

    schedules = provider.async_schedules_for_entity("light.kitchen")

    assert len(schedules) == 1
    schedule = schedules[0]
    assert schedule.schedule_id == "switch.schedule_0a2f7e"
    assert schedule.target_entity_ids == ["light.kitchen", "light.hallway"]
    assert schedule.shared_target_count == 2
    assert schedule.is_shared is True
    assert schedule.enabled is True

    provider.async_teardown()


async def test_schedules_for_entity_empty_when_not_targeted(
    hass: HomeAssistant,
) -> None:
    _set_schedule(hass, "switch.schedule_0a2f7e", entities=["light.kitchen"])
    provider = NielsFaberSchedulerProvider(hass)
    await provider.async_setup()

    assert provider.async_schedules_for_entity("light.hallway") == []

    provider.async_teardown()


async def test_non_shared_schedule_reports_shared_target_count_one(
    hass: HomeAssistant,
) -> None:
    _set_schedule(hass, "switch.schedule_0a2f7e", entities=["light.kitchen"])
    provider = NielsFaberSchedulerProvider(hass)
    await provider.async_setup()

    schedule = provider.async_schedules_for_entity("light.kitchen")[0]
    assert schedule.shared_target_count == 1
    assert schedule.is_shared is False

    provider.async_teardown()


async def test_index_rebuilds_when_schedule_changes(hass: HomeAssistant) -> None:
    _set_schedule(hass, "switch.schedule_0a2f7e", entities=["light.kitchen"])
    provider = NielsFaberSchedulerProvider(hass)
    await provider.async_setup()

    # Edit the schedule to add a second target - Scheduler Component
    # fires an ordinary state_changed event for this (confirmed live
    # against Hans's instance), which should trigger a re-index.
    _set_schedule(
        hass, "switch.schedule_0a2f7e", entities=["light.kitchen", "light.hallway"]
    )
    await hass.async_block_till_done()

    schedule = provider.async_schedules_for_entity("light.kitchen")[0]
    assert schedule.target_entity_ids == ["light.kitchen", "light.hallway"]

    provider.async_teardown()


async def test_non_schedule_switch_entities_are_ignored(hass: HomeAssistant) -> None:
    hass.states.async_set("switch.kettle", "off")
    _set_schedule(hass, "switch.schedule_0a2f7e", entities=["light.kitchen"])

    provider = NielsFaberSchedulerProvider(hass)
    await provider.async_setup()

    all_schedules = provider.async_all_schedules()
    assert len(all_schedules) == 1
    assert all_schedules[0].schedule_id == "switch.schedule_0a2f7e"

    provider.async_teardown()
