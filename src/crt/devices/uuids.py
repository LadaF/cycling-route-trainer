"""Bluetooth SIG assigned UUIDs used by the application."""


def uuid16(short: int) -> str:
    """Expand a 16-bit SIG-assigned UUID to the full 128-bit string form used by bleak."""
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


# Services
FTMS_SERVICE = uuid16(0x1826)
CYCLING_POWER_SERVICE = uuid16(0x1818)
HEART_RATE_SERVICE = uuid16(0x180D)
DEVICE_INFORMATION_SERVICE = uuid16(0x180A)
BATTERY_SERVICE = uuid16(0x180F)

# Fitness Machine Service characteristics
FTMS_FEATURE = uuid16(0x2ACC)
INDOOR_BIKE_DATA = uuid16(0x2AD2)
TRAINING_STATUS = uuid16(0x2AD3)
SUPPORTED_INCLINATION_RANGE = uuid16(0x2AD5)
SUPPORTED_RESISTANCE_LEVEL_RANGE = uuid16(0x2AD6)
SUPPORTED_POWER_RANGE = uuid16(0x2AD8)
FTMS_CONTROL_POINT = uuid16(0x2AD9)
FTMS_STATUS = uuid16(0x2ADA)

# Heart Rate Service characteristics
HEART_RATE_MEASUREMENT = uuid16(0x2A37)

# Device Information Service characteristics, with display names
DEVICE_INFORMATION = {
    uuid16(0x2A29): "manufacturer",
    uuid16(0x2A24): "model",
    uuid16(0x2A25): "serial_number",
    uuid16(0x2A27): "hardware_revision",
    uuid16(0x2A26): "firmware_revision",
    uuid16(0x2A28): "software_revision",
}
BATTERY_LEVEL = uuid16(0x2A19)

# Services the scanner reports by name
KNOWN_SERVICES = {
    FTMS_SERVICE: "FTMS",
    CYCLING_POWER_SERVICE: "CPS",
    HEART_RATE_SERVICE: "HRS",
}
