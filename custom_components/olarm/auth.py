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

_LOGGER = logging.getLogger(__name__)

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
        
        _LOGGER.info("Auth initialized for user: %s", user_email_phone)

    async def initialize(self) -> bool:
        """Initialize authentication."""
        _LOGGER.info("Starting authentication initialization")
        
        # Load tokens from storage
        await self._load_tokens_from_storage()
        
        if not self.access_token or not self.refresh_token:
            # No valid tokens, login
            _LOGGER.info("No valid tokens found, performing login")
            return await self.login()
        else:
            # Ensure access token is valid
            _LOGGER.info("Tokens found, ensuring they are valid")
            return await self.ensure_access_token()

    async def login(self) -> bool:
        """Log in to Olarm and obtain tokens."""
        _LOGGER.info("Attempting login for: %s", self.user_email_phone)
        
        try:
            # Using the context manager for the timeout
            async with async_timeout.timeout(30):
                response = await self.session.post(
                    "https://auth.olarm.com/api/v4/oauth/login/mobile",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "userEmailPhone": self.user_email_phone,
                        "userPass": self.user_pass,
                    },
                )
                
                _LOGGER.debug("Login response status: %s", response.status)
                    
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("Login failed: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.access_token = data.get("oat")
                self.refresh_token = data.get("ort")
                self.token_expiration = data.get("oatExpire")
                
                _LOGGER.info("Login successful! Access token: %s...", 
                           self.access_token[:10] if self.access_token else "None")
                
                # Fetch user index
                fetch_success = await self._fetch_user_index()
                if not fetch_success:
                    _LOGGER.error("Failed to fetch user index")
                    return False
                
                # Save tokens
                await self._save_tokens_to_storage()
                
                # Fetch devices
                devices_success = await self._fetch_devices()
                if not devices_success:
                    _LOGGER.error("Failed to fetch devices")
                    return False
                
                return True
                
        except asyncio.TimeoutError:
            _LOGGER.error("Login timed out")
            return False
        except Exception as error:
            _LOGGER.error("Login error: %s", error)
            return False

    async def _fetch_user_index(self) -> bool:
        """Fetch user index from Olarm API."""
        if not self.access_token:
            _LOGGER.error("Cannot fetch user index: No access token")
            return False
        
        _LOGGER.debug("Fetching user index")
        
        try:
            async with async_timeout.timeout(30):
                url = f"https://auth.olarm.com/api/v4/oauth/federated-link-existing?oat={self.access_token}"
                response = await self.session.post(
                    url,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "userEmailPhone": self.user_email_phone,
                        "userPass": self.user_pass,
                        "captchaToken": "olarmapp",
                    },
                )
                
                _LOGGER.debug("User index response status: %s", response.status)
                
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("Failed to fetch user index: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.user_index = data.get("userIndex")
                self.user_id = data.get("userId")
                
                _LOGGER.info("User index: %s, User ID: %s", self.user_index, self.user_id)
                    
                return True
                
        except asyncio.TimeoutError:
            _LOGGER.error("Fetch user index timed out")
            return False
        except Exception as error:
            _LOGGER.error("Error fetching user index: %s", error)
            return False

    async def _fetch_devices(self) -> bool:
        """Fetch devices from Olarm API."""
        if not self.access_token or self.user_index is None:
            _LOGGER.error("Cannot fetch devices: Missing access token or user index")
            return False
        
        _LOGGER.debug("Fetching devices for user index: %s", self.user_index)
        
        try:
            async with async_timeout.timeout(30):
                url = f"https://api-legacy.olarm.com/api/v2/users/{self.user_index}"
                response = await self.session.get(
                    url,
                    headers={"Authorization": f"Bearer {self.access_token}"}
                )
                
                _LOGGER.debug("Devices response status: %s", response.status)
                
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("Failed to fetch devices: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.devices = [{
                    "id": device.get("id"),
                    "imei": device.get("IMEI"),
                    "name": device.get("name", "Olarm Device"),
                } for device in data.get("devices", [])]
                
                _LOGGER.info("Found %d devices", len(self.devices))
                for device in self.devices:
                    _LOGGER.debug("Device: %s, IMEI: %s", 
                                device.get('name'), device.get('imei'))
                        
                return True
                
        except asyncio.TimeoutError:
            _LOGGER.error("Fetch devices timed out")
            return False
        except Exception as error:
            _LOGGER.error("Error fetching devices: %s", error)
            return False

    async def refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            _LOGGER.error("Cannot refresh token: No refresh token available")
            return await self.login()
        
        _LOGGER.debug("Refreshing access token")
        
        try:
            async with async_timeout.timeout(30):
                response = await self.session.post(
                    "https://auth.olarm.com/api/v4/oauth/refresh",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={"ort": self.refresh_token},
                )
                
                _LOGGER.debug("Token refresh response status: %s", response.status)
                
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("Token refresh failed: %s %s", response.status, response_text)
                    return await self.login()
                
                data = await response.json()
                self.access_token = data.get("oat")
                self.refresh_token = data.get("ort")
                self.token_expiration = data.get("oatExpire")
                
                _LOGGER.info("Token refresh successful")
                
                # If user_index or user_id is missing, fetch it
                if self.user_index is None or self.user_id is None:
                    await self._fetch_user_index()
                
                # Save updated tokens
                await self._save_tokens_to_storage()
                
                return True
                
        except asyncio.TimeoutError:
            _LOGGER.error("Token refresh timed out")
            return False
        except Exception as error:
            _LOGGER.error("Error refreshing token: %s", error)
            return False

    async def ensure_access_token(self) -> bool:
        """Ensure the access token is valid, refresh if needed."""
        if not self.access_token or not self.token_expiration:
            _LOGGER.debug("No token/expiration available, performing login")
            return await self.login()
        
        # Check if token is expired or about to expire (within 60 seconds)
        current_time = int(time.time() * 1000)  # Convert to milliseconds
        if current_time >= (self.token_expiration - 60000):
            _LOGGER.debug("Token expired or about to expire, refreshing")
            return await self.refresh_access_token()
        
        # Token is valid, fetch devices if we don't have them
        if not self.devices:
            _LOGGER.debug("Token valid but no devices, fetching devices")
            return await self._fetch_devices()
        
        _LOGGER.debug("Token valid, all data available")
            
        return True

    async def _load_tokens_from_storage(self) -> None:
        """Load tokens from storage."""
        try:
            if not os.path.exists(self.storage_file):
                _LOGGER.debug("No token storage file exists")
                return
            
            tokens = await self.hass.async_add_executor_job(json_util.load_json, self.storage_file)
            if tokens:
                self.user_index = tokens.get("user_index")
                self.user_id = tokens.get("user_id")
                self.access_token = tokens.get("access_token")
                self.refresh_token = tokens.get("refresh_token")
                self.token_expiration = tokens.get("token_expiration")
                _LOGGER.debug("Loaded tokens from storage")
                    
        except Exception as ex:
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
                _LOGGER.debug("Saved tokens to storage")
                    
            except Exception as ex:
                _LOGGER.error("Failed to save tokens to storage: %s", ex)
                    
        else:
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