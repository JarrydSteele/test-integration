"""Handler for Olarm API and MQTT messages."""
import logging
import json
from typing import Dict, List, Optional, Any, Union

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    STATE_DISARMED,
    STATE_ARMED_AWAY,
    STATE_ARMED_HOME,
    STATE_ARMED_NIGHT,
    STATE_TRIGGERED,
    SIGNAL_OLARM_MQTT_UPDATE,
)

_LOGGER = logging.getLogger(__name__)

class OlarmMessageHandler:
    """Handler for Olarm messages."""

    def __init__(self, hass: HomeAssistant, entry_id: str):
        """Initialize the handler."""
        self.hass = hass
        self.entry_id = entry_id
        self.device_state = {}
        self.device_areas = {}
        self.device_zones = {}

    async def process_mqtt_message(self, device_id: str, topic: str, payload: str) -> None:
        """Process incoming MQTT message."""
        try:
            data = json.loads(payload)
            
            # Process alarm payload
            if data.get("type") == "alarmPayload":
                _LOGGER.warning("📩 MQTT: Received alarm state update for device %s", device_id)
                await self._process_alarm_payload(device_id, data)
            else:
                _LOGGER.debug("MQTT: Received non-alarm message type: %s for device %s", 
                             data.get("type"), device_id)
        
        except json.JSONDecodeError:
            _LOGGER.error("❌ MQTT: Failed to parse message as JSON: %s", payload[:100])
        except Exception as ex:
            _LOGGER.error("❌ MQTT: Error processing message: %s", ex)

    async def _process_alarm_payload(self, device_id: str, payload: dict) -> None:
        """Process alarm payload from MQTT."""
        if "data" not in payload:
            _LOGGER.warning("⚠️ MQTT: Alarm payload missing data field")
            return
        
        alarm_data = payload["data"]
        
        # Store full device state
        self.device_state[device_id] = alarm_data
        
        # Process areas
        areas_updated = False
        if "areas" in alarm_data and "areasDetail" in alarm_data:
            areas = []
            for i, area_state in enumerate(alarm_data["areas"]):
                # Get area name (or use default if not available)
                area_name = "Unknown Area"
                if i < len(alarm_data.get("areasDetail", [])) and alarm_data["areasDetail"][i]:
                    area_name = alarm_data["areasDetail"][i]
                else:
                    area_name = f"Area {i + 1}"
                
                # Create area object
                area = {
                    "area_number": i + 1,
                    "area_name": area_name,
                    "area_state": area_state,
                    "device_id": device_id,
                }
                
                # Add timestamp if available
                if "areasStamp" in alarm_data and i < len(alarm_data["areasStamp"]):
                    area["last_changed"] = alarm_data["areasStamp"][i]
                
                areas.append(area)
                
                # Check if state changed
                prev_areas = self.device_areas.get(device_id, [])
                if i < len(prev_areas) and prev_areas[i].get("area_state") != area_state:
                    _LOGGER.warning("✅ MQTT: Area %s state changed to %s", area_name, area_state)
                    areas_updated = True
            
            # Update device areas
            self.device_areas[device_id] = areas
            
            # Dispatch update signal for each area
            for area in areas:
                signal = f"{DOMAIN}_{device_id}_area_{area['area_number']}"
                async_dispatcher_send(self.hass, signal, area)
            
            if areas_updated:
                _LOGGER.warning("✅ MQTT: Updated %d areas for device %s", len(areas), device_id)
        
        # Process zones
        zones_updated = False
        if "zones" in alarm_data and "zonesStamp" in alarm_data:
            zones = []
            for i, zone_state in enumerate(alarm_data["zones"]):
                # Create zone object
                zone = {
                    "zone_number": i + 1,
                    "zone_state": zone_state,
                    "device_id": device_id,
                    "last_changed": alarm_data["zonesStamp"][i] if i < len(alarm_data["zonesStamp"]) else None,
                }
                zones.append(zone)
                
                # Check if state changed
                prev_zones = self.device_zones.get(device_id, [])
                if i < len(prev_zones) and prev_zones[i].get("zone_state") != zone_state:
                    _LOGGER.warning("✅ MQTT: Zone %s state changed to %s", i+1, zone_state)
                    zones_updated = True
            
            # Update device zones
            self.device_zones[device_id] = zones
            
            # Dispatch update signal for each zone
            for zone in zones:
                signal = f"{DOMAIN}_{device_id}_zone_{zone['zone_number']}"
                async_dispatcher_send(self.hass, signal, zone)
            
            if zones_updated:
                _LOGGER.warning("✅ MQTT: Updated %d zones for device %s", len(zones), device_id)
        
        # Also send a general update signal
        async_dispatcher_send(self.hass, f"{DOMAIN}_{device_id}_update", self.device_state[device_id])

    def get_device_state(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get current state for a device."""
        return self.device_state.get(device_id)
    
    def get_device_areas(self, device_id: str) -> List[Dict[str, Any]]:
        """Get areas for a device."""
        return self.device_areas.get(device_id, [])
    
    def get_device_zones(self, device_id: str) -> List[Dict[str, Any]]:
        """Get zones for a device."""
        return self.device_zones.get(device_id, [])