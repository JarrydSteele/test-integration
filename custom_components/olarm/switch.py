"""Support for Olarm switches (PGMs)."""
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import OlarmApiClient, OlarmApiError
from .const import CMD_PGM_CLOSE, CMD_PGM_OPEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Olarm switches based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator = data["coordinator"]
    
    # Add switches for each PGM in each device
    entities = []
    for device_id, device in coordinator.data.items():
        device_name = device.get("deviceName", "Unknown")
        
        # Check if device has PGMs
        if "deviceProfile" in device and "pgmLimit" in device["deviceProfile"]:
            pgm_limit = device["deviceProfile"]["pgmLimit"]
            pgm_labels = device["deviceProfile"].get("pgmLabels", [])
            pgm_control = device["deviceProfile"].get("pgmControl", [])
            
            # Add an entity for each PGM that's enabled for control
            for pgm_num in range(1, pgm_limit + 1):
                # Check if PGM is controllable
                is_controllable = False
                if len(pgm_control) >= pgm_num:
                    # Format according to API: Char 1 is enabled, Char 2 is open/close
                    control_config = pgm_control[pgm_num - 1]
                    if len(control_config) >= 2 and control_config[0] == "1" and control_config[1] == "1":
                        is_controllable = True
                
                # Skip PGMs that aren't controllable
                if not is_controllable:
                    continue
                    
                pgm_name = "PGM"
                if len(pgm_labels) >= pgm_num and pgm_labels[pgm_num - 1]:
                    pgm_name = pgm_labels[pgm_num - 1]
                
                entities.append(
                    OlarmPgmSwitch(
                        coordinator,
                        client,
                        device_id,
                        device_name,
                        pgm_num,
                        pgm_name,
                    )
                )
    
    async_add_entities(entities)

class OlarmPgmSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of an Olarm PGM switch."""

    def __init__(
        self,
        coordinator,
        client: OlarmApiClient,
        device_id: str,
        device_name: str,
        pgm_num: int,
        pgm_name: str,
    ):
        """Initialize the PGM switch."""
        super().__init__(coordinator)
        self._client = client
        self._device_id = device_id
        self._device_name = device_name
        self._pgm_num = pgm_num
        self._pgm_name = pgm_name
        self._attr_unique_id = f"{device_id}_pgm_{pgm_num}"
        self._attr_name = f"{device_name} {pgm_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_name,
            manufacturer="Olarm",
            model="Olarm Communicator",
        )

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if the switch is on."""
        if not self.coordinator.data or self._device_id not in self.coordinator.data:
            return None
            
        device = self.coordinator.data[self._device_id]
        
        if (
            "deviceState" not in device
            or "pgm" not in device["deviceState"]
            or len(device["deviceState"]["pgm"]) < self._pgm_num
        ):
            return None
            
        # Assuming "c" means closed/on and anything else is open/off
        # You may need to adjust this logic based on your understanding of the API
        pgm_state = device["deviceState"]["pgm"][self._pgm_num - 1]
        return pgm_state == "c"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            await self._client.send_device_action(
                self._device_id, CMD_PGM_CLOSE, self._pgm_num
            )
            await self.coordinator.async_request_refresh()
        except OlarmApiError as err:
            _LOGGER.error("Error turning on PGM %s: %s", self._pgm_num, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            await self._client.send_device_action(
                self._device_id, CMD_PGM_OPEN, self._pgm_num
            )
            await self.coordinator.async_request_refresh()
        except OlarmApiError as err:
            _LOGGER.error("Error turning off PGM %s: %s", self._pgm_num, err)
