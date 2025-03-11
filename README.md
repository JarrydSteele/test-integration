# Olarm Integration for Home Assistant

This custom integration allows you to control and monitor your Olarm security system from Home Assistant, with both API and MQTT functionality for real-time updates.

## Features

- Display alarm status for each area (arm, disarm, stay, sleep, alarm)
- Control your alarm system (arm away, arm home, arm night, disarm)
- Show status of each zone (sensors) as binary sensors
- Control PGM outputs through switches
- Show power status (AC and battery)
- **Real-time updates via MQTT** (with email/password authentication)
- Reduced API calls to avoid rate limits

## Installation

### HACS (Recommended)

1. Make sure [HACS](https://hacs.xyz/) is installed in your Home Assistant instance
2. Click on HACS in the sidebar
3. Go to "Integrations"
4. Click the three dots in the top right corner and select "Custom repositories"
5. Add this repository URL and select "Integration" as the category
6. Click "Add"
7. Find and install the "Olarm" integration

### Manual Installation

1. Create a directory `config/custom_components/olarm` in your Home Assistant installation
2. Copy all files from this repository into that directory
3. Restart Home Assistant

## Configuration

You have two authentication options:

### Option 1: Email/Password Authentication (Recommended)

This method enables all features including MQTT for real-time updates.

1. Have your Olarm account email/phone and password ready
2. Go to Home Assistant → Configuration → Integrations
3. Click "+ Add Integration" and search for "Olarm"
4. Enter your email/phone and password when prompted

### Option 2: API Key Authentication (Legacy)

This method does not support MQTT real-time updates and only uses API polling.

1. Log in to your Olarm account
2. Navigate to your account settings
3. Generate or copy your API key
4. Enter this API key in the Home Assistant integration setup

### Option 3: Magic

Configure the integration [here](https://my.home-assistant.io/redirect/config_flow_start/?domain=olarm).

## How It Works

### MQTT Mode (Email/Password Auth)

When configured with email/password:

1. The integration authenticates with Olarm servers
2. It establishes WebSocket MQTT connections to Olarm's MQTT broker
3. It receives real-time updates for all device status changes
4. Commands are sent via MQTT for faster response times
5. API is used as fallback if MQTT is unavailable

### API Mode (API Key Auth)

When configured with API key only:

1. The integration polls the Olarm API periodically
2. All status updates and commands use the REST API
3. Updates may be delayed due to polling interval

## Usage

After configuration, your Olarm devices will appear in Home Assistant:

- **Alarm Control Panel**: Control your alarm system
  - Arm Away
  - Arm Home
  - Arm Night
  - Disarm

- **Binary Sensors**: Monitor zone status
  - Active/Inactive status
  - Bypass information

- **Switches**: Control PGM outputs
  - Turn on/off

## Troubleshooting

### Integration Not Appearing

Ensure all files are correctly placed in the `custom_components/olarm` directory and that Home Assistant has been restarted.

### Connection Issues

- Verify your credentials are correct
- Check if your Olarm system is online
- Ensure your Home Assistant instance has internet access
- For MQTT issues, check your firewall allows WebSocket connections on port 443

### Logs

To enable debug logs, add the following to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.olarm: debug
```

## Support

For issues, questions, or feature requests, please open an issue on GitHub.

## License

This project is licensed under the MIT License.
