"""Authentication handler for Olarm integration."""
import os
import logging
import json
import sys
import time
from typing import Dict, List, Optional, Any, Tuple

import aiohttp
import async_timeout
from aiohttp import ClientSession

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import json as json_util

from .const import DOMAIN, CONF_USER_EMAIL_PHONE, CONF_USER_PASS

def auth_log(message):
    """Direct log for auth debugging."""
    with open("/config/olarm_auth.log", "a") as f:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"{timestamp} - {message}\n")
    print(f"\n🔑 OLARM AUTH: {message}\n", flush=True)

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

    async def initialize(self) -> bool:
        """Initialize authentication."""

        auth_log(f"Initializing auth for user: {self.user_email_phone}")

        # Load tokens from storage
        await self._load_tokens_from_storage()
        
        if not self.access_token or not self.refresh_token:
            # No valid tokens, login
            _LOGGER.debug("No valid tokens found, performing login")
            return await self.login()
        else:
            # Ensure access token is valid
            _LOGGER.debug("Tokens found, ensuring they are valid")
            return await self.ensure_access_token()

    async def login(self) -> bool:
        """Log in to Olarm and obtain tokens."""

        auth_log(f"Attempting login for: {self.user_email_phone}")

        _LOGGER.info("Logging in to Olarm")
        try:
            async with async_timeout.timeout(30):
                response = await self.session.post(
                    "https://auth.olarm.com/api/v4/oauth/login/mobile",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "userEmailPhone": self.user_email_phone,
                        "userPass": self.user_pass,
                    },
                )
                
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("Login failed: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.access_token = data.get("oat")
                self.refresh_token = data.get("ort")
                self.token_expiration = data.get("oatExpire")
                
                auth_log(f"Login successful! Access token: {self.access_token[:10]}...")
                auth_log(f"Token expiration: {self.token_expiration}")

                # Fetch user index
                await self._fetch_user_index()
                
                # Save tokens
                await self._save_tokens_to_storage()
                
                # Fetch devices
                await self._fetch_devices()
                
                return True
                
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            _LOGGER.error("Error during login: %s", error)
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

                auth_log(f"User index response: {response.status}")
                
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("Failed to fetch user index: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.user_index = data.get("userIndex")
                self.user_id = data.get("userId")

                auth_log(f"User index: {self.user_index}, User ID: {self.user_id}")
                
                _LOGGER.debug("User index: %s, User ID: %s", self.user_index, self.user_id)
                return True
                
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            _LOGGER.error("Error fetching user index: %s", error)
            return False

    async def _fetch_devices(self) -> bool:
        """Fetch devices from Olarm API."""

        auth_log(f"Fetching devices for user index: {self.user_index}")

        if not self.access_token or self.user_index is None:
            _LOGGER.error("Cannot fetch devices: Missing access token or user index")
            return False
        
        _LOGGER.debug("Fetching devices for user index %s", self.user_index)
        try:
            async with async_timeout.timeout(30):
                url = f"https://api-legacy.olarm.com/api/v2/users/{self.user_index}"
                response = await self.session.get(
                    url,
                    headers={"Authorization": f"Bearer {self.access_token}"}
                )
                
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("Failed to fetch devices: %s %s", response.status, response_text)
                    return False
                
                data = await response.json()
                self.devices = [{
                    "id": device.get("id"),
                    "imei": device.get("IMEI"),
                    "name": device.get("name", "Olarm Device"),
                    # Add other relevant device properties
                } for device in data.get("devices", [])]

                auth_log(f"Found {len(self.devices)} devices")
                for device in self.devices:
                    auth_log(f"Device: {device.get('name', 'Unknown')}, IMEI: {device.get('imei', 'Unknown')}")
                
                _LOGGER.debug("Fetched %s devices", len(self.devices))
                return True
                
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
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
                
                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.error("Token refresh failed: %s %s", response.status, response_text)
                    return await self.login()
                
                data = await response.json()
                self.access_token = data.get("oat")
                self.refresh_token = data.get("ort")
                self.token_expiration = data.get("oatExpire")
                
                # If user_index or user_id is missing, fetch it
                if self.user_index is None or self.user_id is None:
                    await self._fetch_user_index()
                
                # Save updated tokens
                await self._save_tokens_to_storage()
                
                return True
                
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            _LOGGER.error("Error refreshing token: %s", error)
            return False

    async def ensure_access_token(self) -> bool:
        """Ensure the access token is valid, refresh if needed."""
        if not self.access_token or not self.token_expiration:
            return await self.login()
        
        # Check if token is expired or about to expire (within 60 seconds)
        current_time = int(time.time() * 1000)  # Convert to milliseconds
        if current_time >= (self.token_expiration - 60000):
            _LOGGER.debug("Access token expired or about to expire, refreshing")
            return await self.refresh_access_token()
        
        # Token is valid, fetch devices if we don't have them
        if not self.devices:
            return await self._fetch_devices()
        
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
