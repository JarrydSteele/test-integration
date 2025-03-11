"""The Olarm integration."""
import asyncio
import logging
from datetime import timedelta

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from .api import OlarmApiClient, OlarmApiError
from .auth import OlarmAuth
from .mqtt import OlarmMqttClient, mqtt_log
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
    # Add direct debug logging
    with open("/config/olarm_setup.log", "a") as f:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"{timestamp} - Starting integration setup\n")
        
        # Log config entry data (without passwords)
        entry_data_safe = {k: v for k, v in entry.data.items() if k != "user_pass"}
        f.write(f"{timestamp} - Entry data: {entry_data_safe}\n")
    
    session = async_get_clientsession(hass)
    
    # Set up data structures
    hass.data.setdefault(DOMAIN, {})
    entry_data = hass.data[DOMAIN].setdefault(entry.entry_id, {})
    mqtt_only = entry.data.get(CONF_MQTT_ONLY, False)
    debug_mqtt = entry.data.get(CONF_DEBUG_MQTT, False)
    
    with open("/config/olarm_setup.log", "a") as f:
        f.write(f"{timestamp} - MQTT Only: {mqtt_only}, Debug MQTT: {debug_mqtt}\n")
    
    # Initialize either with API key or email/password auth
    if CONF_API_KEY in entry.data:
        # Legacy API key method
        with open("/config/olarm_setup.log", "a") as f:
            f.write(f"{timestamp} - Setting up with API key authentication\n")
            
        client = OlarmApiClient(entry.data[CONF_API_KEY], session)
        entry_data["client"] = client
        
        # Set up coordinator
        coordinator = OlarmDataUpdateCoordinator(hass, client)
        
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as ex:
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - Error in coordinator refresh: {str(ex)}\n")
            raise
            
        entry_data["coordinator"] = coordinator
        
        # We don't have MQTT with API key method
        entry_data["mqtt_enabled"] = False
        
    elif CONF_USER_EMAIL_PHONE in entry.data and CONF_USER_PASS in entry.data:
        # New email/password auth with MQTT support
        with open("/config/olarm_setup.log", "a") as f:
            f.write(f"{timestamp} - Setting up with email/password authentication\n")
        
        if mqtt_only:
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - MQTT-ONLY MODE ENABLED\n")
        
        # Initialize auth
        auth = OlarmAuth(
            hass, 
            entry.data[CONF_USER_EMAIL_PHONE], 
            entry.data[CONF_USER_PASS], 
            session
        )
        
        try:
            auth_success = await auth.initialize()
            if not auth_success:
                with open("/config/olarm_setup.log", "a") as f:
                    f.write(f"{timestamp} - Auth initialization failed\n")
                raise Exception("Authentication initialization failed")
                
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - Auth initialized successfully\n")
                
        except Exception as ex:
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - Auth error: {str(ex)}\n")
            raise
        
        entry_data["auth"] = auth
        
        # Get devices from auth
        devices = auth.get_devices()
        if not devices:
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - No devices found for user\n")
        else:
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - Found {len(devices)} devices\n")
            
        # Create API client from access token
        tokens = auth.get_tokens()
        if tokens["access_token"]:
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - Creating API client with token\n")
                
            client = OlarmApiClient(tokens["access_token"], session)
            entry_data["client"] = client
            
            # Set up coordinator
            coordinator = OlarmDataUpdateCoordinator(hass, client, auth)
            try:
                await coordinator.async_config_entry_first_refresh()
                with open("/config/olarm_setup.log", "a") as f:
                    f.write(f"{timestamp} - Coordinator refresh succeeded\n")
            except Exception as ex:
                with open("/config/olarm_setup.log", "a") as f:
                    f.write(f"{timestamp} - Coordinator refresh error: {str(ex)}\n")
                raise
                
            entry_data["coordinator"] = coordinator
        
        # Set up message handler
        message_handler = OlarmMessageHandler(hass, entry.entry_id)
        entry_data["message_handler"] = message_handler
        
        # Set up MQTT clients for each device
        mqtt_clients = {}
        with open("/config/olarm_setup.log", "a") as f:
            f.write(f"{timestamp} - Setting up MQTT for {len(devices)} devices\n")
        
        for device in devices:
            device_id = device["id"]
            imei = device["imei"]
            device_name = device.get("name", "Unknown Device")
            
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - Setting up MQTT for device: {device_name} (IMEI: {imei})\n")
            
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
                    with open("/config/olarm_setup.log", "a") as f:
                        f.write(f"{timestamp} - MQTT connected for device: {device_name}\n")
                    mqtt_clients[device_id] = mqtt_client
                else:
                    with open("/config/olarm_setup.log", "a") as f:
                        f.write(f"{timestamp} - MQTT connection failed for device: {device_name}\n")
                    
                    if mqtt_only:
                        with open("/config/olarm_setup.log", "a") as f:
                            f.write(f"{timestamp} - MQTT-ONLY MODE: Device {device_name} will be unavailable\n")
                    else:
                        with open("/config/olarm_setup.log", "a") as f:
                            f.write(f"{timestamp} - Falling back to API polling for device: {device_name}\n")
            except Exception as mqtt_ex:
                with open("/config/olarm_setup.log", "a") as f:
                    f.write(f"{timestamp} - MQTT connection error for {device_name}: {str(mqtt_ex)}\n")
                # Continue to next device, don't raise the exception
        
        entry_data["mqtt_clients"] = mqtt_clients
        
        # Setup periodic MQTT checks if we have clients
        if mqtt_clients:
            mqtt_count = len(mqtt_clients)
            total_count = len(devices)
            success_percent = int(mqtt_count/total_count*100) if total_count > 0 else 0
            
            with open("/config/olarm_setup.log", "a") as f:
                f.write(f"{timestamp} - MQTT setup complete: {mqtt_count}/{total_count} devices connected ({success_percent}%)\n")
                
            entry_data["mqtt_enabled"] = True
            
            # Set up a periodic task to check MQTT status
            async def check_mqtt_periodically(now=None):
                """Check MQTT status periodically and log results."""
                with open("/config/olarm_status.log", "a") as f:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{ts} - Performing periodic MQTT check\n")
                
                for device_id, client in mqtt_clients.items():
                    status = client.get_status()
                    connection_state = "CONNECTED" if status["is_connected"] else "DISCONNECTED"
                    
                    with open("/config/olarm_status.log", "a") as f:
                        f.write(f"{ts} - {status['device_name']}: {connection_state}, Messages: {status['messages_received']}\n")
                    
                    # If connected, request a status update
                    if status["is_connected"]:
                        with open("/config/olarm_status.log", "a") as f:
                            f.write(f"{ts} - Requesting update for {status['device_name']}\n")
                        client.publish_status_request()
                    else:
                        with open("/config/olarm_status.log", "a") as f:
                            f.write(f"{ts} - Attempting to reconnect {status['device_name']}\n")
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
                with open("/config/olarm_setup.log", "a") as f:
                    f.write(f"{timestamp} - MQTT-ONLY MODE: No MQTT connections were established, integration may not function!\n")
            else:
                with open("/config/olarm_setup.log", "a") as f:
                    f.write(f"{timestamp} - No MQTT connections established, using API polling only\n")
            entry_data["mqtt_enabled"] = False
    
    else:
        # This shouldn't happen
        with open("/config/olarm_setup.log", "a") as f:
            f.write(f"{timestamp} - Neither API key nor email/password provided\n")
        return False
    
    # Register the MQTT status service
    async def async_check_mqtt_status(call):
        """Service to check MQTT status."""
        with open("/config/olarm_status.log", "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{ts} - Manual MQTT status check triggered\n")
        
        if "mqtt_clients" not in entry_data:
            with open("/config/olarm_status.log", "a") as f:
                f.write(f"{ts} - No MQTT clients available\n")
            return
            
        mqtt_clients = entry_data["mqtt_clients"]
        if not mqtt_clients:
            with open("/config/olarm_status.log", "a") as f:
                f.write(f"{ts} - No active MQTT clients found\n")
            return
            
        for device_id, client in mqtt_clients.items():
            status = client.get_status()
            connection_state = "CONNECTED" if status["is_connected"] else "DISCONNECTED"
            
            with open("/config/olarm_status.log", "a") as f:
                f.write(f"{ts} - Status for {status['device_name']} ({device_id}): {connection_state}\n")
            
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
                
                with open("/config/olarm_status.log", "a") as f:
                    f.write(f"{ts} - Connected for: {uptime}, {status['messages_received']} messages received, last message: {last_msg}\n")
            
            # Request a status update from each client to verify it still works
            if status["is_connected"]:
                with open("/config/olarm_status.log", "a") as f:
                    f.write(f"{ts} - Testing connection by requesting status update\n")
                client.publish_status_request()
            else:
                with open("/config/olarm_status.log", "a") as f:
                    f.write(f"{ts} - Attempting to reconnect\n")
                hass.async_create_task(client.connect())
    
    # Register the service
    hass.services.async_register(
        DOMAIN, SERVICE_CHECK_MQTT_STATUS, async_check_mqtt_status
    )
    
    # Load platform entities
    for platform in PLATFORMS:
        await hass.config_entries.async_forward_entry_setup(entry, platform)
    
    with open("/config/olarm_setup.log", "a") as f:
        f.write(f"{timestamp} - Integration setup complete\n")
        
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    mqtt_log("Unloading Olarm integration")
    
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
        
        # Cancel periodic MQTT checks if they exist
        if "mqtt_checker" in entry_data:
            entry_data["mqtt_checker"]()
            
        # Disconnect MQTT clients
        if "mqtt_clients" in entry_data:
            mqtt_log(f"Disconnecting {len(entry_data['mqtt_clients'])} MQTT clients")
            for client in entry_data["mqtt_clients"].values():
                client.disconnect()
        
        # Clean up data
        hass.data[DOMAIN].pop(entry.entry_id)
    
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
                    self.client.api_key = tokens["access_token"]
                    self.client.headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            
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
            mqtt_log(f"API data update complete, found {len(devices)} devices")
            return devices
        except OlarmApiError as err:
            mqtt_log(f"❌ API Error: {err}")
            raise UpdateFailed(f"Error communicating with API: {err}")