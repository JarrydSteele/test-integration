"""The Olarm integration."""
import asyncio
import logging
from datetime import timedelta
from .debug import mqtt_log

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .api import OlarmApiClient, OlarmApiError
from .auth import OlarmAuth
from .mqtt import OlarmMqttClient
from .handler import OlarmMessageHandler
from .const import (
    CONF_API_KEY,
    CONF_USER_EMAIL_PHONE,
    CONF_USER_PASS,
    CONF_MQTT_ONLY,
    CONF_DEBUG_MQTT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SIGNAL_OLARM_MQTT_UPDATE,
    SERVICE_CHECK_MQTT_STATUS,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Olarm component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Olarm from a config entry."""
    session = async_get_clientsession(hass)
    
    # Set up data structures
    hass.data[DOMAIN].setdefault(entry.entry_id, {})
    entry_data = hass.data[DOMAIN][entry.entry_id]
    mqtt_only = entry.data.get(CONF_MQTT_ONLY, False)
    debug_mqtt = entry.data.get(CONF_DEBUG_MQTT, False)
    
    # Initialize either with API key or email/password auth
    if CONF_API_KEY in entry.data:
        # Legacy API key method
        _LOGGER.debug("Setting up with API key authentication")
        client = OlarmApiClient(entry.data[CONF_API_KEY], session)
        entry_data["client"] = client
        
        # Set up coordinator
        coordinator = OlarmDataUpdateCoordinator(hass, client)
        await coordinator.async_config_entry_first_refresh()
        entry_data["coordinator"] = coordinator
        
        # We don't have MQTT with API key method
        entry_data["mqtt_enabled"] = False
        
    elif CONF_USER_EMAIL_PHONE in entry.data and CONF_USER_PASS in entry.data:
        # New email/password auth with MQTT support
        _LOGGER.warning("Setting up with email/password authentication")
        
        if mqtt_only:
            _LOGGER.warning("⚠️ MQTT-ONLY MODE ENABLED: API fallback will be disabled")
        
        # Initialize auth
        auth = OlarmAuth(
            hass, 
            entry.data[CONF_USER_EMAIL_PHONE], 
            entry.data[CONF_USER_PASS], 
            session
        )
        if not await auth.initialize():
            raise Exception("Failed to initialize Olarm authentication")
        
        entry_data["auth"] = auth
        
        # Get devices from auth
        devices = auth.get_devices()
        if not devices:
            _LOGGER.warning("No devices found for user")
            
        # Create API client from access token
        tokens = auth.get_tokens()
        if tokens["access_token"]:
            client = OlarmApiClient(tokens["access_token"], session)
            entry_data["client"] = client
            
            # Set up coordinator
            coordinator = OlarmDataUpdateCoordinator(hass, client, auth)
            await coordinator.async_config_entry_first_refresh()
            entry_data["coordinator"] = coordinator
        
        # Set up message handler
        message_handler = OlarmMessageHandler(hass, entry.entry_id)
        entry_data["message_handler"] = message_handler
        
        # Set up MQTT clients for each device
        mqtt_clients = {}
        _LOGGER.warning("🔄 Setting up MQTT connections for %d device(s)...", len(devices))
        
        for device in devices:
            device_id = device["id"]
            imei = device["imei"]
            device_name = device.get("name", "Unknown Device")
            
            _LOGGER.warning("🔄 Setting up MQTT for device: %s (ID: %s)", device_name, device_id)
            
            # Create MQTT client
            mqtt_client = OlarmMqttClient(
                hass, 
                imei, 
                tokens["access_token"],
                device_id,
                device_name,
                debug_mqtt
            )
            
            # Register message callback
            mqtt_client.register_message_callback(message_handler.process_mqtt_message)
            
            # Connect to MQTT
            connected = await mqtt_client.connect()
            if connected:
                _LOGGER.warning("✅ MQTT successfully connected for device: %s", device_name)
                mqtt_clients[device_id] = mqtt_client
            else:
                _LOGGER.error("❌ Failed to connect to MQTT for device: %s", device_name)
                
                if mqtt_only:
                    _LOGGER.error("⚠️ MQTT-ONLY MODE: This device will be unavailable")
                else:
                    _LOGGER.warning("⚠️ Falling back to API polling for device: %s", device_name)
        
        entry_data["mqtt_clients"] = mqtt_clients
        
        if mqtt_clients:
            mqtt_count = len(mqtt_clients)
            total_count = len(devices)
            _LOGGER.warning(
                "✅ MQTT setup complete: %d/%d devices connected (%s%%)",
                mqtt_count, total_count, int(mqtt_count/total_count*100) if total_count > 0 else 0
            )
            entry_data["mqtt_enabled"] = True
        else:
            if mqtt_only:
                _LOGGER.error("⚠️ MQTT-ONLY MODE: No MQTT connections were established, integration may not function!")
            else:
                _LOGGER.warning("⚠️ No MQTT connections established, using API polling only")
            entry_data["mqtt_enabled"] = False

        if mqtt_clients:
            # Set up a periodic task to check MQTT status
            async def check_mqtt_periodically(now=None):
                """Check MQTT status periodically and log results."""
                mqtt_log("Performing periodic MQTT check")
                for device_id, client in mqtt_clients.items():
                    status = client.get_status()
                    connected = "🟢 CONNECTED" if status["is_connected"] else "🔴 DISCONNECTED"
                    mqtt_log(f"[{status['device_name']}]: {connected}, Messages: {status['messages_received']}")
                    
                    # If connected, request a status update
                    if status["is_connected"]:
                        client.publish_status_request()
                        mqtt_log(f"[{status['device_name']}]: Requesting update via MQTT")
            
        # Register a periodic check every 5 minutes
        mqtt_log("Setting up periodic MQTT checks every 5 minutes")
        track_time_interval(hass, check_mqtt_periodically, timedelta(minutes=5))
        
        # Also run once at startup
        hass.async_create_task(check_mqtt_periodically())
    
    else:
        # This shouldn't happen
        _LOGGER.error("Neither API key nor email/password provided")
        return False
    
    # Register the MQTT status service
    async def async_check_mqtt_status(call):
        """Service to check MQTT status."""
        if "mqtt_clients" not in entry_data:
            _LOGGER.warning("No MQTT clients available - email/password authentication required for MQTT support")
            return
            
        mqtt_clients = entry_data["mqtt_clients"]
        if not mqtt_clients:
            _LOGGER.warning("No active MQTT clients found")
            return
            
        for device_id, client in mqtt_clients.items():
            status = client.get_status()
            _LOGGER.warning(
                "MQTT Status for %s (%s): %s", 
                status["device_name"], device_id,
                "🟢 CONNECTED" if status["is_connected"] else "🔴 DISCONNECTED"
            )
            
            if status["is_connected"]:
                uptime = "Unknown"
                if status["uptime_seconds"] is not None:
                    minutes, seconds = divmod(status["uptime_seconds"], 60)
                    hours, minutes = divmod(minutes, 60)
                    uptime = f"{hours}h {minutes}m {seconds}s"
                
                last_msg = "Never"
                if status["last_message_seconds_ago"] is not None:
                    minutes, seconds = divmod(status["last_message_seconds_ago"], 60)
                    hours, minutes = divmod(minutes, 60)
                    last_msg = f"{hours}h {minutes}m {seconds}s ago"
                
                _LOGGER.warning(
                    "  - Connected for: %s, %d messages received, last message: %s",
                    uptime, status["messages_received"], last_msg
                )
            
            # Request a status update from each client to verify it still works
            if status["is_connected"]:
                _LOGGER.warning("  - Testing connection by requesting status update...")
                client.publish_status_request()
    
    # Register the service
    hass.services.async_register(
        DOMAIN, SERVICE_CHECK_MQTT_STATUS, async_check_mqtt_status
    )
    
    # Load platform entities
    for platform in PLATFORMS:
        await hass.config_entries.async_forward_entry_setup(entry, platform)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    # Unload platforms
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ]
        )
    )
    
    # Clean up MQTT clients
    if unload_ok and entry.entry_id in hass.data[DOMAIN]:
        entry_data = hass.data[DOMAIN][entry.entry_id]
        if "mqtt_clients" in entry_data:
            for client in entry_data["mqtt_clients"].values():
                client.disconnect()
        
        # Clean up data
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok

class OlarmDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Olarm data."""

    def __init__(self, hass: HomeAssistant, client: OlarmApiClient, auth=None):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.auth = auth
        self.devices = {}

    async def _async_update_data(self):
        """Fetch data from Olarm."""
        try:
            # If using auth, ensure token is valid
            if self.auth:
                await self.auth.ensure_access_token()
                # Update client access token if it changed
                tokens = self.auth.get_tokens()
                if tokens["access_token"] != self.client.api_key:
                    self.client.api_key = tokens["access_token"]
                    self.client.headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            
            # Get all devices
            result = await self.client.get_devices()
            devices = {}
            
            # Get detailed info for each device
            for device in result.get("data", []):
                device_id = device["deviceId"]
                # Don't need to fetch detail again as the devices endpoint returns full details
                devices[device_id] = device
            
            self.devices = devices
            return devices
        except OlarmApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}")