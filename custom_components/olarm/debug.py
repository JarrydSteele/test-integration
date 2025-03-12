"""Debug utilities for Olarm integration."""
import logging
import sys
from datetime import datetime
import traceback

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

def direct_log(message: str, level="info"):
    """Log directly to console, bypassing Home Assistant's log filtering."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    # Add level indicator
    prefix = "ℹ️"
    if level.lower() == "error":
        prefix = "❌"
    elif level.lower() == "warning":
        prefix = "⚠️"
    elif level.lower() == "debug":
        prefix = "🔍"
        
    print(f"\n{timestamp} {prefix} OLARM DIRECT: {message}\n", flush=True)
    
    # Also log through the logger
    if level.lower() == "error":
        olarm_direct_logger.error(f"{prefix} {message}")
    elif level.lower() == "warning":
        olarm_direct_logger.warning(f"{prefix} {message}")
    else:
        olarm_direct_logger.warning(f"{prefix} {message}")  # Default to warning to ensure visibility

def mqtt_log(message: str, level="info"):
    """Log MQTT messages directly to console."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    # Add level indicator
    prefix = "ℹ️"
    if level.lower() == "error":
        prefix = "❌"
    elif level.lower() == "warning":
        prefix = "⚠️"
    elif level.lower() == "debug":
        prefix = "🔍"
    
    print(f"\n{timestamp} 🔵 MQTT DIRECT: {prefix} {message}\n", flush=True)
    olarm_direct_logger.warning(f"🔵 MQTT: {prefix} {message}")

def log_exception(ex, context=""):
    """Log exception with traceback directly to console."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    
    if context:
        context_str = f" [{context}]"
    else:
        context_str = ""
        
    error_message = f"Exception{context_str}: {str(ex)}"
    traceback_str = "".join(traceback.format_tb(ex.__traceback__))
    
    print(f"\n{timestamp} 🔥 EXCEPTION: {error_message}\n{traceback_str}\n", flush=True)
    olarm_direct_logger.error(f"🔥 EXCEPTION{context_str}: {str(ex)}\n{traceback_str}")