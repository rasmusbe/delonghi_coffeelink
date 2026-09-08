"""Select platform for DeLonghi Coffee Link - the machine's active user profile."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DelonghiCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinators: list[DelonghiCoordinator] = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    for coord in coordinators:
        # The first refresh has already run (__init__.py), so the catalogue is
        # as complete as it will get: a machine that published nothing per
        # profile gets no select at all rather than an empty one - the same
        # skip-at-setup rule the counter sensors follow.
        if not coord.user_profile_slots():
            _LOGGER.debug(
                "Skipping user profile select for dsn=%s: the machine declared no "
                "profile slots",
                coord.device.dsn,
            )
            continue
        entities.append(DelonghiUserProfileSelect(coord))
    async_add_entities(entities)


class _Base(CoordinatorEntity[DelonghiCoordinator], SelectEntity):
    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Stay available across a failed poll - on purpose.

        A round trip through ``unavailable`` on a cloud hiccup fires every
        ``state`` trigger watching the entity (``unavailable -> <profile>``)
        without the machine having done anything. Refusing a command to a
        machine that cannot take it is the coordinator preflight's job
        (``_ensure_machine_reachable``), which raises an error the user sees.
        """
        return True

    @property
    def device_info(self) -> DeviceInfo:
        d = self.coordinator.device
        return DeviceInfo(
            identifiers={(DOMAIN, d.dsn)},
            name=d.name or f"DeLonghi {d.dsn}",
            manufacturer=MANUFACTURER,
            model=d.oem_model or d.model,
            sw_version=d.sw_version,
            configuration_url=f"http://{d.lan_ip}" if d.lan_ip else None,
        )


class DelonghiUserProfileSelect(_Base):
    """The machine's active user profile (the app's profile switcher).

    Options are the profiles the machine's display offers: the slots it gave a
    name cell when the name blob was read, every declared slot as ``Profile N``
    otherwise (see ``catalog_profile_labels`` for why the two differ). Selecting
    one sends the same ``a9 f0`` frame the official app writes; the state
    follows the reply the machine leaves on its response channel. A switch made
    over the cloud (including the app when it uses the cloud) is picked up; one
    made on the panel or by the app over Bluetooth is not, until the machine
    next reports over the cloud.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:account"
    _attr_translation_key = "user_profile"

    def __init__(self, coord: DelonghiCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.device.dsn}_user_profile"

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.user_profile_labels().values())

    @property
    def current_option(self) -> str | None:
        slot = self.coordinator.active_user_profile
        if slot is None:
            return None
        return self.coordinator.user_profile_labels().get(slot)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coord = self.coordinator
        return {
            "profile_slot": coord.active_user_profile,
            "profile_read_at": coord.active_user_profile_at,
            "profile_slots": coord.user_profile_slots(),
            "pending": coord.profile_change_pending,
        }

    async def async_select_option(self, option: str) -> None:
        coord = self.coordinator
        slot = coord.user_profile_slot_for_label(option)
        if slot is None:
            raise coord.unknown_profile_error(option, sorted(coord.user_profile_labels()))
        _LOGGER.info("Selecting user profile %d (%s)", slot, option)
        await coord.async_send_profile(slot)
        self.async_write_ha_state()
