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
            _LOGGER.debug("Connecting to MQTT broker at %s:%s", MQTT_HOST, MQTT_PORT)
            self.mqtt_client.connect(MQTT_HOST, MQTT_PORT)
            
            # Start the MQTT client loop in a separate thread
            self.mqtt_client.loop_start()
            
            # Wait for connection to establish
            for _ in range(10):  # Try for 5 seconds
                if self.is_connected:
                    return True
                await asyncio.sleep(0.5)
            
            _LOGGER.error("Failed to connect to MQTT broker within timeout")
            return False
        
        except Exception as ex:
            _LOGGER.error("Error connecting to MQTT broker: %s", ex)
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
        _LOGGER.debug("Received MQTT message on topic %s: %s", topic, payload)
        
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
                _LOGGER.error("Error in MQTT message callback: %s", ex)
        
        # Dispatch update signal
        async_dispatcher_send(
            self.hass, 
            f"{SIGNAL_OLARM_MQTT_UPDATE}_{self.device_id}", 
            {"topic": topic, "payload": payload}
        )

    def publish_status_request(self):
        """Request device status."""
        if not self.is_connected or not self.mqtt_client:
            _LOGGER.warning("Cannot request status - MQTT client not connected")
            return False
        
        topic = f"si/app/v2/{self.device_imei}/status"
        payload = json.dumps({"method": "GET"})
        
        _LOGGER.debug("Publishing status request to topic %s: %s", topic, payload)
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            _LOGGER.error("Failed to publish status request, return code: %s", result.rc)
            return False
        
        _LOGGER.info("Published status request to topic: %s", topic)
        return True

    def publish_action(self, action_cmd: str, area_num: int):
        """Publish an action command to the device."""
        if not self.is_connected or not self.mqtt_client:
            _LOGGER.warning("Cannot publish action - MQTT client not connected")
            return False
        
        topic = f"si/app/v2/{self.device_imei}/control"
        payload = json.dumps({
            "method": "POST",
            "data": [action_cmd, area_num]
        })
        
        _LOGGER.debug("Publishing action to topic %s: %s", topic, payload)
        result = self.mqtt_client.publish(topic, payload, qos=1)
        
        if result.rc != 0:
            _LOGGER.error("Failed to publish action, return code: %s", result.rc)
            return False
        
        _LOGGER.info("Published action to topic: %s", topic)
        return True

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.is_connected = False
            self.subscribed_topics.clear()
            _LOGGER.info("Disconnected from MQTT broker")
