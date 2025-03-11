"""MQTT Client for Olarm integration."""
import asyncio
import json
import logging
from typing import Dict, List, Optional, Callable, Any, Awaitable

import paho.mqtt.client as mqtt_client
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
)

_LOGGER = logging.getLogger(__name__)

class OlarmMqttClient:
    """MQTT client for Olarm devices."""

    def __init__(self, hass: HomeAssistant, device_imei: str, access_token: str, device_id: str):
        """Initialize the MQTT client."""
        self.hass = hass
        self.device_imei = device_imei
        self.device_id = device_id
        self.access_token = access_token
        self.mqtt_client = None
        self.is_connected = False
        self.subscribed_topics = set()
        self._message_callbacks = []

    def register_message_callback(self, callback: Callable[[str, str, str], Awaitable[None]]):
        """Register a callback for MQTT messages."""
        self._message_callbacks.append(callback)

    async def connect(self) -> bool:
        """Connect to MQTT broker."""
        try:
            _LOGGER.info("🔄 MQTT: Connecting to broker for device %s (IMEI: %s)...", 
                       self.device_id, self.device_imei)
            
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
            _LOGGER.debug("MQTT: Connecting to broker at %s:%s", MQTT_HOST, MQTT_PORT)
            self.mqtt_client.connect(MQTT_HOST, MQTT_PORT)
            
            # Start the MQTT client loop in a separate thread
            self.mqtt_client.loop_start()
            
            # Wait for connection to establish
            connection_timeout = 10  # seconds
            for i in range(connection_timeout * 2):  # Check every 0.5 seconds
                if self.is_connected:
                    _LOGGER.info("✅ MQTT: Connection established for device %s", self.device_id)
                    return True
                await asyncio.sleep(0.5)
                if i == 5:  # After 2.5 seconds
                    _LOGGER.info("⏳ MQTT: Still waiting for connection...")
            
            _LOGGER.error("❌ MQTT: Connection timed out after %s seconds for device %s", 
                         connection_timeout, self.device_id)
            return False
        
        except Exception as ex:
            _LOGGER.error("❌ MQTT: Error connecting to broker for device %s: %s", 
                         self.device_id, ex)
            return False

    def on_connect(self, client, userdata, flags, rc):
        """Handle connection established callback."""
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker")
            self.is_connected = True
            
            # Subscribe to device topic
            topic = f"so/app/v1/{self.device_imei}"
            self.mqtt_client.subscribe(topic)
            self.subscribed_topics.add(topic)
            _LOGGER.info("Subscribed to topic: %s", topic)
            
            # Request device status
            self.publish_status_request()
        else:
            _LOGGER.error("Failed to connect to MQTT broker, return code: %s", rc)
            self.is_connected = False

    def on_disconnect(self, client, userdata, rc):
        """Handle disconnection callback."""
        _LOGGER.warning("Disconnected from MQTT broker with code: %s", rc)
        self.is_connected = False
        self.subscribed_topics.clear()

    def on_message(self, client, userdata, msg: MQTTMessage):
        """Handle message received callback."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")
        _LOGGER.debug("📩 MQTT: Received message on topic %s", topic)
        
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
                _LOGGER.error("❌ MQTT: Error in message callback: %s", ex)
        
        # Dispatch update signal
        async_dispatcher_send(
            self.hass, 
            f"{SIGNAL_OLARM_MQTT_UPDATE}_{self.device_id}", 
            {"topic": topic, "payload": payload}
        )

    def publish_status_request(self):
        """Request device status."""
        if not self.is_connected or not self.mqtt_client:
            _LOGGER.warning("⚠️ MQTT: Cannot request status - client not connected for device %s", self.device_id)
            return False
        
        topic = f"si/app/v2/{self.device_imei}/status"
        payload = json.dumps({"method": "GET"})
        
        _LOGGER.debug("MQTT: Publishing status request to topic %s", topic)
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            _LOGGER.error("❌ MQTT: Failed to publish status request, return code: %s", result.rc)
            return False
        
        _LOGGER.info("MQTT: Published status request for device %s", self.device_id)
        return True

    def publish_action(self, action_cmd: str, area_num: int):
        """Publish an action command to the device."""
        if not self.is_connected or not self.mqtt_client:
            _LOGGER.warning("⚠️ MQTT: Cannot publish action - client not connected for device %s", self.device_id)
            return False
        
        topic = f"si/app/v2/{self.device_imei}/control"
        payload = json.dumps({
            "method": "POST",
            "data": [action_cmd, area_num]
        })
        
        _LOGGER.debug("MQTT: Publishing action '%s' for area %s to device %s", 
                     action_cmd, area_num, self.device_id)
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            _LOGGER.error("❌ MQTT: Failed to publish action, return code: %s", result.rc)
            return False
        
        _LOGGER.info("✅ MQTT: Published action '%s' for area %s to device %s", 
                    action_cmd, area_num, self.device_id)
        return True

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.is_connected = False
            self.subscribed_topics.clear()
            _LOGGER.info("Disconnected from MQTT broker")