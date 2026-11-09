"""Passive BLE advertisement scan with Bleak.

The scan never connects to a device: it only records advertisements, so it
cannot alter Bluetooth pairing or the speakers' configuration.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .constants import BLE_COMPANY_ID, BLE_NAME_HINTS, BLE_PRODUCT_TYPES, BLE_SERVICE_UUIDS


@dataclass
class DevialetAdvertisement:
    product_type: str
    serial: str | None
    firmware: str | None


def parse_manufacturer_data(data: bytes) -> DevialetAdvertisement:
    """Decode company-0x0258 manufacturer data like BleSetupScanner does.

    byte 0: product type; bytes 1..13: serial (ASCII);
    bytes 14..19: firmware u8.u8.u16be.u16be.
    """
    product = BLE_PRODUCT_TYPES.get(data[0], f"unknown(0x{data[0]:02x})") if data else "empty"
    serial = data[1:14].decode("ascii", "replace") if len(data) >= 14 else None
    firmware = None
    if len(data) >= 20:
        fw = data[14:20]
        firmware = f"{fw[0]}.{fw[1]}.{int.from_bytes(fw[2:4], 'big')}.{int.from_bytes(fw[4:6], 'big')}"
    return DevialetAdvertisement(product, serial, firmware)


@dataclass
class BleDevice:
    address: str
    name: str | None
    rssi: int | None
    service_uuids: list[str] = field(default_factory=list)
    manufacturer_data: dict[int, str] = field(default_factory=dict)
    service_data: dict[str, str] = field(default_factory=dict)

    @property
    def advertises_devialet_service(self) -> bool:
        return any(u.lower() in BLE_SERVICE_UUIDS for u in self.service_uuids)

    @property
    def devialet_advertisement(self) -> DevialetAdvertisement | None:
        raw = self.manufacturer_data.get(BLE_COMPANY_ID)
        return parse_manufacturer_data(bytes.fromhex(raw)) if raw is not None else None

    @property
    def name_matches(self) -> bool:
        return bool(self.name) and any(h in self.name.lower() for h in BLE_NAME_HINTS)

    @property
    def is_candidate(self) -> bool:
        return (self.advertises_devialet_service or self.name_matches
                or BLE_COMPANY_ID in self.manufacturer_data)


async def scan(timeout: float = 10.0) -> list[BleDevice]:
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = []
    for address, (device, adv) in found.items():
        devices.append(BleDevice(
            address=address,
            name=adv.local_name or device.name,
            rssi=adv.rssi,
            service_uuids=[u.lower() for u in adv.service_uuids],
            manufacturer_data={k: v.hex() for k, v in adv.manufacturer_data.items()},
            service_data={k: v.hex() for k, v in adv.service_data.items()},
        ))
    return sorted(devices, key=lambda d: (not d.is_candidate, -(d.rssi or -999)))
