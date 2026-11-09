"""Network constants of the Phantom II local interfaces (interoperability).

Ports, service names and UUIDs the speakers use on the local network, as
observed on DOS firmware 2.19.1; they may change with other firmware versions.
"""

# mDNS service types announced by the speakers.
# _whatsup._tcp : legacy protocol stack
# _http._tcp / _devialet-http._tcp : IP Control (HTTP) announcements
MDNS_WHATSUP = "_whatsup._tcp.local."
MDNS_HTTP = "_http._tcp.local."
MDNS_DEVIALET_HTTP = "_devialet-http._tcp.local."
MDNS_SERVICE_TYPES = (MDNS_WHATSUP, MDNS_HTTP, MDNS_DEVIALET_HTTP)

# IP Control (HTTP): port 80, base path "/ipcontrol/v1/"
IPCONTROL_DEFAULT_PORT = 80
IPCONTROL_BASE_PATH = "/ipcontrol/v1"
IPCONTROL_REQUIRED_VERSION = "1"
# Lowest DOS firmware version expected to expose IP Control
IPCONTROL_MIN_DOS_VERSION = (2, 18, 0, 0)

# UDP discovery.
# V1: announcements received on UDP 24243; packets start with "_DVLT_".
# V2: announcements on UDP 24244, answered to a solicitation
#     "_DVLT_" + 02 00 01 00 00 broadcast to 255.255.255.255:24244 every 5 s.
UDP_PREFIX = b"_DVLT_"
UDP_V1_PORT = 24243
UDP_V2_PORT = 24244
UDP_V2_SOLICITATION = UDP_PREFIX + bytes([0x02, 0x00, 0x01, 0x00, 0x00])
LEGACY_BROADCAST_PORT = UDP_V1_PORT

# BLE (setup only: no control path is known over BLE).
# Manufacturer data company id 600 (0x0258)
BLE_COMPANY_ID = 0x0258
# Product family: byte 0 of the manufacturer data
BLE_PRODUCT_TYPES = {0x00: "LegacyDOS1", 0x10: "LegacyDOS2", 0x11: "SDOS", 0x12: "DOS"}
# Wi-Fi setup GATT service (IP Control generation)
BLE_IPC_SETUP_SERVICE_UUID = "eb33e83b-ab67-41b0-b583-68027fca43b9"
# Legacy setup GATT service and characteristic
BLE_SERVICE_UUID = "de71a7e7-70d0-70d0-70d0-b7e5e1371cee"
BLE_CHARACTERISTIC_UUID = "de71a7e7-70d0-70d0-70d0-c11a13ac7e13"
BLE_SERVICE_UUIDS = (BLE_IPC_SETUP_SERVICE_UUID, BLE_SERVICE_UUID)
BLE_NAME_HINTS = ("devialet", "phantom")

# Read-only HTTP endpoints probed when --http-probe is given (GET only).
IPCONTROL_READONLY_PATHS = (
    "/devices/current",
    "/systems/current",
    "/groups/current/sources",
    "/groups/current/sources/current",
    "/systems/current/sources/current/soundControl/volume",
)
