"""MQTT Client for Olarm integration."""
import asyncio
import json
import logging
from typing import Dict, List, Optional, Callable, Any, Awaitable
from .debug import mqtt_log

try:
    import paho.mqtt.client as mqtt_client
    _LOGGER.warning("✅ paho-mqtt is installed correctly")
except ImportError:
    _LOGGER.error("❌ paho-mqtt is NOT installed! MQTT won't work!")

from paho.mqtt.client import MQTTMessage

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

_LOGGER = logging.getLogger(__name__)

class OlarmMqttClient:
    """MQTT client for Olarm devices."""

    def __init__(
        self, 
        hass: HomeAssistant, 
        device_imei: str, 
        access_token: str, 
        device_id: str,
        device_name: str = "Unknown",
        debug_mqtt: bool = False
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

    def register_message_callback(self, callback: Callable[[str, str, str], Awaitable[None]]):
        """Register a callback for MQTT messages."""
        self._message_callbacks.append(callback)

    async def connect(self) -> bool:
        """Connect to MQTT broker."""
        try:
            _LOGGER.warning("🔄 MQTT [%s]: Connecting to broker...", self.device_name)
            
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
            if self.debug_mqtt:
                _LOGGER.warning("🔍 MQTT [%s]: Debug - Connecting to %s:%s with client ID %s", 
                               self.device_name, MQTT_HOST, MQTT_PORT, client_id)
            self.mqtt_client.connect(MQTT_HOST, MQTT_PORT)
            
            # Start the MQTT client loop in a separate thread
            self.mqtt_client.loop_start()
            
            # Wait for connection to establish
            connection_timeout = 15  # seconds
            for i in range(connection_timeout * 2):  # Check every 0.5 seconds
                if self.is_connected:
                    _LOGGER.warning("✅ MQTT [%s]: Connection established!", self.device_name)
                    return True
                await asyncio.sleep(0.5)
                if i % 4 == 0 and i > 0:  # Every 2 seconds
                    _LOGGER.warning("⏳ MQTT [%s]: Still trying to connect... (%ds)", 
                                  self.device_name, i/2)
            
            _LOGGER.error("❌ MQTT [%s]: Connection timed out after %s seconds", 
                         self.device_name, connection_timeout)
            return False
        
        except Exception as ex:
            _LOGGER.error("❌ MQTT [%s]: Error connecting to broker: %s", 
                         self.device_name, ex)
            return False

    def on_connect(self, client, userdata, flags, rc):
        """Handle connection established callback."""
        if rc == 0:
            import time
            self.connection_time = time.time()
            mqtt_log(f"✅ [CONNECTED] {self.device_name} (IMEI: {self.device_imei})")
            _LOGGER.warning("✅ MQTT [%s]: Successfully connected to broker (IMEI: %s)",
                self.device_name, self.device_imei
            )
            self.is_connected = True
            
            # Subscribe to device topic
            topic = f"so/app/v1/{self.device_imei}"
            self.mqtt_client.subscribe(topic)
            self.subscribed_topics.add(topic)
            mqtt_log(f"📥 [SUBSCRIBED] {self.device_name}: {topic}")
            _LOGGER.warning("📥 MQTT [%s]: Subscribed to topic: %s", self.device_name, topic)
            
            # Request device status
            mqtt_log(f"📤 [REQUESTING] {self.device_name}: Sending status request")
            self.publish_status_request()
        else:
            mqtt_log(f"❌ [FAILED] {self.device_name}: Failed to connect, code: {rc}")
            _LOGGER.error(
                "❌ MQTT [%s]: Failed to connect to broker, return code: %s",
                self.device_name, rc
            )
            self.is_connected = False

    def on_disconnect(self, client, userdata, rc):
        """Handle disconnection callback."""
        if rc == 0:
            _LOGGER.warning("MQTT [%s]: Cleanly disconnected from broker", self.device_name)
        else:
            _LOGGER.error(
                "⚠️ MQTT [%s]: Unexpectedly disconnected from broker with code: %s", 
                self.device_name, rc
            )
        self.is_connected = False
        self.subscribed_topics.clear()

    def on_message(self, client, userdata, msg: MQTTMessage):
        """Handle message received callback."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")
        
        import time
        self.messages_received += 1
        self.last_message_time = time.time()
        
        mqtt_log(f"📩 [MESSAGE] {self.device_name}: #{self.messages_received} on {topic}")
        
        if self.debug_mqtt:
            mqtt_log(f"🔍 [PAYLOAD] {payload[:100]}{'...' if len(payload) > 100 else ''}")
            _LOGGER.warning("📩 MQTT [%s]: Received message #%d on topic %s: %s", 
                        self.device_name, self.messages_received, topic, 
                        payload[:100] + "..." if len(payload) > 100 else payload)
        else:
            _LOGGER.warning("📩 MQTT [%s]: Received message #%d on topic %s", 
                        self.device_name, self.messages_received, topic)
            
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
                _LOGGER.error("❌ MQTT [%s]: Error in message callback: %s", self.device_name, ex)
        
        # Dispatch update signal
        async_dispatcher_send(
            self.hass, 
            f"{SIGNAL_OLARM_MQTT_UPDATE}_{self.device_id}", 
            {"topic": topic, "payload": payload}
        )

    def publish_status_request(self):
        """Request device status."""
        if not self.is_connected or not self.mqtt_client:
            _LOGGER.warning("⚠️ MQTT [%s]: Cannot request status - client not connected", self.device_name)
            return False
        
        topic = f"si/app/v2/{self.device_imei}/status"
        payload = json.dumps({"method": "GET"})
        
        if self.debug_mqtt:
            _LOGGER.warning("📤 MQTT [%s]: Publishing status request to topic %s: %s", 
                          self.device_name, topic, payload)
        else:
            _LOGGER.warning("📤 MQTT [%s]: Publishing status request to topic %s", 
                          self.device_name, topic)
            
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            _LOGGER.error("❌ MQTT [%s]: Failed to publish status request, return code: %s", 
                         self.device_name, result.rc)
            return False
        
        _LOGGER.warning("📤 MQTT [%s]: Status request published successfully", self.device_name)
        return True

    def publish_action(self, action_cmd: str, area_num: int):
        """Publish an action command to the device."""
        if not self.is_connected or not self.mqtt_client:
            _LOGGER.warning("⚠️ MQTT [%s]: Cannot publish action - client not connected", self.device_name)
            return False
        
        topic = f"si/app/v2/{self.device_imei}/control"
        payload = json.dumps({
            "method": "POST",
            "data": [action_cmd, area_num]
        })
        
        if self.debug_mqtt:
            _LOGGER.warning("📤 MQTT [%s]: Publishing action '%s' for area %s: %s", 
                          self.device_name, action_cmd, area_num, payload)
        else:
            _LOGGER.warning("📤 MQTT [%s]: Publishing action '%s' for area %s", 
                          self.device_name, action_cmd, area_num)
            
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            _LOGGER.error("❌ MQTT [%s]: Failed to publish action, return code: %s", 
                         self.device_name, result.rc)
            return False
        
        _LOGGER.warning("✅ MQTT [%s]: Action '%s' for area %s published successfully", 
                      self.device_name, action_cmd, area_num)
        return True

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.is_connected = False
            self.subscribed_topics.clear()
            _LOGGER.warning("MQTT [%s]: Disconnected from broker", self.device_name)
            
    def get_status(self) -> Dict[str, Any]:
        """Get the status of this MQTT client."""
        import time
        current_time = time.time()
        
        uptime = None
        if self.connection_time:
            uptime = int(current_time - self.connection_time)
            
        last_msg_age = None
        if self.last_message_time:
            last_msg_age = int(current_time - self.last_message_time)
            
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "is_connected": self.is_connected,
            "messages_received": self.messages_received,
            "uptime_seconds": uptime,
            "last_message_seconds_ago": last_msg_age,
            "subscribed_topics": list(self.subscribed_topics)
        }