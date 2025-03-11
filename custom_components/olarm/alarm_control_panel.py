"""Support for Olarm alarm control panels."""
import logging
from typing import Any, Dict, Optional, Callable

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .api import OlarmApiClient, OlarmApiError
from .mqtt import OlarmMqttClient
from .const import (
    ATTR_AC_POWER,
    ATTR_BATTERY,
    CMD_ARM_AWAY,
    CMD_ARM_HOME,
    CMD_ARM_NIGHT,
    CMD_DISARM,
    DOMAIN,
    MQTT_CMD_ARM_AWAY,
    MQTT_CMD_ARM_HOME,
    MQTT_CMD_ARM_NIGHT,
    MQTT_CMD_DISARM,
    STATE_ARMED_AWAY,
    STATE_ARMED_HOME,
    STATE_ARMED_NIGHT,
    STATE_DISARMED,
    STATE_PENDING,
    STATE_TRIGGERED,
)

_LOGGER = logging.getLogger(__name__)

# Map Olarm states to HA AlarmControlPanelState enum
OLARM_TO_HA_STATE = {
    STATE_DISARMED: AlarmControlPanelState.DISARMED,
    STATE_ARMED_AWAY: AlarmControlPanelState.ARMED_AWAY,
    STATE_ARMED_HOME: AlarmControlPanelState.ARMED_HOME,
    STATE_ARMED_NIGHT: AlarmControlPanelState.ARMED_NIGHT,
    STATE_TRIGGERED: AlarmControlPanelState.TRIGGERED,
    STATE_PENDING: AlarmControlPanelState.ARMING,
}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Olarm alarm control panel based on a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]
    
    entities = []
    
    # Check if MQTT is enabled
    mqtt_enabled = entry_data.get("mqtt_enabled", False)
    mqtt_clients = entry_data.get("mqtt_clients", {})
    message_handler = entry_data.get("message_handler")
    
    # Add alarm panels for each area in each device
    for device_id, device in coordinator.data.items():
        device_name = device.get("deviceName", "Unknown")
        
        # Check if device has areas
        if "deviceProfile" in device and "areasLimit" in device["deviceProfile"]:
            areas_limit = device["deviceProfile"]["areasLimit"]
            areas_labels = device["deviceProfile"].get("areasLabels", [])
            
            # Add an entity for each area
            for area_num in range(1, areas_limit + 1):
                area_name = "Unknown"
                if len(areas_labels) >= area_num:
                    area_name = areas_labels[area_num - 1]
                
                # If MQTT is available, get the MQTT client for this device
                mqtt_client = mqtt_clients.get(device_id) if mqtt_enabled else None
                
                entities.append(
                    OlarmAlarmPanel(
                        coordinator,
                        client,
                        device_id,
                        device_name,
                        area_num,
                        area_name,
                        mqtt_client,
                        message_handler,
                        mqtt_enabled,
                    )
                )
    
    async_add_entities(entities)

class OlarmAlarmPanel(CoordinatorEntity, AlarmControlPanelEntity):
    """Representation of an Olarm alarm panel."""

    def __init__(
        self,
        coordinator,
        client: OlarmApiClient,
        device_id: str,
        device_name: str,
        area_num: int,
        area_name: str,
        mqtt_client: Optional[OlarmMqttClient] = None,
        message_handler = None,
        mqtt_enabled: bool = False,
    ):
        """Initialize the alarm panel."""
        super().__init__(coordinator)
        self._client = client
        self._device_id = device_id
        self._device_name = device_name
        self._area_num = area_num
        self._area_name = area_name
        self._mqtt_client = mqtt_client
        self._message_handler = message_handler
        self._mqtt_enabled = mqtt_enabled
        self._current_state = None
        
        self._attr_unique_id = f"{device_id}_area_{area_num}"
        self._attr_name = f"{device_name} {area_name}"
        self._attr_supported_features = (
            AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_NIGHT
        )
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
            def handle_mqtt_update(area_data):
                """Handle MQTT update."""
                if area_data.get("area_number") == self._area_num:
                    self._current_state = area_data.get("area_state")
                    self.async_write_ha_state()
            
            # Subscribe to area updates
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    f"{DOMAIN}_{self._device_id}_area_{self._area_num}",
                    handle_mqtt_update
                )
            )

    @property
    def alarm_state(self) -> AlarmControlPanelState:
        """Return the state of the alarm control panel as an enum value."""
        olarm_state = self._get_olarm_state()
        if olarm_state is None:
            return None
        
        # Map Olarm state to HA enum state
        return OLARM_TO_HA_STATE.get(olarm_state)

    def _get_olarm_state(self) -> Optional[str]:
        """Get the current Olarm state string."""
        # If we have a current state from MQTT, use it
        if self._mqtt_enabled and self._current_state:
            return self._current_state
        
        # Otherwise, fall back to coordinator data
        if not self.coordinator.data or self._device_id not in self.coordinator.data:
            return None
            
        device = self.coordinator.data[self._device_id]
        
        if (
            "deviceState" not in device
            or "areas" not in device["deviceState"]
            or len(device["deviceState"]["areas"]) < self._area_num
        ):
            return None
            
        # Get the area state
        return device["deviceState"]["areas"][self._area_num - 1]

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes."""
        # Try to get attributes from MQTT data
        if self._mqtt_enabled and self._message_handler:
            device_state = self._message_handler.get_device_state(self._device_id)
            if device_state and "power" in device_state:
                attributes = {}
                power = device_state["power"]
                if "AC" in power:
                    attributes[ATTR_AC_POWER] = power["AC"] == "1"
                if "Batt" in power:
                    attributes[ATTR_BATTERY] = power["Batt"] == "1"
                return attributes
        
        # Fall back to coordinator data
        if not self.coordinator.data or self._device_id not in self.coordinator.data:
            return {}
            
        device = self.coordinator.data[self._device_id]
        attributes = {}
        
        # Add power information if available
        if (
            "deviceState" in device
            and "power" in device["deviceState"]
        ):
            power = device["deviceState"]["power"]
            if "AC" in power:
                attributes[ATTR_AC_POWER] = power["AC"] == "1"
            if "Batt" in power:
                attributes[ATTR_BATTERY] = power["Batt"] == "1"
                
        return attributes

    async def async_alarm_disarm(self, code: Optional[str] = None) -> None:
        """Send disarm command."""
        # Try MQTT first if available
        if self._mqtt_enabled and self._mqtt_client and self._mqtt_client.is_connected:
            _LOGGER.info("🔄 Using MQTT to disarm area %s on device %s", 
                        self._area_num, self._device_name)
            success = self._mqtt_client.publish_action(MQTT_CMD_DISARM, self._area_num)
            if success:
                return
            _LOGGER.warning("⚠️ MQTT disarm failed, falling back to API")
        else:
            if self._mqtt_enabled:
                _LOGGER.info("ℹ️ MQTT client not connected, using API for disarm")
            else:
                _LOGGER.debug("Using API for disarm (MQTT not enabled)")
        
        # Fall back to API
        try:
            _LOGGER.info("🔄 Using API to disarm area %s on device %s", 
                        self._area_num, self._device_name)
            await self._client.send_device_action(
                self._device_id, CMD_DISARM, self._area_num
            )
            _LOGGER.info("✅ API disarm command sent successfully")
            await self.coordinator.async_request_refresh()
        except OlarmApiError as err:
            _LOGGER.error("❌ Error disarming alarm via API: %s", err)

    async def async_alarm_arm_away(self, code: Optional[str] = None) -> None:
        """Send arm away command."""
        # Try MQTT first if available
        if self._mqtt_enabled and self._mqtt_client and self._mqtt_client.is_connected:
            _LOGGER.info("🔄 Using MQTT to arm away area %s on device %s", 
                        self._area_num, self._device_name)
            success = self._mqtt_client.publish_action(MQTT_CMD_ARM_AWAY, self._area_num)
            if success:
                return
            _LOGGER.warning("⚠️ MQTT arm away failed, falling back to API")
        else:
            if self._mqtt_enabled:
                _LOGGER.info("ℹ️ MQTT client not connected, using API for arm away")
            else:
                _LOGGER.debug("Using API for arm away (MQTT not enabled)")
                
        # Fall back to API
        try:
            _LOGGER.info("🔄 Using API to arm away area %s on device %s", 
                        self._area_num, self._device_name)
            await self._client.send_device_action(
                self._device_id, CMD_ARM_AWAY, self._area_num
            )
            _LOGGER.info("✅ API arm away command sent successfully")
            await self.coordinator.async_request_refresh()
        except OlarmApiError as err:
            _LOGGER.error("❌ Error arming away via API: %s", err)

    async def async_alarm_arm_home(self, code: Optional[str] = None) -> None:
        """Send arm home command."""
        # Try MQTT first if available
        if self._mqtt_enabled and self._mqtt_client and self._mqtt_client.is_connected:
            _LOGGER.info("🔄 Using MQTT to arm home area %s on device %s", 
                        self._area_num, self._device_name)
            success = self._mqtt_client.publish_action(MQTT_CMD_ARM_HOME, self._area_num)
            if success:
                return
            _LOGGER.warning("⚠️ MQTT arm home failed, falling back to API")
        else:
            if self._mqtt_enabled:
                _LOGGER.info("ℹ️ MQTT client not connected, using API for arm home")
            else:
                _LOGGER.debug("Using API for arm home (MQTT not enabled)")
                
        # Fall back to API
        try:
            _LOGGER.info("🔄 Using API to arm home area %s on device %s", 
                        self._area_num, self._device_name)
            await self._client.send_device_action(
                self._device_id, CMD_ARM_HOME, self._area_num
            )
            _LOGGER.info("✅ API arm home command sent successfully")
            await self.coordinator.async_request_refresh()
        except OlarmApiError as err:
            _LOGGER.error("❌ Error arming home via API: %s", err)

    async def async_alarm_arm_night(self, code: Optional[str] = None) -> None:
        """Send arm night command."""
        # Try MQTT first if available
        if self._mqtt_enabled and self._mqtt_client and self._mqtt_client.is_connected:
            _LOGGER.info("🔄 Using MQTT to arm night area %s on device %s", 
                        self._area_num, self._device_name)
            success = self._mqtt_client.publish_action(MQTT_CMD_ARM_NIGHT, self._area_num)
            if success:
                return
            _LOGGER.warning("⚠️ MQTT arm night failed, falling back to API")
        else:
            if self._mqtt_enabled:
                _LOGGER.info("ℹ️ MQTT client not connected, using API for arm night")
            else:
                _LOGGER.debug("Using API for arm night (MQTT not enabled)")
                
        # Fall back to API
        try:
            _LOGGER.info("🔄 Using API to arm night area %s on device %s", 
                        self._area_num, self._device_name)
            await self._client.send_device_action(
                self._device_id, CMD_ARM_NIGHT, self._area_num
            )
            _LOGGER.info("✅ API arm night command sent successfully")
            await self.coordinator.async_request_refresh()
        except OlarmApiError as err:
            _LOGGER.error("❌ Error arming night via API: %s", err)