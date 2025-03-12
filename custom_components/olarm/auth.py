"""Authentication handler for Olarm integration."""
import os
import logging
import json
import time
from typing import Dict, List, Optional, Any, Tuple
import asyncio

import aiohttp
import async_timeout
from aiohttp import ClientSession

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import json as json_util

from .const import DOMAIN, CONF_USER_EMAIL_PHONE, CONF_USER_PASS
from .debug import direct_log  # Import the direct logger

_LOGGER = logging.getLogger(__name__)
# Force the logger to show all messages at least at INFO level
_LOGGER.setLevel(logging.INFO)

class OlarmAuthError(Exception):
    """Raised when Olarm authentication fails."""
    pass

class OlarmAuth:
    """Olarm authentication handler."""

    def __init__(self, hass: HomeAssistant, user_email_phone: str, user_pass: str, session: ClientSession):
        """Initialize the auth handler."""
        self.hass = hass
        self.user_email_phone = user_email_phone
        self.user_pass = user_pass
        self.session = session
        
        self.user_index = None
        self.user_id = None
        self.access_token = None
        self.refresh_token = None
        self.token_expiration = None
        self.devices = []
        
        # Set the storage path for tokens
        self.storage_file = self.hass.config.path(f".{DOMAIN}_tokens.json")
        
        direct_log(f"Auth initialized for user: {user_email_phone}")
        _LOGGER.warning("Auth initialized for user: %s", user_email_phone)

    async def initialize(self) -> bool:
        """Initialize authentication."""
        direct_log("Starting authentication initialization")
        _LOGGER.warning("Starting authentication initialization")
        
        # Load tokens from storage
        await self._load_tokens_from_storage()
        
        if not self.access_token or not self.refresh_token:
            # No valid tokens, login
            direct_log("No valid tokens found, performing login")
            _LOGGER.warning("No valid tokens found, performing login")
            return await self.login()
        else:
            # Ensure access token is valid
            direct_log("Tokens found, ensuring they are valid")
            _LOGGER.warning("Tokens found, ensuring they are valid")
            return await self.ensure_access_token()

    async def login(self) -> bool:
        """Log in to Olarm and obtain tokens."""
        direct_log(f"Attempting login for: {self.user_email_phone}")
        _LOGGER.warning("Attempting login for: %s", self.user_email_phone)
        
        try:
            # Using the context manager for the timeout
            async with async_timeout.timeout(30):
                direct_log("Making login request to auth.olarm.com")
                response = await self.session.post(
                    "https://auth.olarm.com/api/v4/oauth/login/mobile",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "userEmailPhone": self.user_email_phone,
                        "userPass": self.user_pass,
                    },
                )
                
                direct_log(f"Login response status: {response.status}")
                _LOGGER.warning("Login response status: %s", response.status)
                    
                if response.status != 200:
                    response_text = await response.text()
                    direct_log(f"Login failed: {response.status} {response_text}")
                    _LOGGER.error("Login failed: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.access_token = data.get("oat")
                self.refresh_token = data.get("ort")
                self.token_expiration = data.get("oatExpire")
                
                token_preview = self.access_token[:10] if self.access_token else "None"
                direct_log(f"Login successful! Access token: {token_preview}...")
                _LOGGER.warning("Login successful! Access token: %s...", token_preview)
                
                # Fetch user index
                fetch_success = await self._fetch_user_index()
                if not fetch_success:
                    direct_log("Failed to fetch user index")
                    _LOGGER.error("Failed to fetch user index")
                    return False
                
                # Save tokens
                await self._save_tokens_to_storage()
                
                # Fetch devices
                devices_success = await self._fetch_devices()
                if not devices_success:
                    direct_log("Failed to fetch devices")
                    _LOGGER.error("Failed to fetch devices")
                    return False
                
                return True
                
        except asyncio.TimeoutError:
            direct_log("Login timed out")
            _LOGGER.error("Login timed out")
            return False
        except Exception as error:
            direct_log(f"Login error: {error}")
            _LOGGER.error("Login error: %s", error)
            return False

    async def _fetch_user_index(self) -> bool:
        """Fetch user index from Olarm API."""
        if not self.access_token:
            direct_log("Cannot fetch user index: No access token")
            _LOGGER.error("Cannot fetch user index: No access token")
            return False
        
        direct_log("Fetching user index")
        _LOGGER.warning("Fetching user index")
        
        try:
            async with async_timeout.timeout(30):
                url = f"https://auth.olarm.com/api/v4/oauth/federated-link-existing?oat={self.access_token}"
                direct_log(f"Making request to: {url}")
                response = await self.session.post(
                    url,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "userEmailPhone": self.user_email_phone,
                        "userPass": self.user_pass,
                        "captchaToken": "olarmapp",
                    },
                )
                
                direct_log(f"User index response status: {response.status}")
                _LOGGER.warning("User index response status: %s", response.status)
                
                if response.status != 200:
                    response_text = await response.text()
                    direct_log(f"Failed to fetch user index: {response.status} {response_text}")
                    _LOGGER.error("Failed to fetch user index: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.user_index = data.get("userIndex")
                self.user_id = data.get("userId")
                
                direct_log(f"User index: {self.user_index}, User ID: {self.user_id}")
                _LOGGER.warning("User index: %s, User ID: %s", self.user_index, self.user_id)
                    
                return True
                
        except asyncio.TimeoutError:
            direct_log("Fetch user index timed out")
            _LOGGER.error("Fetch user index timed out")
            return False
        except Exception as error:
            direct_log(f"Error fetching user index: {error}")
            _LOGGER.error("Error fetching user index: %s", error)
            return False

    async def _fetch_devices(self) -> bool:
        """Fetch devices from Olarm API."""
        if not self.access_token or self.user_index is None:
            direct_log("Cannot fetch devices: Missing access token or user index")
            _LOGGER.error("Cannot fetch devices: Missing access token or user index")
            return False
        
        direct_log(f"Fetching devices for user index: {self.user_index}")
        _LOGGER.warning("Fetching devices for user index: %s", self.user_index)
        
        try:
            async with async_timeout.timeout(30):
                url = f"https://api-legacy.olarm.com/api/v2/users/{self.user_index}"
                direct_log(f"Making request to: {url}")
                response = await self.session.get(
                    url,
                    headers={"Authorization": f"Bearer {self.access_token}"}
                )
                
                direct_log(f"Devices response status: {response.status}")
                _LOGGER.warning("Devices response status: %s", response.status)
                
                if response.status != 200:
                    response_text = await response.text()
                    direct_log(f"Failed to fetch devices: {response.status} {response_text}")
                    _LOGGER.error("Failed to fetch devices: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.devices = [{
                    "id": device.get("id"),
                    "imei": device.get("IMEI"),
                    "name": device.get("name", "Olarm Device"),
                } for device in data.get("devices", [])]
                
                direct_log(f"Found {len(self.devices)} devices")
                _LOGGER.warning("Found %d devices", len(self.devices))
                for device in self.devices:
                    device_info = f"Device: {device.get('name')}, IMEI: {device.get('imei')}"
                    direct_log(device_info)
                    _LOGGER.warning(device_info)
                        
                return True
                
        except asyncio.TimeoutError:
            direct_log("Fetch devices timed out")
            _LOGGER.error("Fetch devices timed out")
            return False
        except Exception as error:
            direct_log(f"Error fetching devices: {error}")
            _LOGGER.error("Error fetching devices: %s", error)
            return False

    async def refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            direct_log("Cannot refresh token: No refresh token available")
            _LOGGER.error("Cannot refresh token: No refresh token available")
            return await self.login()
        
        direct_log("Refreshing access token")
        _LOGGER.warning("Refreshing access token")
        
        try:
            async with async_timeout.timeout(30):
                response = await self.session.post(
                    "https://auth.olarm.com/api/v4/oauth/refresh",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={"ort": self.refresh_token},
                )
                
                direct_log(f"Token refresh response status: {response.status}")
                _LOGGER.warning("Token refresh response status: %s", response.status)
                
                if response.status != 200:
                    response_text = await response.text()
                    direct_log(f"Token refresh failed: {response.status} {response_text}")
                    _LOGGER.error("Token refresh failed: %s %s", response.status, response_text)
                    return await self.login()
                
                data = await response.json()
                self.access_token = data.get("oat")
                self.refresh_token = data.get("ort")
                self.token_expiration = data.get("oatExpire")
                
                direct_log("Token refresh successful")
                _LOGGER.warning("Token refresh successful")
                
                # If user_index or user_id is missing, fetch it
                if self.user_index is None or self.user_id is None:
                    await self._fetch_user_index()
                
                # Save updated tokens
                await self._save_tokens_to_storage()
                
                return True
                
        except asyncio.TimeoutError:
            direct_log("Token refresh timed out")
            _LOGGER.error("Token refresh timed out")
            return False
        except Exception as error:
            direct_log(f"Error refreshing token: {error}")
            _LOGGER.error("Error refreshing token: %s", error)
            return False

    async def ensure_access_token(self) -> bool:
        """Ensure the access token is valid, refresh if needed."""
        if not self.access_token or not self.token_expiration:
            direct_log("No token/expiration available, performing login")
            _LOGGER.warning("No token/expiration available, performing login")
            return await self.login()
        
        # Check if token is expired or about to expire (within 60 seconds)
        current_time = int(time.time() * 1000)  # Convert to milliseconds
        if current_time >= (self.token_expiration - 60000):
            direct_log("Token expired or about to expire, refreshing")
            _LOGGER.warning("Token expired or about to expire, refreshing")
            return await self.refresh_access_token()
        
        # Token is valid, fetch devices if we don't have them
        if not self.devices:
            direct_log("Token valid but no devices, fetching devices")
            _LOGGER.warning("Token valid but no devices, fetching devices")
            return await self._fetch_devices()
        
        direct_log("Token valid, all data available")
        _LOGGER.debug("Token valid, all data available")
            
        return True

    async def _load_tokens_from_storage(self) -> None:
        """Load tokens from storage."""
        try:
            if not os.path.exists(self.storage_file):
                direct_log("No token storage file exists")
                _LOGGER.debug("No token storage file exists")
                return
            
            tokens = await self.hass.async_add_executor_job(json_util.load_json, self.storage_file)
            if tokens:
                self.user_index = tokens.get("user_index")
                self.user_id = tokens.get("user_id")
                self.access_token = tokens.get("access_token")
                self.refresh_token = tokens.get("refresh_token")
                self.token_expiration = tokens.get("token_expiration")
                direct_log("Loaded tokens from storage")
                _LOGGER.debug("Loaded tokens from storage")
                    
        except Exception as ex:
            direct_log(f"Failed to load tokens from storage: {ex}")
            _LOGGER.error("Failed to load tokens from storage: %s", ex)
                
            self.user_index = None
            self.user_id = None
            self.access_token = None
            self.refresh_token = None
            self.token_expiration = None

    async def _save_tokens_to_storage(self) -> None:
        """Save tokens to storage."""
        if (self.access_token and self.refresh_token and 
            self.token_expiration and 
            self.user_index is not None and 
            self.user_id is not None):
            
            tokens = {
                "user_index": self.user_index,
                "user_id": self.user_id,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "token_expiration": self.token_expiration,
            }
            
            try:
                await self.hass.async_add_executor_job(
                    json_util.save_json, self.storage_file, tokens
                )
                direct_log("Saved tokens to storage")
                _LOGGER.debug("Saved tokens to storage")
                    
            except Exception as ex:
                direct_log(f"Failed to save tokens to storage: {ex}")
                _LOGGER.error("Failed to save tokens to storage: %s", ex)
                    
        else:
            direct_log("Cannot save tokens to storage: Missing values")
            _LOGGER.warning("Cannot save tokens to storage: Missing values")
                
    def get_devices(self) -> List[Dict[str, Any]]:
        """Get the list of devices."""
        return self.devices
    
    def get_tokens(self) -> Dict[str, Any]:
        """Get the current tokens."""
        return {
            "user_index": self.user_index,
            "user_id": self.user_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_expiration": self.token_expiration,
        }