"""Scheduler provider abstraction (PRD §18, §21).

Lighting Manager normalises just enough from a scheduler provider to
drive its own UI (PRD §18: "expose a normalised schedule model
containing ID, provider, enabled state, targets, days, start time,
actions and shared-target count"). It does not attempt to reimplement
or fully mirror the provider's own data model - the provider remains
the source of truth and retains its own execution semantics (PRD §21);
Lighting Manager only needs enough to answer "which schedules affect
this light" and "is this schedule shared".
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from homeassistant.core import HomeAssistant


@dataclass
class NormalisedSchedule:
    """PRD §18/§20 normalised schedule model, provider-agnostic."""

    schedule_id: str
    provider: str
    enabled: bool
    target_entity_ids: list[str]
    weekdays: list[str]
    start_times: list[str]
    actions: list[str]

    @property
    def shared_target_count(self) -> int:
        return len(self.target_entity_ids)

    @property
    def is_shared(self) -> bool:
        return self.shared_target_count > 1


class SchedulerProvider(ABC):
    """Interface a scheduler adapter must implement."""

    provider_id: str

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    @abstractmethod
    def is_available(self) -> bool:
        """Whether the underlying integration is currently installed/loaded."""

    @abstractmethod
    def async_schedules_for_entity(self, entity_id: str) -> list[NormalisedSchedule]:
        """All schedules (across the provider) that target this entity.

        Synchronous by design: implementations should serve this from an
        in-memory reverse index maintained by listening to state-change
        events, not by querying the provider live on every call (PRD
        §18/§29 - Scheduler Component has no query API for this, so the
        index has to be built and kept fresh by Lighting Manager itself).
        """

    @abstractmethod
    def async_all_schedules(self) -> list[NormalisedSchedule]:
        """Every schedule the provider currently has, normalised."""

    @abstractmethod
    async def async_set_enabled(self, schedule_id: str, enabled: bool) -> None:
        """Enable/disable a schedule (PRD §18)."""

    @abstractmethod
    def deep_link_for_schedule(self, schedule_id: str) -> str | None:
        """A path/URL Lighting Manager's UI can use to 'open the provider editor' (PRD §18)."""
