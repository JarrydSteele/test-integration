"""Config flow for Olarm integration."""
import logging
import asyncio
from typing import Any, Dict, Optional

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# Make sure all imports are correct and available
try:
    from .api import OlarmApiClient, OlarmApiError
except ImportError:
    # Handle fallback for import errors
    from api import OlarmApiClient, OlarmApiError

from .const import (
    DOMAIN, 
    CONF_USER_EMAIL_PHONE, 
    CONF_USER_PASS, 
    CONF_API_KEY,
    CONF_MQTT_ONLY,
    CONF_DEBUG_MQTT
)

_LOGGER = logging.getLogger(__name__)

async def validate_auth(hass: HomeAssistant, user_email_phone: str, user_pass: str) -> bool:
    """Validate the user credentials."""
    from .auth import OlarmAuth
    
    session = async_get_clientsession(hass)
    auth = OlarmAuth(hass, user_email_phone, user_pass, session)
    
    try:
        return await auth.initialize()
    except Exception as e:
        _LOGGER.error(f"Auth validation error: {e}")
        return False

class OlarmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Olarm."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Support both authentication methods (API key for backward compatibility)
            if CONF_API_KEY in user_input and user_input[CONF_API_KEY]:
                api_key = user_input[CONF_API_KEY]
                # Create the config entry
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, "Olarm"),
                    data={CONF_API_KEY: api_key},
                )
            elif CONF_USER_EMAIL_PHONE in user_input and CONF_USER_PASS in user_input:
                user_email_phone = user_input[CONF_USER_EMAIL_PHONE]
                user_pass = user_input[CONF_USER_PASS]
                mqtt_only = user_input.get(CONF_MQTT_ONLY, False)
                debug_mqtt = user_input.get(CONF_DEBUG_MQTT, False)
                
                # Validate credentials
                if await validate_auth(self.hass, user_email_phone, user_pass):
                    # Create the config entry
                    return self.async_create_entry(
                        title=user_input.get(CONF_NAME, "Olarm"),
                        data={
                            CONF_USER_EMAIL_PHONE: user_email_phone,
                            CONF_USER_PASS: user_pass,
                            CONF_MQTT_ONLY: mqtt_only,
                            CONF_DEBUG_MQTT: debug_mqtt,
                        },
                    )
                else:
                    errors["base"] = "invalid_auth"
            else:
                errors["base"] = "missing_credentials"

        # Show the form
        data_schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY): str,
                vol.Optional(CONF_USER_EMAIL_PHONE): str,
                vol.Optional(CONF_USER_PASS): str,
                vol.Optional(CONF_MQTT_ONLY, default=False): bool,
                vol.Optional(CONF_DEBUG_MQTT, default=False): bool,
                vol.Optional(CONF_NAME, default="Olarm"): str,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )