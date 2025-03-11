"""MQTT Client for Olarm integration."""
import asyncio
import json
import logging
import sys
print("\n\n========== OLARM MQTT MODULE LOADED ==========\n\n", flush=True)
from typing import Dict, List, Optional, Callable, Any, Awaitable

# Extremely direct diagnostic logger that will bypass all filtering
def direct_log(message):
    with open("/config/olarm_mqtt.log", "a") as f:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"{timestamp} - {message}\n")
    print(f"\n⚠️ OLARM MQTT: {message}\n", flush=True)

# Add diagnostic info
direct_log("MQTT module initialized")
try:
    import paho.mqtt.client
    direct_log("✅ PAHO-MQTT IMPORTED SUCCESSFULLY")
except ImportError as e:
    direct_log(f"❌ PAHO-MQTT IMPORT ERROR: {e}")

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

# Direct console logger for MQTT diagnostics
def mqtt_log(message: str):
    """Log MQTT messages directly to console for diagnostics."""
    print(f"\n===== OLARM MQTT: {message} =====\n", flush=True)
    _LOGGER.warning(f"🔵 DIRECT MQTT LOG: {message}")

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
        
        # Log initialization
        mqtt_log(f"Initializing MQTT client for {device_name} (IMEI: {device_imei})")

    def register_message_callback(self, callback: Callable[[str, str, str], Awaitable[None]]):
        """Register a callback for MQTT messages."""
        self._message_callbacks.append(callback)
        mqtt_log(f"Registered message callback for {self.device_name}")

    async def connect(self) -> bool:
        """Connect to MQTT broker."""

        direct_log(f"Attempting to connect to MQTT for device {self.device_name} (IMEI: {self.device_imei})")

        # Skip if paho-mqtt is not installed
        if mqtt_client is None:
            mqtt_log(f"❌ Cannot connect - paho-mqtt is not installed")
            return False
            
        try:
            mqtt_log(f"🔄 Connecting to broker for {self.device_name}...")
            
            # Create MQTT client
            client_id = f"home-assistant-oauth-{self.device_imei}"
            self.mqtt_client = mqtt_client.Client(client_id=client_id, transport="websockets")
            self.mqtt_client.ws_set_options(path="/mqtt")  # WebSocket path
            self.mqtt_client.username_pw_set(MQTT_USERNAME, self.access_token)
            
            direct_log(f"MQTT client created with ID: {client_id}, username: {MQTT_USERNAME}")

            # Set callbacks
            self.mqtt_client.on_connect = self.on_connect
            self.mqtt_client.on_disconnect = self.on_disconnect
            self.mqtt_client.on_message = self.on_message
            
            # Setup TLS for secure connection
            self.mqtt_client.tls_set()
            
            direct_log(f"Connecting to {MQTT_HOST}:{MQTT_PORT} via WebSockets")

            # Connect
            mqtt_log(f"🔄 Connecting to {MQTT_HOST}:{MQTT_PORT} with client ID {client_id}")
            self.mqtt_client.connect(MQTT_HOST, MQTT_PORT)
            
            # Start the MQTT client loop in a separate thread
            self.mqtt_client.loop_start()
            
            # Wait for connection to establish
            connection_timeout = 15  # seconds
            for i in range(connection_timeout * 2):  # Check every 0.5 seconds
                if self.is_connected:
                    mqtt_log(f"✅ Connection established for {self.device_name}!")
                    return True
                await asyncio.sleep(0.5)
                if i % 4 == 0 and i > 0:  # Every 2 seconds
                    mqtt_log(f"⏳ Still trying to connect for {self.device_name}... ({i/2}s)")
            
            mqtt_log(f"❌ Connection timed out for {self.device_name}")
            return False
        
        except Exception as ex:
            mqtt_log(f"❌ Error connecting to broker for {self.device_name}: {ex}")
            return False

    def on_connect(self, client, userdata, flags, rc):
        """Handle connection established callback."""

        direct_log(f"on_connect called with result code {rc} for {self.device_name}")
        
        if rc == 0:
            import time
            self.connection_time = time.time()
            mqtt_log(f"✅ [CONNECTED] {self.device_name} (IMEI: {self.device_imei})")
            self.is_connected = True
            
            # Subscribe to device topic
            topic = f"so/app/v1/{self.device_imei}"
            self.mqtt_client.subscribe(topic)
            self.subscribed_topics.add(topic)
            mqtt_log(f"📥 [SUBSCRIBED] {self.device_name}: {topic}")
            
            # Request device status
            mqtt_log(f"📤 [REQUESTING] {self.device_name}: Sending status request")
            self.publish_status_request()
        else:
            mqtt_log(f"❌ [FAILED] {self.device_name}: Failed to connect, code: {rc}")
            self.is_connected = False

    def on_disconnect(self, client, userdata, rc):
        """Handle disconnection callback."""
        if rc == 0:
            mqtt_log(f"[DISCONNECTED] {self.device_name}: Clean disconnect")
        else:
            mqtt_log(f"⚠️ [DISCONNECTED] {self.device_name}: Unexpected disconnect, code: {rc}")
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
            shortened_payload = payload[:100] + ("..." if len(payload) > 100 else "")
            mqtt_log(f"🔍 [PAYLOAD] {shortened_payload}")
        
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
                mqtt_log(f"❌ Error in message callback for {self.device_name}: {ex}")
        
        # Dispatch update signal
        async_dispatcher_send(
            self.hass, 
            f"{SIGNAL_OLARM_MQTT_UPDATE}_{self.device_id}", 
            {"topic": topic, "payload": payload}
        )

    def publish_status_request(self):
        """Request device status."""
        if not self.is_connected or not self.mqtt_client:
            mqtt_log(f"⚠️ Cannot request status - client not connected for {self.device_name}")
            return False
        
        topic = f"si/app/v2/{self.device_imei}/status"
        payload = json.dumps({"method": "GET"})
        
        mqtt_log(f"📤 Publishing status request for {self.device_name}")
            
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            mqtt_log(f"❌ Failed to publish status request for {self.device_name}, code: {result.rc}")
            return False
        
        mqtt_log(f"📤 Status request published successfully for {self.device_name}")
        return True

    def publish_action(self, action_cmd: str, area_num: int):
        """Publish an action command to the device."""
        if not self.is_connected or not self.mqtt_client:
            mqtt_log(f"⚠️ Cannot publish action - client not connected for {self.device_name}")
            return False
        
        topic = f"si/app/v2/{self.device_imei}/control"
        payload = json.dumps({
            "method": "POST",
            "data": [action_cmd, area_num]
        })
        
        mqtt_log(f"📤 Publishing action '{action_cmd}' for area {area_num} to {self.device_name}")
            
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            mqtt_log(f"❌ Failed to publish action for {self.device_name}, code: {result.rc}")
            return False
        
        mqtt_log(f"✅ Action '{action_cmd}' for area {area_num} published to {self.device_name}")
        return True

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.is_connected = False
            self.subscribed_topics.clear()
            mqtt_log(f"Disconnected from broker for {self.device_name}")
            
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
        mqtt_log(f"STATUS [{self.device_name}]: Connected={self.is_connected}, " +
                f"Messages={self.messages_received}")
        
        return status