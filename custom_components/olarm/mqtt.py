"""MQTT Client for Olarm integration.

Enhanced MQTT client that provides robust connection handling, automatic reconnection,
and detailed logging to help diagnose connectivity issues.
"""
import asyncio
import json
import logging
import time
import threading
from typing import Dict, List, Optional, Callable, Any, Awaitable

# Set up loggers
_LOGGER = logging.getLogger(__name__)

# Check paho-mqtt installation
try:
    import paho.mqtt.client as mqtt_client
    from paho.mqtt.client import MQTTMessage
    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False
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
from .debug import mqtt_log, log_exception, direct_log

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
        self.connection_lock = threading.Lock()
        self.reconnect_task = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # seconds
        
        # Log initialization
        direct_log(f"Initializing MQTT client for {device_name} (IMEI: {device_imei})")
        mqtt_log(f"Initializing MQTT client for {device_name} (IMEI: {device_imei})")
        _LOGGER.warning("Initializing MQTT client for %s (IMEI: %s)", device_name, device_imei)

    def register_message_callback(self, callback: Callable[[str, str, str], Awaitable[None]]):
        """Register a callback for MQTT messages."""
        self._message_callbacks.append(callback)
        mqtt_log(f"Registered message callback for {self.device_name}")
        _LOGGER.warning("Registered message callback for %s", self.device_name)

    async def connect(self) -> bool:
        """Connect to MQTT broker."""
        # Skip if paho-mqtt is not installed
        if not HAS_PAHO:
            mqtt_log("Cannot connect - paho-mqtt is not installed", "error")
            _LOGGER.error("Cannot connect - paho-mqtt is not installed")
            return False
        
        # Use a lock to prevent multiple simultaneous connection attempts
        with self.connection_lock:
            if self.is_connected:
                mqtt_log(f"Already connected for {self.device_name}")
                return True
                
            try:
                mqtt_log(f"Connecting to broker for {self.device_name}...")
                _LOGGER.warning("Connecting to broker for %s...", self.device_name)
                
                # Create MQTT client
                client_id = f"home-assistant-oauth-{self.device_imei}-{int(time.time())}"
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
                mqtt_log(f"Connecting to {MQTT_HOST}:{MQTT_PORT} with client ID {client_id}")
                _LOGGER.warning("Connecting to %s:%s with client ID %s", 
                                MQTT_HOST, MQTT_PORT, client_id)
                
                # Set a connection timeout
                self.mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
                
                # Start the MQTT client loop in a separate thread
                self.mqtt_client.loop_start()
                
                # Wait for connection to establish
                connection_timeout = 15  # seconds
                for i in range(connection_timeout * 2):  # Check every 0.5 seconds
                    if self.is_connected:
                        mqtt_log(f"✅ Connection established for {self.device_name}!")
                        _LOGGER.warning("✅ Connection established for %s!", self.device_name)
                        return True
                    await asyncio.sleep(0.5)
                    if i % 4 == 0 and i > 0:  # Every 2 seconds
                        mqtt_log(f"⏳ Still trying to connect for {self.device_name}... ({i/2}s)")
                        _LOGGER.warning("⏳ Still trying to connect for %s... (%ds)", 
                                      self.device_name, i/2)
                
                mqtt_log(f"❌ Connection timed out for {self.device_name} after {connection_timeout} seconds", "error")
                _LOGGER.error("❌ Connection timed out for %s after %s seconds", 
                           self.device_name, connection_timeout)
                
                # Cleanup on timeout
                self.mqtt_client.loop_stop()
                self.mqtt_client = None
                return False
            
            except Exception as ex:
                log_exception(ex, f"MQTT connect for {self.device_name}")
                mqtt_log(f"❌ Error connecting to broker for {self.device_name}: {ex}", "error")
                _LOGGER.error("❌ Error connecting to broker for %s: %s", 
                           self.device_name, ex)
                return False

    def on_connect(self, client, userdata, flags, rc):
        """Handle connection established callback."""
        if rc == 0:
            now = time.time()
            self.connection_time = now
            self.reconnect_attempts = 0  # Reset reconnect attempts counter
            
            mqtt_log(f"✅ [CONNECTED] {self.device_name} (IMEI: {self.device_imei})")
            _LOGGER.warning("✅ [CONNECTED] %s (IMEI: %s)",
                         self.device_name, self.device_imei)
                         
            self.is_connected = True
            
            # Subscribe to device topic
            topic = f"so/app/v1/{self.device_imei}"
            self.mqtt_client.subscribe(topic)
            self.subscribed_topics.add(topic)
            
            mqtt_log(f"[SUBSCRIBED] {self.device_name}: {topic}")
            _LOGGER.warning("[SUBSCRIBED] %s: %s", self.device_name, topic)
            
            # Request device status
            mqtt_log(f"[REQUESTING] {self.device_name}: Sending status request")
            _LOGGER.warning("[REQUESTING] %s: Sending status request", self.device_name)
            self.publish_status_request()
        else:
            mqtt_log(f"[FAILED] {self.device_name}: Failed to connect, code: {rc}", "error")
            _LOGGER.error("[FAILED] %s: Failed to connect, code: %s", 
                      self.device_name, rc)
            self.is_connected = False
            
            # Schedule a reconnect
            if self.reconnect_task is None:
                self.schedule_reconnect()

    def on_disconnect(self, client, userdata, rc):
        """Handle disconnection callback."""
        self.is_connected = False
        self.subscribed_topics.clear()
        
        if rc == 0:
            mqtt_log(f"[DISCONNECTED] {self.device_name}: Clean disconnect")
            _LOGGER.warning("[DISCONNECTED] %s: Clean disconnect", self.device_name)
        else:
            mqtt_log(f"⚠️ [DISCONNECTED] {self.device_name}: Unexpected disconnect, code: {rc}", "warning")
            _LOGGER.error("⚠️ [DISCONNECTED] %s: Unexpected disconnect, code: %s", 
                       self.device_name, rc)
            
            # Schedule a reconnect
            if self.reconnect_task is None:
                self.schedule_reconnect()

    def schedule_reconnect(self):
        """Schedule a reconnect attempt after a delay."""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            mqtt_log(f"Max reconnect attempts ({self.max_reconnect_attempts}) reached for {self.device_name}", "warning")
            _LOGGER.warning("Max reconnect attempts (%d) reached for %s", 
                         self.max_reconnect_attempts, self.device_name)
            self.reconnect_task = None
            return
            
        self.reconnect_attempts += 1
        delay = self.reconnect_delay * (2 ** (self.reconnect_attempts - 1))  # Exponential backoff
        mqtt_log(f"Scheduling reconnect for {self.device_name} in {delay} seconds (attempt {self.reconnect_attempts})")
        _LOGGER.warning("Scheduling reconnect for %s in %d seconds (attempt %d)", 
                     self.device_name, delay, self.reconnect_attempts)
        
        asyncio.run_coroutine_threadsafe(self._delayed_reconnect(delay), self.hass.loop)

    async def _delayed_reconnect(self, delay):
        """Reconnect after a delay."""
        try:
            self.reconnect_task = asyncio.current_task()
            await asyncio.sleep(delay)
            
            # Check if we're already connected (might have connected through another means)
            if self.is_connected:
                mqtt_log(f"Already reconnected to {self.device_name}, canceling reconnect task")
                self.reconnect_task = None
                return
                
            mqtt_log(f"Attempting reconnect for {self.device_name} after {delay}s delay")
            await self.connect()
        except Exception as ex:
            log_exception(ex, f"Delayed reconnect for {self.device_name}")
        finally:
            self.reconnect_task = None

    def on_message(self, client, userdata, msg: MQTTMessage):
        """Handle message received callback."""
        topic = msg.topic
        try:
            payload = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            mqtt_log(f"Error decoding message payload on topic {topic}", "error")
            return
        
        now = time.time()
        self.messages_received += 1
        self.last_message_time = now
        
        mqtt_log(f"[MESSAGE] {self.device_name}: #{self.messages_received} on {topic}")
        _LOGGER.warning("[MESSAGE] %s: #%d on %s", 
                     self.device_name, self.messages_received, topic)
        
        if self.debug_mqtt:
            # Try to parse as JSON to display more nicely
            try:
                payload_obj = json.loads(payload)
                mqtt_log(f"[PAYLOAD JSON] {json.dumps(payload_obj, indent=2)[:500]}...")
            except json.JSONDecodeError:
                # Not JSON, show as text
                shortened_payload = payload[:500] + ("..." if len(payload) > 500 else "")
                mqtt_log(f"[PAYLOAD RAW] {shortened_payload}")
        
        # Process message in the event loop
        asyncio.run_coroutine_threadsafe(
            self._process_message(topic, payload), 
            self.hass.loop
        )

    async def _process_message(self, topic: str, payload: str):
        """Process MQTT message."""
        try:
            # Log message type if it's JSON
            try:
                payload_json = json.loads(payload)
                if "type" in payload_json:
                    mqtt_log(f"Message type: {payload_json['type']}")
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
                
            # Call all registered callbacks
            for callback in self._message_callbacks:
                try:
                    await callback(self.device_id, topic, payload)
                except Exception as ex:
                    log_exception(ex, f"Message callback for {self.device_name}")
                    mqtt_log(f"❌ Error in message callback for {self.device_name}: {ex}", "error")
            
            # Dispatch update signal
            async_dispatcher_send(
                self.hass, 
                f"{SIGNAL_OLARM_MQTT_UPDATE}_{self.device_id}", 
                {"topic": topic, "payload": payload}
            )
        except Exception as ex:
            log_exception(ex, f"MQTT process message for {self.device_name}")

    def publish_status_request(self):
        """Request device status."""
        if not self.is_connected or not self.mqtt_client:
            mqtt_log(f"⚠️ Cannot request status - client not connected for {self.device_name}", "warning")
            _LOGGER.warning("⚠️ Cannot request status - client not connected for %s", self.device_name)
            return False
        
        topic = f"si/app/v2/{self.device_imei}/status"
        payload = json.dumps({"method": "GET"})
        
        mqtt_log(f"Publishing status request for {self.device_name}")
        _LOGGER.warning("Publishing status request for %s", self.device_name)
            
        try:
            result = self.mqtt_client.publish(topic, payload, qos=1)
            
            if result.rc != 0:
                mqtt_log(f"❌ Failed to publish status request for {self.device_name}, code: {result.rc}", "error")
                _LOGGER.error("❌ Failed to publish status request for %s, code: %s", 
                          self.device_name, result.rc)
                return False
            
            mqtt_log(f"Status request published successfully for {self.device_name}")
            _LOGGER.warning("Status request published successfully for %s", self.device_name)
            return True
        except Exception as ex:
            log_exception(ex, f"Publish status for {self.device_name}")
            return False

    def publish_action(self, action_cmd: str, area_num: int):
        """Publish an action command to the device."""
        if not self.is_connected or not self.mqtt_client:
            mqtt_log(f"⚠️ Cannot publish action - client not connected for {self.device_name}", "warning")
            _LOGGER.warning("⚠️ Cannot publish action - client not connected for %s", self.device_name)
            return False
        
        topic = f"si/app/v2/{self.device_imei}/control"
        payload = json.dumps({
            "method": "POST",
            "data": [action_cmd, area_num]
        })
        
        mqtt_log(f"Publishing action '{action_cmd}' for area {area_num} to {self.device_name}")
        _LOGGER.warning("Publishing action '%s' for area %s to %s", 
                   action_cmd, area_num, self.device_name)
            
        try:
            result = self.mqtt_client.publish(topic, payload, qos=1)
            
            if result.rc != 0:
                mqtt_log(f"❌ Failed to publish action for {self.device_name}, code: {result.rc}", "error")
                _LOGGER.error("❌ Failed to publish action for %s, code: %s", 
                          self.device_name, result.rc)
                return False
            
            mqtt_log(f"✅ Action '{action_cmd}' for area {area_num} published to {self.device_name}")
            _LOGGER.warning("✅ Action '%s' for area %s published to %s", 
                       action_cmd, area_num, self.device_name)
            return True
        except Exception as ex:
            log_exception(ex, f"Publish action for {self.device_name}")
            return False

    def disconnect(self):
        """Disconnect from MQTT broker."""
        with self.connection_lock:
            if self.mqtt_client:
                try:
                    self.mqtt_client.loop_stop()
                    self.mqtt_client.disconnect()
                except Exception as ex:
                    log_exception(ex, f"MQTT disconnect for {self.device_name}")
                finally:
                    self.is_connected = False
                    self.subscribed_topics.clear()
                    mqtt_log(f"Disconnected from broker for {self.device_name}")
                    _LOGGER.warning("Disconnected from broker for %s", self.device_name)
            
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
            "device_imei": self.device_imei,
            "is_connected": self.is_connected,
            "messages_received": self.messages_received,
            "uptime_seconds": uptime,
            "last_message_seconds_ago": last_msg_age,
            "subscribed_topics": list(self.subscribed_topics),
            "reconnect_attempts": self.reconnect_attempts,
        }
        
        # Log the status
        mqtt_log(f"STATUS [{self.device_name}]: Connected={self.is_connected}, Messages={self.messages_received}")
        _LOGGER.warning("STATUS [%s]: Connected=%s, Messages=%d",
                    self.device_name, self.is_connected, self.messages_received)
        
        return status