import json

from devialet_diag.ble import BleDevice
from devialet_diag.constants import BLE_SERVICE_UUID
from devialet_diag.mdns import MdnsService
from devialet_diag.network import HttpResult, PortResult, UdpListenResult, UdpPacket
from devialet_diag.report import DiagnosticRun, group_by_host, render_markdown

LEFT_TXT = {"manufacturer": "Devialet", "ipControlVersion": "1", "firmwareFamily": "DOS",
            "firmwareVersion": "2.19.0.1", "serialNumber": "L123", "path": "/ipcontrol/v1"}


def make_services():
    return [
        MdnsService("_http._tcp.local.", "Phantom L", "left.local.", 80,
                    ["192.168.1.10", "fe80::1"], LEFT_TXT),
        MdnsService("_whatsup._tcp.local.", "wu-left", "left.local.", 41000,
                    ["192.168.1.10"], {"sn": "L123"}),
        MdnsService("_whatsup._tcp.local.", "wu-right", "right.local.", 41001,
                    ["192.168.1.11"], {"sn": "R456"}),
        MdnsService("_http._tcp.local.", "Printer", "printer.local.", 80,
                    ["192.168.1.50"], {"manufacturer": "HP"}),
    ]


def test_group_by_host_keeps_only_devialet_and_merges():
    hosts = group_by_host(make_services())
    assert set(hosts) == {"192.168.1.10", "192.168.1.11"}
    left = hosts["192.168.1.10"]
    assert left.serials == {"L123"}
    assert left.advertised_ports == [80, 41000]
    assert left.ipcontrol[0] is True
    assert hosts["192.168.1.11"].ipcontrol is None  # only WhatsUp seen


def make_run():
    services = make_services()
    hosts = group_by_host(services)
    hosts["192.168.1.10"].ports = [PortResult("192.168.1.10", 80, True, 3.2)]
    hosts["192.168.1.10"].http = [HttpResult("http://192.168.1.10:80/ipcontrol/v1/devices/current",
                                             200, "application/json", {"deviceName": "Left"})]
    ble = [BleDevice("AA:BB", "Phantom II", -60, [BLE_SERVICE_UUID]),
           BleDevice("CC:DD", "Headphones", -70)]
    udp = UdpListenResult([24243, 24244], packets=[UdpPacket("192.168.1.10", 24244, 24244, 4, "deadbeef", 0.0,
                                                            {"version": 2, "json": {"serialNumber": "L123"}})])
    return DiagnosticRun("2026-10-24T12:00:00Z", 12.3, {"http_probe": True},
                         ["_http._tcp.local.", "_whatsup._tcp.local."],
                         services, hosts, ble, None, udp)


def test_markdown_report_contents():
    md = render_markdown(make_run())
    assert "Devialet hosts found via mDNS: **2**" in md
    assert "IP Control eligible (app rules): **yes**" in md
    assert "`firmwareVersion` = `2.19.0.1`" in md
    assert '"deviceName": "Left"' in md
    assert "Phantom II" in md and "Headphones" not in md
    assert "deadbeef" in md
    assert "Printer" in md  # listed under non-Devialet answers


def test_json_report_is_valid():
    data = json.loads(make_run().to_json())
    assert data["hosts"]["192.168.1.10"]["ipcontrol"][0] is True
    assert data["hosts"]["192.168.1.10"]["serials"] == ["L123"]


def test_ble_candidate_rules():
    assert BleDevice("1", None, None, [BLE_SERVICE_UUID.upper()]).is_candidate
    assert BleDevice("2", "DEVIALET Phantom", None).is_candidate
    assert not BleDevice("3", "Speaker", None).is_candidate
    assert not BleDevice("4", None, None).is_candidate


def test_parse_ble_manufacturer_data():
    from devialet_diag.ble import parse_manufacturer_data
    raw = bytes([0x12]) + b"ABCDEFGHIJKLM" + bytes([2, 19, 0, 3, 0, 7])
    adv = parse_manufacturer_data(raw)
    assert adv.product_type == "DOS" and adv.serial == "ABCDEFGHIJKLM" and adv.firmware == "2.19.3.7"
    short = parse_manufacturer_data(bytes([0x10]) + b"XY")
    assert short.product_type == "LegacyDOS2" and short.serial is None and short.firmware is None


def test_ble_company_id_makes_candidate():
    from devialet_diag.constants import BLE_COMPANY_ID
    dev = BleDevice("EE", None, -50, manufacturer_data={BLE_COMPANY_ID: "11" + "41" * 13})
    assert dev.is_candidate and dev.devialet_advertisement.product_type == "SDOS"
