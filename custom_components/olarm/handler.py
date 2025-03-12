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
    ZONE_ACTIVE,
    ZONE_CLOSED,
    ZONE_BYPASSED,
)
from .debug import direct_log, mqtt_log, log_exception

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
        self.raw_messages = {}  # Store last raw payload for debugging
        self.raw_message_count = 0

    async def process_mqtt_message(self, device_id: str, topic: str, payload: str) -> None:
        """Process incoming MQTT message."""
        try:
            # Store raw message for debugging
            self.raw_message_count += 1
            msg_id = self.raw_message_count
            self.raw_messages[msg_id] = {
                "device_id": device_id,
                "topic": topic,
                "payload": payload[:500] if len(payload) > 500 else payload,
                "timestamp": self.hass.loop.time()
            }
            
            # Keep only the last 10 messages
            if len(self.raw_messages) > 10:
                oldest_id = min(self.raw_messages.keys())
                self.raw_messages.pop(oldest_id)
            
            # Parse JSON
            data = json.loads(payload)
            
            # Process alarm payload
            if data.get("type") == "alarmPayload":
                mqtt_log(f"📩 MQTT: Received alarm state update for device {device_id}")
                _LOGGER.warning("📩 MQTT: Received alarm state update for device %s", device_id)
                await self._process_alarm_payload(device_id, data)
            else:
                mqtt_log(f"MQTT: Received non-alarm message type: {data.get('type')} for device {device_id}")
                _LOGGER.debug("MQTT: Received non-alarm message type: %s for device %s", 
                             data.get("type"), device_id)
        
        except json.JSONDecodeError as ex:
            mqtt_log(f"❌ MQTT: Failed to parse message as JSON: {payload[:100]}", "error")
            _LOGGER.error("❌ MQTT: Failed to parse message as JSON: %s", payload[:100])
        except Exception as ex:
            log_exception(ex, f"Process MQTT message for {device_id}")
            mqtt_log(f"❌ MQTT: Error processing message: {ex}", "error")
            _LOGGER.error("❌ MQTT: Error processing message: %s", ex)

    async def _process_alarm_payload(self, device_id: str, payload: dict) -> None:
        """Process alarm payload from MQTT."""
        if "data" not in payload:
            mqtt_log("⚠️ MQTT: Alarm payload missing data field", "warning")
            _LOGGER.warning("⚠️ MQTT: Alarm payload missing data field")
            return
        
        alarm_data = payload["data"]
        
        # Store full device state
        self.device_state[device_id] = alarm_data
        
        # Log summary of the received data
        summary = {
            "has_areas": "areas" in alarm_data,
            "has_zones": "zones" in alarm_data,
            "has_power": "power" in alarm_data,
            "areas_count": len(alarm_data.get("areas", [])),
            "zones_count": len(alarm_data.get("zones", [])),
        }
        mqtt_log(f"MQTT data summary for {device_id}: {summary}")
        
        # Process areas
        areas_updated = False
        if "areas" in alarm_data:
            areas = []
            for i, area_state in enumerate(alarm_data["areas"]):
                # Get area name (or use default if not available)
                area_name = "Unknown Area"
                if "areasDetail" in alarm_data and i < len(alarm_data["areasDetail"]) and alarm_data["areasDetail"][i]:
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
                    mqtt_log(f"✅ MQTT: Area {area_name} state changed to {area_state}")
                    _LOGGER.warning("✅ MQTT: Area %s state changed to %s", area_name, area_state)
                    areas_updated = True
            
            # Update device areas
            self.device_areas[device_id] = areas
            
            # Dispatch update signal for each area
            for area in areas:
                signal = f"{DOMAIN}_{device_id}_area_{area['area_number']}"
                async_dispatcher_send(self.hass, signal, area)
                mqtt_log(f"Dispatched {signal} with state {area['area_state']}")
            
            if areas_updated:
                mqtt_log(f"✅ MQTT: Updated {len(areas)} areas for device {device_id}")
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
                    mqtt_log(f"✅ MQTT: Zone {i+1} state changed to {zone_state}")
                    _LOGGER.warning("✅ MQTT: Zone %s state changed to %s", i+1, zone_state)
                    zones_updated = True
            
            # Update device zones
            self.device_zones[device_id] = zones
            
            # Dispatch update signal for each zone
            for zone in zones:
                signal = f"{DOMAIN}_{device_id}_zone_{zone['zone_number']}"
                async_dispatcher_send(self.hass, signal, zone)
                
                # Log if this is an active zone for easier debugging
                if zone["zone_state"] == ZONE_ACTIVE:
                    mqtt_log(f"Active Zone: {zone['zone_number']} for device {device_id}")
            
            if zones_updated:
                mqtt_log(f"✅ MQTT: Updated {len(zones)} zones for device {device_id}")
                _LOGGER.warning("✅ MQTT: Updated %d zones for device %s", len(zones), device_id)
        
        # Also send a general update signal
        async_dispatcher_send(self.hass, f"{DOMAIN}_{device_id}_update", self.device_state[device_id])
        mqtt_log(f"Dispatched general update for device {device_id}")

    def get_device_state(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get current state for a device."""
        return self.device_state.get(device_id)
    
    def get_device_areas(self, device_id: str) -> List[Dict[str, Any]]:
        """Get areas for a device."""
        return self.device_areas.get(device_id, [])
    
    def get_device_zones(self, device_id: str) -> List[Dict[str, Any]]:
        """Get zones for a device."""
        return self.device_zones.get(device_id, [])
    
    def get_raw_messages(self) -> Dict[int, Dict[str, Any]]:
        """Get the last raw messages received."""
        return self.raw_messages