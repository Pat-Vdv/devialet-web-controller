import pytest

from devialet_diag.mdns import MdnsService, classify_ipcontrol, parse_version

BASE_TXT = {"manufacturer": "Devialet", "ipControlVersion": "1"}


@pytest.mark.parametrize("text,expected", [
    ("2.18.0.0", (2, 18, 0, 0)),
    ("2.19.3", (2, 19, 3, 0)),
    ("2.17.9.123-rc1", (2, 17, 9, 1231)),
    (None, (0, 0, 0, 0)),
    ("garbage", (0, 0, 0, 0)),
])
def test_parse_version(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize("extra,eligible", [
    ({"firmwareFamily": "SDOS"}, True),
    ({"firmwareFamily": "DOS", "firmwareVersion": "2.18.0.0"}, True),
    ({"firmwareFamily": "DOS", "firmwareVersion": "2.20.1.4"}, True),
    ({"firmwareFamily": "DOS", "firmwareVersion": "2.17.9.9"}, False),
    ({"firmwareFamily": "DOS"}, False),
    ({"firmwareFamily": "OTHER"}, False),
])
def test_classify_ipcontrol_firmware_rules(extra, eligible):
    ok, reason = classify_ipcontrol({**BASE_TXT, **extra})
    assert ok is eligible
    assert reason


def test_classify_requires_manufacturer_and_version():
    assert classify_ipcontrol({"ipControlVersion": "1", "firmwareFamily": "SDOS"})[0] is False
    assert classify_ipcontrol({"manufacturer": "Devialet", "ipControlVersion": "2",
                               "firmwareFamily": "SDOS"})[0] is False


def test_service_devialet_detection_and_serial():
    whatsup = MdnsService("_whatsup._tcp.local.", "x", txt={"sn": "N0A1"})
    http = MdnsService("_http._tcp.local.", "y", txt={"manufacturer": "Devialet",
                                                       "serialNumber": "N0B2"})
    printer = MdnsService("_http._tcp.local.", "printer", txt={"manufacturer": "HP"})
    assert whatsup.is_devialet and whatsup.serial == "N0A1"
    assert http.is_devialet and http.serial == "N0B2"
    assert not printer.is_devialet
