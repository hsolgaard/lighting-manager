"""Tests for storage.py: the persisted-metadata wrapper (PRD §25)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.lighting_manager.storage import (
    CountdownRecord,
    LightingManagerStore,
    ManagedLightRecord,
)


async def test_get_light_returns_defaults_when_unknown(hass: HomeAssistant) -> None:
    store = LightingManagerStore(hass)
    await store.async_load()

    record = store.get_light("nonexistent_key")

    assert record == ManagedLightRecord()
    assert record.adopted is False


async def test_set_and_get_light_round_trips(hass: HomeAssistant) -> None:
    store = LightingManagerStore(hass)
    await store.async_load()

    record = ManagedLightRecord(adopted=True, light_type_label="ceiling")
    await store.async_set_light("platform:abc", record)

    fetched = store.get_light("platform:abc")
    assert fetched.adopted is True
    assert fetched.light_type_label == "ceiling"


async def test_light_data_persists_across_store_reload(hass: HomeAssistant) -> None:
    """Simulates a restart: a fresh LightingManagerStore against the same hass."""
    store = LightingManagerStore(hass)
    await store.async_load()
    await store.async_set_light("platform:xyz", ManagedLightRecord(adopted=True))

    reloaded_store = LightingManagerStore(hass)
    await reloaded_store.async_load()

    assert reloaded_store.get_light("platform:xyz").adopted is True


async def test_remove_light_clears_record(hass: HomeAssistant) -> None:
    store = LightingManagerStore(hass)
    await store.async_load()
    await store.async_set_light("platform:abc", ManagedLightRecord(adopted=True))

    await store.async_remove_light("platform:abc")

    assert store.get_light("platform:abc") == ManagedLightRecord()


async def test_countdown_round_trip_and_clear(hass: HomeAssistant) -> None:
    store = LightingManagerStore(hass)
    await store.async_load()

    record = CountdownRecord(expires_at="2026-09-14T12:00:00+00:00", action="turn_off")
    await store.async_set_countdown("light.test", record)

    assert store.get_countdown("light.test") == record
    assert store.all_countdowns() == {"light.test": record}

    await store.async_clear_countdown("light.test")

    assert store.get_countdown("light.test") is None
    assert store.all_countdowns() == {}


async def test_get_countdown_returns_none_when_absent(hass: HomeAssistant) -> None:
    store = LightingManagerStore(hass)
    await store.async_load()

    assert store.get_countdown("light.never_started") is None


async def test_area_light_order_defaults_to_empty(hass: HomeAssistant) -> None:
    store = LightingManagerStore(hass)
    await store.async_load()

    assert store.get_area_light_order("unknown_area") == []


async def test_area_light_order_round_trips(hass: HomeAssistant) -> None:
    store = LightingManagerStore(hass)
    await store.async_load()

    await store.async_set_area_light_order("big_lounge", ["light.a", "light.b"])

    assert store.get_area_light_order("big_lounge") == ["light.a", "light.b"]


async def test_area_light_order_persists_across_store_reload(hass: HomeAssistant) -> None:
    store = LightingManagerStore(hass)
    await store.async_load()
    await store.async_set_area_light_order("kitchen", ["light.hob_spots"])

    reloaded_store = LightingManagerStore(hass)
    await reloaded_store.async_load()

    assert reloaded_store.get_area_light_order("kitchen") == ["light.hob_spots"]
