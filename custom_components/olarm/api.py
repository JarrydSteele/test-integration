"""Olarm API Client for Home Assistant."""
import logging
import aiohttp
import async_timeout
from typing import Dict, List, Optional, Any, Union

_LOGGER = logging.getLogger(__name__)

class OlarmApiClient:
    """Olarm API client."""

    def __init__(self, api_key: str, session: aiohttp.ClientSession):
        """Initialize the API client."""
        self.api_key = api_key
        self.session = session
        self.base_url = "https://apiv4.olarm.co/api/v4"
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def get_devices(self, search: str = None, page: int = 1, page_length: int = 50) -> Dict[str, Any]:
        """Get all devices."""
        params = {"page": page, "pageLength": page_length}
        if search:
            params["search"] = search

        return await self._request("get", "/devices", params=params)

    async def get_device(self, device_id: str) -> Dict[str, Any]:
        """Get a specific device."""
        return await self._request("get", f"/devices/{device_id}")

    async def send_device_action(self, device_id: str, action_cmd: str, action_num: int) -> Dict[str, Any]:
        """Send an action to a device."""
        data = {
            "actionCmd": action_cmd,
            "actionNum": action_num
        }
        return await self._request("post", f"/devices/{device_id}/actions", json=data)

    async def get_device_actions(self, device_id: str) -> List[Dict[str, Any]]:
        """Get device actions."""
        return await self._request("get", f"/devices/{device_id}/actions")

    async def get_device_events(self, device_id: str, limit: int = 20, after: str = None) -> Dict[str, Any]:
        """Get device events."""
        params = {"limit": limit}
        if after:
            params["after"] = after

        return await self._request("get", f"/devices/{device_id}/events", params=params)

    async def send_prolink_action(self, prolink_id: str, action_cmd: str, action_num: int) -> Dict[str, Any]:
        """Send an action to an Olarm LINK."""
        data = {
            "actionCmd": action_cmd,
            "actionNum": action_num
        }
        return await self._request("post", f"/prolinks/{prolink_id}/actions", json=data)

    async def get_prolink_actions(self, prolink_id: str) -> List[Dict[str, Any]]:
        """Get Olarm LINK actions."""
        return await self._request("get", f"/prolinks/{prolink_id}/actions")

    async def _request(
        self, method: str, endpoint: str, params: Dict = None, json: Dict = None
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """Make a request to the Olarm API."""
        url = f"{self.base_url}{endpoint}"
        
        try:
            async with async_timeout.timeout(30):
                if method == "get":
                    response = await self.session.get(url, headers=self.headers, params=params)
                elif method == "post":
                    response = await self.session.post(url, headers=self.headers, params=params, json=json)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    _LOGGER.error(
                        "Error requesting Olarm API %s, status: %s, response: %s",
                        url,
                        response.status,
                        error_text,
                    )
                    raise OlarmApiError(f"Error {response.status}: {error_text}")
        except aiohttp.ClientError as error:
            _LOGGER.error("Error requesting Olarm API %s: %s", url, error)
            raise OlarmApiError(f"Connection error: {error}")
        except async_timeout.timeout:
            _LOGGER.error("Timeout requesting Olarm API %s", url)
            raise OlarmApiError("Connection timeout")

class OlarmApiError(Exception):
    """Raised when Olarm API request ends in error."""
    pass
