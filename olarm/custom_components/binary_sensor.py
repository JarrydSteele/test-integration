"""Support for Olarm binary sensors (zones)."""
import logging
from typing import Dict, List, Optional, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .api import OlarmApiClient
from .const import DOMAIN, ZONE_ACTIVE, ZONE_BYPASSED

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Olarm binary sensors based on a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]
    
    # Check if MQTT is enabled
    mqtt_enabled = entry_data.get("mqtt_enabled", False)
    message_handler = entry_data.get("message_handler")
    
    # Add binary sensors for each zone in each device
    entities = []
    for device_id, device in coordinator.data.items():
        device_name = device.get("deviceName", "Unknown")
        
        # Check if device has zones
        if "deviceProfile" in device and "zonesLimit" in device["deviceProfile"]:
            zones_limit = device["deviceProfile"]["zonesLimit"]
            zones_labels = device["deviceProfile"].get("zonesLabels", [])
            zones_types = device["deviceProfile"].get("zonesTypes", [])
            
            # Add an entity for each zone
            for zone_num in range(1, zones_limit + 1):
                zone_name = "Unknown"
                if len(zones_labels) >= zone_num:
                    zone_name = zones_labels[zone_num - 1]
                
                # Try to determine device class from zone type
                device_class = BinarySensorDeviceClass.MOTION
                if len(zones_types) >= zone_num:
                    # Map zone_type to device_class if known
                    # This is a placeholder - you may need to adjust based on actual zone types
                    zone_type = zones_types[zone_num - 1]
                    if "door" in zone_name.lower() or "window" in zone_name.lower():
                        device_class = BinarySensorDeviceClass.DOOR
                    
                entities.append(
                    OlarmZoneSensor(
                        coordinator,
                        device_id,
                        device_name,
                        zone_num,
                        zone_name,
                        device_class,
                        message_handler,
                        mqtt_enabled,
                    )
                )
    
    async_add_entities(entities)

class OlarmZoneSensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of an Olarm zone sensor."""

    def __init__(
        self,
        coordinator,
        device_id: str,
        device_name: str,
        zone_num: int,
        zone_name: str,
        device_class: str,
        message_handler = None,
        mqtt_enabled: bool = False,
    ):
        """Initialize the zone sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._device_name = device_name
        self._zone_num = zone_num
        self._zone_name = zone_name
        self._message_handler = message_handler
        self._mqtt_enabled = mqtt_enabled
        self._current_state = None
        self._current_attributes = {}
        
        self._attr_unique_id = f"{device_id}_zone_{zone_num}"
        self._attr_name = f"{device_name} {zone_name}"
        self._attr_device_class = device_class
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_name,
            manufacturer="Olarm",
            model="Olarm Communicator",
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        
        # Register to MQTT updates if available
        if self._mqtt_enabled and self._message_handler:
            @callback
            def handle_mqtt_update(zone_data):
                """Handle MQTT update."""
                if zone_data.get("zone_number") == self._zone_num:
                    self._current_state = zone_data.get("zone_state")
                    if "last_changed" in zone_data:
                        self._current_attributes["last_changed"] = zone_data["last_changed"]
                    self._current_attributes["bypassed"] = (self._current_state == ZONE_BYPASSED)
                    self.async_write_ha_state()
            
            # Subscribe to zone updates
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    f"{DOMAIN}_{self._device_id}_zone_{self._zone_num}",
                    handle_mqtt_update
                )
            )

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if the binary sensor is on."""
        # If we have a current state from MQTT, use it
        if self._mqtt_enabled and self._current_state is not None:
            return self._current_state == ZONE_ACTIVE
        
        # Otherwise, fall back to coordinator data
        if not self.coordinator.data or self._device_id not in self.coordinator.data:
            return None
            
        device = self.coordinator.data[self._device_id]
        
        if (
            "deviceState" not in device
            or "zones" not in device["deviceState"]
            or len(device["deviceState"]["zones"]) < self._zone_num
        ):
            return None
            
        # Get the zone state
        zone_state = device["deviceState"]["zones"][self._zone_num - 1]
        
        # Zone is active if state is "a" (active)
        return zone_state == ZONE_ACTIVE

    @property
    def extra_state_attributes(self) -> Dict[str, bool]:
        """Return the state attributes."""
        # If we have attributes from MQTT, use them
        if self._mqtt_enabled and self._current_attributes:
            return self._current_attributes
        
        # Otherwise, fall back to coordinator data
        if not self.coordinator.data or self._device_id not in self.coordinator.data:
            return {}
            
        device = self.coordinator.data[self._device_id]
        attributes = {}
        
        if (
            "deviceState" in device
            and "zones" in device["deviceState"]
            and len(device["deviceState"]["zones"]) >= self._zone_num
        ):
            zone_state = device["deviceState"]["zones"][self._zone_num - 1]
            attributes["bypassed"] = zone_state == ZONE_BYPASSED
            
            # Add timestamp if available
            if (
                "zonesStamp" in device["deviceState"]
                and len(device["deviceState"]["zonesStamp"]) >= self._zone_num
            ):
                attributes["last_changed"] = device["deviceState"]["zonesStamp"][self._zone_num - 1]
                
        return attributes
