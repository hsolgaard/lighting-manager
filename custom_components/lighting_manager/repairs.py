"""HA Repairs integration (PRD §17).

Decision recorded in the PRD: "Lighting Manager should use Home
Assistant Repairs where an issue is persistent, system-level and
appropriate for the HA repair model. Day-to-day lighting administration
should remain in the Lighting Manager Inbox." Routine per-light issues
(unassigned, area mismatch, missing) are deliberately Inbox-only and
never create a Repairs issue - only the two candidates the PRD names
explicitly do:

- corrupt manager configuration (storage failed to load/migrate)
- persistent provider failure (scheduler provider was available, then
  disappeared or started erroring repeatedly)
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def async_create_storage_corrupt_issue(hass: HomeAssistant, detail: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        "storage_corrupt",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="storage_corrupt",
        translation_placeholders={"detail": detail},
    )


def async_create_provider_failure_issue(hass: HomeAssistant, provider_id: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"provider_failure_{provider_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="provider_failure",
        translation_placeholders={"provider": provider_id},
    )


def async_clear_provider_failure_issue(hass: HomeAssistant, provider_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, f"provider_failure_{provider_id}")
