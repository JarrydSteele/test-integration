"""Constants for the Olarm integration."""

DOMAIN = "olarm"
PLATFORMS = ["alarm_control_panel", "binary_sensor", "switch"]

CONF_API_KEY = "api_key"
CONF_USER_EMAIL_PHONE = "user_email_phone"
CONF_USER_PASS = "user_pass"
DEFAULT_SCAN_INTERVAL = 30

# Alarm States
STATE_DISARMED = "disarm"
STATE_ARMED_AWAY = "arm"
STATE_ARMED_HOME = "stay"
STATE_ARMED_NIGHT = "sleep"
STATE_TRIGGERED = "alarm"
STATE_PENDING = "countdown"

# Commands
CMD_DISARM = "area-disarm"
CMD_ARM_AWAY = "area-arm"
CMD_ARM_HOME = "area-stay"
CMD_ARM_NIGHT = "area-sleep"

# MQTT Commands (for direct MQTT control)
MQTT_CMD_DISARM = "disarm"
MQTT_CMD_ARM_AWAY = "arm"
MQTT_CMD_ARM_HOME = "stay" 
MQTT_CMD_ARM_NIGHT = "sleep"

# Zone States
ZONE_ACTIVE = "a"
ZONE_CLOSED = "c" 
ZONE_BYPASSED = "b"

# PGM Commands
CMD_PGM_OPEN = "pgm-open"
CMD_PGM_CLOSE = "pgm-close"
CMD_PGM_PULSE = "pgm-pulse"

# Power States
ATTR_AC_POWER = "ac_power"
ATTR_BATTERY = "battery"

# MQTT Settings
MQTT_HOST = "mqtt-ws.olarm.com"
MQTT_PORT = 443
MQTT_USERNAME = "native_app"
MQTT_PROTOCOL = "wss"

# Signal constants
SIGNAL_OLARM_MQTT_UPDATE = f"{DOMAIN}_mqtt_update"
SIGNAL_OLARM_API_UPDATE = f"{DOMAIN}_api_update"
