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

from .api import OlarmApiClient, OlarmApiError
from .auth import OlarmAuth
from .mqtt import OlarmMqttClient
from .handler import OlarmMessageHandler
from .const import (
    CONF_API_KEY,
    CONF_USER_EMAIL_PHONE,
    CONF_USER_PASS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SIGNAL_OLARM_MQTT_UPDATE,
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
        _LOGGER.debug("Setting up with email/password authentication")
        
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
        for device in devices:
            device_id = device["id"]
            imei = device["imei"]
            
            # Create MQTT client
            mqtt_client = OlarmMqttClient(
                hass, 
                imei, 
                tokens["access_token"],
                device_id
            )
            
            # Register message callback
            mqtt_client.register_message_callback(message_handler.process_mqtt_message)
            
            # Connect to MQTT
            connected = await mqtt_client.connect()
            if connected:
                _LOGGER.info("Connected to MQTT for device %s", device_id)
                mqtt_clients[device_id] = mqtt_client
            else:
                _LOGGER.error("Failed to connect to MQTT for device %s", device_id)
        
        entry_data["mqtt_clients"] = mqtt_clients
        entry_data["mqtt_enabled"] = bool(mqtt_clients)
    
    else:
        # This shouldn't happen
        _LOGGER.error("Neither API key nor email/password provided")
        return False
    
    # Load platform entities
    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )
    
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
