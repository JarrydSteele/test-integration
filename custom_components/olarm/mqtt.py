"""MQTT Client for Olarm integration."""
import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Callable, Any, Awaitable

# Set up loggers
_LOGGER = logging.getLogger(__name__)

# Check paho-mqtt installation
try:
    import paho.mqtt.client as mqtt_client
    from paho.mqtt.client import MQTTMessage
    _LOGGER.warning("✅ paho-mqtt is installed correctly")
except ImportError:
    _LOGGER.error("❌ paho-mqtt is NOT installed! MQTT won't work!")
    # Create dummy classes to prevent errors
    class MQTTMessage:
        pass
    mqtt_client = None

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    MQTT_HOST,
    MQTT_PORT,
    MQTT_USERNAME,
    MQTT_PROTOCOL,
    SIGNAL_OLARM_MQTT_UPDATE,
    CONF_DEBUG_MQTT,
)

class OlarmMqttClient:
    """MQTT client for Olarm devices."""

    def __init__(
        self, 
        hass: HomeAssistant, 
        device_imei: str, 
        access_token: str, 
        device_id: str,
        device_name: str = "Unknown",
        debug_mqtt: bool = False,
        mqtt_logger = None
    ):
        """Initialize the MQTT client."""
        self.hass = hass
        self.device_imei = device_imei
        self.device_id = device_id
        self.device_name = device_name
        self.access_token = access_token
        self.mqtt_client = None
        self.is_connected = False
        self.subscribed_topics = set()
        self._message_callbacks = []
        self.debug_mqtt = debug_mqtt
        self.connection_time = None
        self.messages_received = 0
        self.last_message_time = None
        self.mqtt_logger = mqtt_logger or _LOGGER
        
        # Log initialization
        self.mqtt_logger.warning("Initializing MQTT client for %s (IMEI: %s)", device_name, device_imei)

    def register_message_callback(self, callback: Callable[[str, str, str], Awaitable[None]]):
        """Register a callback for MQTT messages."""
        self._message_callbacks.append(callback)
        self.mqtt_logger.warning("Registered message callback for %s", self.device_name)

    async def connect(self) -> bool:
        """Connect to MQTT broker."""
        # Skip if paho-mqtt is not installed
        if mqtt_client is None:
            self.mqtt_logger.error("Cannot connect - paho-mqtt is not installed")
            return False
            
        try:
            self.mqtt_logger.warning("Connecting to broker for %s...", self.device_name)
            
            # Create MQTT client
            client_id = f"home-assistant-oauth-{self.device_imei}"
            self.mqtt_client = mqtt_client.Client(client_id=client_id, transport="websockets")
            self.mqtt_client.ws_set_options(path="/mqtt")  # WebSocket path
            self.mqtt_client.username_pw_set(MQTT_USERNAME, self.access_token)
            
            # Set callbacks
            self.mqtt_client.on_connect = self.on_connect
            self.mqtt_client.on_disconnect = self.on_disconnect
            self.mqtt_client.on_message = self.on_message
            
            # Setup TLS for secure connection
            self.mqtt_client.tls_set()
            
            # Connect
            self.mqtt_logger.warning("Connecting to %s:%s with client ID %s", 
                                    MQTT_HOST, MQTT_PORT, client_id)
            self.mqtt_client.connect(MQTT_HOST, MQTT_PORT)
            
            # Start the MQTT client loop in a separate thread
            self.mqtt_client.loop_start()
            
            # Wait for connection to establish
            connection_timeout = 15  # seconds
            for i in range(connection_timeout * 2):  # Check every 0.5 seconds
                if self.is_connected:
                    self.mqtt_logger.warning("✅ Connection established for %s!", self.device_name)
                    return True
                await asyncio.sleep(0.5)
                if i % 4 == 0 and i > 0:  # Every 2 seconds
                    self.mqtt_logger.warning("⏳ Still trying to connect for %s... (%ds)", 
                                          self.device_name, i/2)
            
            self.mqtt_logger.error("❌ Connection timed out for %s after %s seconds", 
                               self.device_name, connection_timeout)
            return False
        
        except Exception as ex:
            self.mqtt_logger.error("❌ Error connecting to broker for %s: %s", 
                               self.device_name, ex)
            return False

    def on_connect(self, client, userdata, flags, rc):
        """Handle connection established callback."""
        if rc == 0:
            now = time.time()
            self.connection_time = now
            self.mqtt_logger.warning(
                "✅ [CONNECTED] %s (IMEI: %s)",
                self.device_name, self.device_imei
            )
            self.is_connected = True
            
            # Subscribe to device topic
            topic = f"so/app/v1/{self.device_imei}"
            self.mqtt_client.subscribe(topic)
            self.subscribed_topics.add(topic)
            self.mqtt_logger.warning("[SUBSCRIBED] %s: %s", self.device_name, topic)
            
            # Request device status
            self.mqtt_logger.warning("[REQUESTING] %s: Sending status request", self.device_name)
            self.publish_status_request()
        else:
            self.mqtt_logger.error("[FAILED] %s: Failed to connect, code: %s", 
                              self.device_name, rc)
            self.is_connected = False

    def on_disconnect(self, client, userdata, rc):
        """Handle disconnection callback."""
        if rc == 0:
            self.mqtt_logger.warning("[DISCONNECTED] %s: Clean disconnect", self.device_name)
        else:
            self.mqtt_logger.error("⚠️ [DISCONNECTED] %s: Unexpected disconnect, code: %s", 
                               self.device_name, rc)
        self.is_connected = False
        self.subscribed_topics.clear()

    def on_message(self, client, userdata, msg: MQTTMessage):
        """Handle message received callback."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")
        
        now = time.time()
        self.messages_received += 1
        self.last_message_time = now
        
        self.mqtt_logger.warning("[MESSAGE] %s: #%d on %s", 
                             self.device_name, self.messages_received, topic)
        
        if self.debug_mqtt:
            shortened_payload = payload[:100] + ("..." if len(payload) > 100 else "")
            self.mqtt_logger.warning("[PAYLOAD] %s", shortened_payload)
        
        # Process message in the event loop
        asyncio.run_coroutine_threadsafe(
            self._process_message(topic, payload), 
            self.hass.loop
        )

    async def _process_message(self, topic: str, payload: str):
        """Process MQTT message."""
        # Call all registered callbacks
        for callback in self._message_callbacks:
            try:
                await callback(self.device_id, topic, payload)
            except Exception as ex:
                self.mqtt_logger.error("❌ Error in message callback for %s: %s", self.device_name, ex)
        
        # Dispatch update signal
        async_dispatcher_send(
            self.hass, 
            f"{SIGNAL_OLARM_MQTT_UPDATE}_{self.device_id}", 
            {"topic": topic, "payload": payload}
        )

    def publish_status_request(self):
        """Request device status."""
        if not self.is_connected or not self.mqtt_client:
            self.mqtt_logger.warning("⚠️ Cannot request status - client not connected for %s", self.device_name)
            return False
        
        topic = f"si/app/v2/{self.device_imei}/status"
        payload = json.dumps({"method": "GET"})
        
        self.mqtt_logger.warning("Publishing status request for %s", self.device_name)
            
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            self.mqtt_logger.error("❌ Failed to publish status request for %s, code: %s", 
                              self.device_name, result.rc)
            return False
        
        self.mqtt_logger.warning("Status request published successfully for %s", self.device_name)
        return True

    def publish_action(self, action_cmd: str, area_num: int):
        """Publish an action command to the device."""
        if not self.is_connected or not self.mqtt_client:
            self.mqtt_logger.warning("⚠️ Cannot publish action - client not connected for %s", self.device_name)
            return False
        
        topic = f"si/app/v2/{self.device_imei}/control"
        payload = json.dumps({
            "method": "POST",
            "data": [action_cmd, area_num]
        })
        
        self.mqtt_logger.warning("Publishing action '%s' for area %s to %s", 
                           action_cmd, area_num, self.device_name)
            
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            self.mqtt_logger.error("❌ Failed to publish action for %s, code: %s", 
                              self.device_name, result.rc)
            return False
        
        self.mqtt_logger.warning("✅ Action '%s' for area %s published to %s", 
                           action_cmd, area_num, self.device_name)
        return True

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.is_connected = False
            self.subscribed_topics.clear()
            self.mqtt_logger.warning("Disconnected from broker for %s", self.device_name)
            
    def get_status(self) -> Dict[str, Any]:
        """Get the status of this MQTT client."""
        now = time.time()
        
        uptime = None
        if self.connection_time:
            uptime = int(now - self.connection_time)
            
        last_msg_age = None
        if self.last_message_time:
            last_msg_age = int(now - self.last_message_time)
            
        status = {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "is_connected": self.is_connected,
            "messages_received": self.messages_received,
            "uptime_seconds": uptime,
            "last_message_seconds_ago": last_msg_age,
            "subscribed_topics": list(self.subscribed_topics)
        }
        
        # Log the status
        self.mqtt_logger.warning("STATUS [%s]: Connected=%s, Messages=%d",
                            self.device_name, self.is_connected, self.messages_received)
        
        return status