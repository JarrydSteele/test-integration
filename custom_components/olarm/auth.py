"""Authentication handler for Olarm integration."""
import os
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import aiohttp
import async_timeout
from aiohttp import ClientSession

from homeassistant.core import HomeAssistant
from homeassistant.util import json as json_util

from .const import DOMAIN

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
        
        # Debug log
        with open("/config/olarm_auth.log", "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{timestamp} - Auth initialized for user: {user_email_phone}\n")

    async def initialize(self) -> bool:
        """Initialize authentication."""
        with open("/config/olarm_auth.log", "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{timestamp} - Starting authentication initialization\n")
            
        # Load tokens from storage
        await self._load_tokens_from_storage()
        
        if not self.access_token or not self.refresh_token:
            # No valid tokens, login
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - No valid tokens found, performing login\n")
            return await self.login()
        else:
            # Ensure access token is valid
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Tokens found, ensuring they are valid\n")
            return await self.ensure_access_token()

    async def login(self) -> bool:
        """Log in to Olarm and obtain tokens."""
        with open("/config/olarm_auth.log", "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{timestamp} - Attempting login for: {self.user_email_phone}\n")
            
        try:
            # Using a context manager for the timeout which is better for Python 3.13
            timeout_ctx = async_timeout.timeout(30)
            async with timeout_ctx:
                response = await self.session.post(
                    "https://auth.olarm.com/api/v4/oauth/login/mobile",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "userEmailPhone": self.user_email_phone,
                        "userPass": self.user_pass,
                    },
                )
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Login response status: {response.status}\n")
                    
                if response.status != 200:
                    response_text = await response.text()
                    with open("/config/olarm_auth.log", "a") as f:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        f.write(f"{timestamp} - Login failed: {response.status} {response_text}\n")
                    return False
                
                data = await response.json()
                self.access_token = data.get("oat")
                self.refresh_token = data.get("ort")
                self.token_expiration = data.get("oatExpire")
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Login successful! Access token received\n")
                
                # Fetch user index
                fetch_success = await self._fetch_user_index()
                if not fetch_success:
                    with open("/config/olarm_auth.log", "a") as f:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        f.write(f"{timestamp} - Failed to fetch user index\n")
                    return False
                
                # Save tokens
                await self._save_tokens_to_storage()
                
                # Fetch devices
                devices_success = await self._fetch_devices()
                if not devices_success:
                    with open("/config/olarm_auth.log", "a") as f:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        f.write(f"{timestamp} - Failed to fetch devices\n")
                    return False
                
                return True
                
        except asyncio.TimeoutError:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Login timed out\n")
            return False
        except Exception as error:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Login error: {error}\n")
            return False

    async def _fetch_user_index(self) -> bool:
        """Fetch user index from Olarm API."""
        if not self.access_token:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Cannot fetch user index: No access token\n")
            return False
        
        with open("/config/olarm_auth.log", "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{timestamp} - Fetching user index\n")
            
        try:
            timeout_ctx = async_timeout.timeout(30)
            async with timeout_ctx:
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
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - User index response status: {response.status}\n")
                
                if response.status != 200:
                    response_text = await response.text()
                    with open("/config/olarm_auth.log", "a") as f:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        f.write(f"{timestamp} - Failed to fetch user index: {response.status} {response_text}\n")
                    return False
                
                data = await response.json()
                self.user_index = data.get("userIndex")
                self.user_id = data.get("userId")
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - User index: {self.user_index}, User ID: {self.user_id}\n")
                    
                return True
                
        except asyncio.TimeoutError:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Fetch user index timed out\n")
            return False
        except Exception as error:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Error fetching user index: {error}\n")
            return False

    async def _fetch_devices(self) -> bool:
        """Fetch devices from Olarm API."""
        if not self.access_token or self.user_index is None:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Cannot fetch devices: Missing access token or user index\n")
            return False
        
        with open("/config/olarm_auth.log", "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{timestamp} - Fetching devices for user index: {self.user_index}\n")
            
        try:
            timeout_ctx = async_timeout.timeout(30)
            async with timeout_ctx:
                url = f"https://api-legacy.olarm.com/api/v2/users/{self.user_index}"
                response = await self.session.get(
                    url,
                    headers={"Authorization": f"Bearer {self.access_token}"}
                )
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Devices response status: {response.status}\n")
                
                if response.status != 200:
                    response_text = await response.text()
                    with open("/config/olarm_auth.log", "a") as f:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        f.write(f"{timestamp} - Failed to fetch devices: {response.status} {response_text}\n")
                    return False
                
                data = await response.json()
                self.devices = [{
                    "id": device.get("id"),
                    "imei": device.get("IMEI"),
                    "name": device.get("name", "Olarm Device"),
                } for device in data.get("devices", [])]
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Found {len(self.devices)} devices\n")
                    for device in self.devices:
                        f.write(f"{timestamp} - Device: {device.get('name')}, IMEI: {device.get('imei')}\n")
                        
                return True
                
        except asyncio.TimeoutError:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Fetch devices timed out\n")
            return False
        except Exception as error:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Error fetching devices: {error}\n")
            return False

    async def refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Cannot refresh token: No refresh token available\n")
            return await self.login()
        
        with open("/config/olarm_auth.log", "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{timestamp} - Refreshing access token\n")
            
        try:
            timeout_ctx = async_timeout.timeout(30)
            async with timeout_ctx:
                response = await self.session.post(
                    "https://auth.olarm.com/api/v4/oauth/refresh",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={"ort": self.refresh_token},
                )
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Token refresh response status: {response.status}\n")
                
                if response.status != 200:
                    response_text = await response.text()
                    with open("/config/olarm_auth.log", "a") as f:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        f.write(f"{timestamp} - Token refresh failed: {response.status} {response_text}\n")
                    return await self.login()
                
                data = await response.json()
                self.access_token = data.get("oat")
                self.refresh_token = data.get("ort")
                self.token_expiration = data.get("oatExpire")
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Token refresh successful\n")
                
                # If user_index or user_id is missing, fetch it
                if self.user_index is None or self.user_id is None:
                    await self._fetch_user_index()
                
                # Save updated tokens
                await self._save_tokens_to_storage()
                
                return True
                
        except asyncio.TimeoutError:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Token refresh timed out\n")
            return False
        except Exception as error:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Error refreshing token: {error}\n")
            return False

    async def ensure_access_token(self) -> bool:
        """Ensure the access token is valid, refresh if needed."""
        if not self.access_token or not self.token_expiration:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - No token/expiration available, performing login\n")
            return await self.login()
        
        # Check if token is expired or about to expire (within 60 seconds)
        current_time = int(time.time() * 1000)  # Convert to milliseconds
        if current_time >= (self.token_expiration - 60000):
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Token expired or about to expire, refreshing\n")
            return await self.refresh_access_token()
        
        # Token is valid, fetch devices if we don't have them
        if not self.devices:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Token valid but no devices, fetching devices\n")
            return await self._fetch_devices()
        
        with open("/config/olarm_auth.log", "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{timestamp} - Token valid, all data available\n")
            
        return True

    async def _load_tokens_from_storage(self) -> None:
        """Load tokens from storage."""
        try:
            if not os.path.exists(self.storage_file):
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - No token storage file exists\n")
                return
            
            tokens = await self.hass.async_add_executor_job(json_util.load_json, self.storage_file)
            if tokens:
                self.user_index = tokens.get("user_index")
                self.user_id = tokens.get("user_id")
                self.access_token = tokens.get("access_token")
                self.refresh_token = tokens.get("refresh_token")
                self.token_expiration = tokens.get("token_expiration")
                
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Loaded tokens from storage\n")
                    
        except Exception as ex:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Failed to load tokens from storage: {ex}\n")
                
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
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Saved tokens to storage\n")
                    
            except Exception as ex:
                with open("/config/olarm_auth.log", "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{timestamp} - Failed to save tokens to storage: {ex}\n")
                    
        else:
            with open("/config/olarm_auth.log", "a") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{timestamp} - Cannot save tokens to storage: Missing values\n")
                
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