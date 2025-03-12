"""The Olarm integration."""
import asyncio
import logging
import time
from datetime import datetime, timedelta

import aiohttp
import async_timeout
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from .api import OlarmApiClient, OlarmApiError
from .auth import OlarmAuth
from .mqtt import OlarmMqttClient
from .handler import OlarmMessageHandler
from .debug import direct_log, mqtt_log, log_exception
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

# Set up loggers
_LOGGER = logging.getLogger(__name__)

# Force the logger to show all messages at least at INFO level
_LOGGER.setLevel(logging.INFO)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Olarm component."""
    hass.data.setdefault(DOMAIN, {})
    
    # Register shutdown hook
    async def shutdown_hook(event):
        """Shutdown hook for the integration."""
        direct_log("Olarm integration shutdown hook triggered")
        for entry_id, data in hass.data.get(DOMAIN, {}).items():
            if "mqtt_clients" in data:
                for client in data["mqtt_clients"].values():
                    direct_log(f"Disconnecting MQTT client for {client.device_name}")
                    client.disconnect()
    
    # Register shutdown listener
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown_hook)
    
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Olarm from a config entry."""
    # Log entry data (without passwords)
    entry_data_safe = {k: v for k, v in entry.data.items() if k != CONF_USER_PASS}
    direct_log(f"Setting up Olarm integration with data: {entry_data_safe}")
    _LOGGER.info("Setting up Olarm integration with data: %s", entry_data_safe)
    
    session = async_get_clientsession(hass)
    
    # Set up data structures
    hass.data.setdefault(DOMAIN, {})
    entry_data = hass.data[DOMAIN].setdefault(entry.entry_id, {})
    mqtt_only = entry.data.get(CONF_MQTT_ONLY, False)
    debug_mqtt = entry.data.get(CONF_DEBUG_MQTT, False)
    
    # Initialize either with API key or email/password auth
    if CONF_API_KEY in entry.data:
        # Legacy API key method
        direct_log("Setting up with API key authentication")
        _LOGGER.info("Setting up with API key authentication")
        client = OlarmApiClient(entry.data[CONF_API_KEY], session)
        entry_data["client"] = client
        
        # Set up coordinator
        coordinator = OlarmDataUpdateCoordinator(hass, client)
        
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as ex:
            log_exception(ex, "Coordinator refresh")
            _LOGGER.error("Error in coordinator refresh: %s", ex)
            raise
            
        entry_data["coordinator"] = coordinator
        
        # We don't have MQTT with API key method
        entry_data["mqtt_enabled"] = False
        
    elif CONF_USER_EMAIL_PHONE in entry.data and CONF_USER_PASS in entry.data:
        # New email/password auth with MQTT support
        direct_log("Setting up with email/password authentication")
        _LOGGER.info("Setting up with email/password authentication")
        mqtt_log("Setting up Olarm integration with email/password auth")
        
        if mqtt_only:
            direct_log("⚠️ MQTT-ONLY MODE ENABLED: API fallback will be disabled")
            _LOGGER.warning("⚠️ MQTT-ONLY MODE ENABLED: API fallback will be disabled")
            mqtt_log("⚠️ MQTT-ONLY MODE ENABLED: API fallback will be disabled")
        
        # Initialize auth
        auth = OlarmAuth(
            hass, 
            entry.data[CONF_USER_EMAIL_PHONE], 
            entry.data[CONF_USER_PASS], 
            session
        )
        
        try:
            direct_log("Starting auth initialization...")
            auth_success = await auth.initialize()
            if not auth_success:
                error_msg = "Authentication initialization failed"
                direct_log(error_msg)
                _LOGGER.error(error_msg)
                raise Exception(error_msg)
                
            direct_log("Auth initialized successfully")
            _LOGGER.info("Auth initialized successfully")
                
        except Exception as ex:
            log_exception(ex, "Auth")
            direct_log(f"Auth error: {ex}")
            _LOGGER.error("Auth error: %s", ex)
            raise
        
        entry_data["auth"] = auth
        
        # Get devices from auth
        devices = auth.get_devices()
        if not devices:
            direct_log("No devices found for user")
            _LOGGER.warning("No devices found for user")
            mqtt_log("No devices found for user - MQTT setup will be skipped")
        else:
            direct_log(f"Found {len(devices)} devices")
            _LOGGER.info("Found %d devices", len(devices))
            
        # Create API client from access token
        tokens = auth.get_tokens()
        if tokens["access_token"]:
            direct_log("Creating API client with token")
            _LOGGER.debug("Creating API client with token")
                
            client = OlarmApiClient(tokens["access_token"], session)
            entry_data["client"] = client
            
            # Set up coordinator
            coordinator = OlarmDataUpdateCoordinator(hass, client, auth)
            try:
                await coordinator.async_config_entry_first_refresh()
                direct_log("Coordinator refresh succeeded")
                _LOGGER.debug("Coordinator refresh succeeded")
            except Exception as ex:
                log_exception(ex, "Coordinator refresh")
                direct_log(f"Coordinator refresh error: {ex}")
                _LOGGER.error("Coordinator refresh error: %s", ex)
                raise
                
            entry_data["coordinator"] = coordinator
        
        # Set up message handler
        message_handler = OlarmMessageHandler(hass, entry.entry_id)
        entry_data["message_handler"] = message_handler
        
        # Set up MQTT clients for each device
        mqtt_clients = {}
        direct_log(f"🔄 Setting up MQTT for {len(devices)} devices")
        _LOGGER.warning("🔄 Setting up MQTT for %d devices", len(devices))
        mqtt_log(f"Setting up MQTT for {len(devices)} devices")
        
        for device in devices:
            device_id = device["id"]
            imei = device["imei"]
            device_name = device.get("name", "Unknown Device")
            
            direct_log(f"🔄 Setting up MQTT for device: {device_name} (IMEI: {imei})")
            _LOGGER.warning("🔄 Setting up MQTT for device: %s (IMEI: %s)", device_name, imei)
            mqtt_log(f"Setting up MQTT for device: {device_name} (IMEI: {imei})")
            
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
            try:
                connected = await mqtt_client.connect()
                if connected:
                    direct_log(f"✅ MQTT connected for device: {device_name}")
                    _LOGGER.warning("✅ MQTT connected for device: %s", device_name)
                    mqtt_log(f"✅ MQTT connected for device: {device_name}")
                    mqtt_clients[device_id] = mqtt_client
                else:
                    direct_log(f"❌ MQTT connection failed for device: {device_name}")
                    _LOGGER.error("❌ MQTT connection failed for device: %s", device_name)
                    mqtt_log(f"❌ MQTT connection failed for device: {device_name}")
                    
                    if mqtt_only:
                        direct_log(f"⚠️ MQTT-ONLY MODE: Device {device_name} will be unavailable")
                        _LOGGER.error("⚠️ MQTT-ONLY MODE: Device %s will be unavailable", device_name)
                        mqtt_log(f"⚠️ MQTT-ONLY MODE: Device {device_name} will be unavailable")
                    else:
                        direct_log(f"⚠️ Falling back to API polling for device: {device_name}")
                        _LOGGER.warning("⚠️ Falling back to API polling for device: %s", device_name)
                        mqtt_log(f"⚠️ Falling back to API polling for device: {device_name}")
            except Exception as mqtt_ex:
                log_exception(mqtt_ex, f"MQTT connection for {device_name}")
                direct_log(f"MQTT connection error for {device_name}: {mqtt_ex}")
                _LOGGER.error("MQTT connection error for %s: %s", device_name, mqtt_ex)
                # Continue to next device, don't raise the exception
        
        entry_data["mqtt_clients"] = mqtt_clients
        
        # Setup periodic MQTT checks if we have clients
        if mqtt_clients:
            mqtt_count = len(mqtt_clients)
            total_count = len(devices)
            success_percent = int(mqtt_count/total_count*100) if total_count > 0 else 0
            
            direct_log(
                f"✅ MQTT setup complete: {mqtt_count}/{total_count} devices connected ({success_percent}%)"
            )
            _LOGGER.warning(
                "✅ MQTT setup complete: %d/%d devices connected (%s%%)",
                mqtt_count, total_count, success_percent
            )
            mqtt_log(f"✅ MQTT setup complete: {mqtt_count}/{total_count} devices connected ({success_percent}%)")
            entry_data["mqtt_enabled"] = True
            
            # Set up a periodic task to check MQTT status
            async def check_mqtt_periodically(now=None):
                """Check MQTT status periodically and log results."""
                direct_log("🔄 Performing periodic MQTT check")
                _LOGGER.warning("🔄 Performing periodic MQTT check")
                mqtt_log("🔄 Performing periodic MQTT check")
                
                for device_id, client in mqtt_clients.items():
                    status = client.get_status()
                    connection_state = "🟢 CONNECTED" if status["is_connected"] else "🔴 DISCONNECTED"
                    
                    direct_log(
                        f"MQTT Status for {status['device_name']}: {connection_state}, Messages: {status['messages_received']}"
                    )
                    _LOGGER.warning(
                        "MQTT Status for %s: %s, Messages: %d",
                        status["device_name"], connection_state, status["messages_received"]
                    )
                    mqtt_log(f"Status for {status['device_name']}: {connection_state}, Messages: {status['messages_received']}")
                    
                    # If connected, request a status update
                    if status["is_connected"]:
                        direct_log(f"Requesting MQTT update for {status['device_name']}")
                        _LOGGER.warning("Requesting MQTT update for %s", status["device_name"])
                        mqtt_log(f"Requesting update for {status['device_name']}")
                        client.publish_status_request()
                    else:
                        direct_log(f"Attempting to reconnect {status['device_name']}")
                        _LOGGER.warning("Attempting to reconnect %s", status["device_name"])
                        mqtt_log(f"Attempting to reconnect {status['device_name']}")
                        hass.async_create_task(client.connect())
            
            # Register a periodic check every 5 minutes
            entry_data["mqtt_checker"] = async_track_time_interval(
                hass, 
                check_mqtt_periodically, 
                timedelta(minutes=5)
            )
            
            # Also run once right now
            hass.async_create_task(check_mqtt_periodically())
        else:
            if mqtt_only:
                direct_log("⚠️ MQTT-ONLY MODE: No MQTT connections were established, integration may not function!")
                _LOGGER.error("⚠️ MQTT-ONLY MODE: No MQTT connections were established, integration may not function!")
                mqtt_log("⚠️ MQTT-ONLY MODE: No MQTT connections were established, integration may not function!")
            else:
                direct_log("⚠️ No MQTT connections established, using API polling only")
                _LOGGER.warning("⚠️ No MQTT connections established, using API polling only")
                mqtt_log("⚠️ No MQTT connections established, using API polling only")
            entry_data["mqtt_enabled"] = False
    
    else:
        # This shouldn't happen
        _LOGGER.error("Neither API key nor email/password provided")
        return False
    
    # Register the MQTT status service
    async def async_check_mqtt_status(call):
        """Service to check MQTT status."""
        direct_log("Manual MQTT status check triggered")
        mqtt_log("Manual MQTT status check triggered")
        
        if "mqtt_clients" not in entry_data:
            direct_log("No MQTT clients available - email/password authentication required for MQTT support")
            _LOGGER.warning("No MQTT clients available - email/password authentication required for MQTT support")
            mqtt_log("No MQTT clients available - email/password authentication required")
            return
            
        mqtt_clients = entry_data["mqtt_clients"]
        if not mqtt_clients:
            direct_log("No active MQTT clients found")
            _LOGGER.warning("No active MQTT clients found")
            mqtt_log("No active MQTT clients found")
            return
            
        for device_id, client in mqtt_clients.items():
            status = client.get_status()
            connection_state = "🟢 CONNECTED" if status["is_connected"] else "🔴 DISCONNECTED"
            
            direct_log(f"MQTT Status for {status['device_name']} ({device_id}): {connection_state}")
            _LOGGER.warning(
                "MQTT Status for %s (%s): %s", 
                status["device_name"], device_id, connection_state
            )
            mqtt_log(f"Status for {status['device_name']} ({device_id}): {connection_state}")
            
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
                
                direct_log(
                    f"  - Connected for: {uptime}, {status['messages_received']} messages received, last message: {last_msg}"
                )
                _LOGGER.warning(
                    "  - Connected for: %s, %d messages received, last message: %s",
                    uptime, status["messages_received"], last_msg
                )
                mqtt_log(f"  - Connected for: {uptime}, {status['messages_received']} messages received, last message: {last_msg}")
            
            # Request a status update from each client to verify it still works
            if status["is_connected"]:
                direct_log("  - Testing connection by requesting status update...")
                _LOGGER.warning("  - Testing connection by requesting status update...")
                mqtt_log("  - Testing connection by requesting status update...")
                client.publish_status_request()
            else:
                direct_log("  - Attempting to reconnect...")
                _LOGGER.warning("  - Attempting to reconnect...")
                mqtt_log("  - Attempting to reconnect...")
                hass.async_create_task(client.connect())
    
    # Register the service
    hass.services.async_register(
        DOMAIN, SERVICE_CHECK_MQTT_STATUS, async_check_mqtt_status
    )
    direct_log(f"Registered service: {DOMAIN}.{SERVICE_CHECK_MQTT_STATUS}")
    mqtt_log(f"Registered service: {DOMAIN}.{SERVICE_CHECK_MQTT_STATUS}")
    
    # Load platform entities
    for platform in PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    direct_log("Olarm integration setup complete")
    mqtt_log("Olarm integration setup complete")
    _LOGGER.info("Olarm integration setup complete")
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    direct_log("Unloading Olarm integration")
    mqtt_log("Unloading Olarm integration")
    
    # Unload platforms using the new method
    unload_ok = await hass.config_entries.async_unload_entry_platforms(entry, PLATFORMS)
    
    # Clean up MQTT clients
    if unload_ok and entry.entry_id in hass.data[DOMAIN]:
        entry_data = hass.data[DOMAIN][entry.entry_id]
        
        # Cancel periodic MQTT checks if they exist
        if "mqtt_checker" in entry_data:
            entry_data["mqtt_checker"]()
            direct_log("Cancelled MQTT periodic checks")
            
        # Disconnect MQTT clients
        if "mqtt_clients" in entry_data:
            mqtt_count = len(entry_data["mqtt_clients"])
            direct_log(f"Disconnecting {mqtt_count} MQTT clients")
            mqtt_log(f"Disconnecting {mqtt_count} MQTT clients")
            for client in entry_data["mqtt_clients"].values():
                client.disconnect()
        
        # Clean up data
        hass.data[DOMAIN].pop(entry.entry_id)
    
    direct_log("Olarm integration unloaded")
    mqtt_log("Olarm integration unloaded")
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
                    direct_log("Updating API client with new access token")
                    self.client.api_key = tokens["access_token"]
                    self.client.headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            
            direct_log("Performing API data update...")
            mqtt_log("Performing API data update...")
            # Get all devices
            result = await self.client.get_devices()
            devices = {}
            
            # Get detailed info for each device
            for device in result.get("data", []):
                device_id = device["deviceId"]
                # Don't need to fetch detail again as the devices endpoint returns full details
                devices[device_id] = device
            
            self.devices = devices
            device_count = len(devices)
            direct_log(f"API data update complete, found {device_count} devices")
            mqtt_log(f"API data update complete, found {device_count} devices")
            return devices
        except OlarmApiError as err:
            error_msg = f"Error communicating with API: {err}"
            direct_log(error_msg)
            mqtt_log(f"❌ API Error: {err}", "error")
            raise UpdateFailed(error_msg)