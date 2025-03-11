"""Debug utilities for Olarm integration."""
import logging
import sys
from datetime import datetime

# Configure a direct console logger that can't be filtered
console = logging.StreamHandler(stream=sys.stdout)
console.setLevel(logging.WARNING)
formatter = logging.Formatter('%(asctime)s [OLARM_DIRECT] %(message)s')
console.setFormatter(formatter)

# Create a special logger that always outputs to console
olarm_direct_logger = logging.getLogger("olarm_direct")
olarm_direct_logger.setLevel(logging.WARNING)
olarm_direct_logger.addHandler(console)
olarm_direct_logger.propagate = False  # Don't pass to parent

def mqtt_log(message: str):
    """Log MQTT messages directly to console."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"\n{timestamp} 🔵 MQTT DIRECT: {message}\n", flush=True)
    olarm_direct_logger.warning(f"🔵 MQTT: {message}")