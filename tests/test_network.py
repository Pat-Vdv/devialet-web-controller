import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from devialet_diag import network


class _Handler(BaseHTTPRequestHandler):
    methods_seen: list[str] = []

    def do_GET(self):  # noqa: N802
        self.methods_seen.append("GET")
        body = json.dumps({"path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_tcp_port_open_and_closed():
    server = _serve()
    try:
        port = server.server_address[1]
        results = asyncio.run(network.check_ports("127.0.0.1", [port, port]))
        assert len(results) == 1 and results[0].open
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        closed = asyncio.run(network.check_tcp_port("127.0.0.1", free, timeout=1))
        assert not closed.open and closed.error
    finally:
        server.shutdown()


def test_ipcontrol_get_only_issues_get():
    _Handler.methods_seen = []
    server = _serve()
    try:
        port = server.server_address[1]
        results = asyncio.run(network.ipcontrol_get("127.0.0.1", port, "/ipcontrol/v1/",
                                                    paths=("/devices/current",)))
        assert results[0].status == 200
        assert results[0].body == {"path": "/ipcontrol/v1/devices/current"}
        assert _Handler.methods_seen == ["GET"]
    finally:
        server.shutdown()


def test_ipcontrol_get_reports_connection_errors():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    results = asyncio.run(network.ipcontrol_get("127.0.0.1", free, paths=("/x",), timeout=1))
    assert results[0].status is None and results[0].error


def test_listen_udp_receives_packets():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    def send_later():
        import time
        time.sleep(0.3)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
            tx.sendto(b"\x01\x02hello", ("127.0.0.1", port))

    threading.Thread(target=send_later, daemon=True).start()
    result = network.listen_udp([port], duration=1.5)
    assert result.errors == []
    assert result.packets and result.packets[0].hex == b"\x01\x02hello".hex()


def _dvlt(version: int, ptype: int, payload: bytes) -> bytes:
    return (b"_DVLT_" + version.to_bytes(2, "little") + bytes([ptype])
            + len(payload).to_bytes(2, "little") + payload)


def test_parse_dvlt_v2_json():
    body = json.dumps({"serialNumber": "N0X", "ipControlVersion": "1"}).encode()
    decoded = network.parse_dvlt_packet(_dvlt(2, 0, body))
    assert decoded["version"] == 2 and decoded["json"]["serialNumber"] == "N0X"


def test_parse_dvlt_v1_host_id():
    import uuid
    host = uuid.uuid4()
    decoded = network.parse_dvlt_packet(_dvlt(1, 0, host.bytes))
    assert decoded["host_id"] == str(host)


def test_parse_dvlt_rejects_foreign_and_solicitation_is_decodable():
    from devialet_diag.constants import UDP_V2_SOLICITATION
    assert network.parse_dvlt_packet(b"hello world") is None
    sol = network.parse_dvlt_packet(UDP_V2_SOLICITATION)
    assert sol == {"version": 2, "type": 1, "length": 0, "payload_hex": ""}


def test_solicitation_is_sent_only_when_requested():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    passive = network.listen_udp([port], duration=0.3)
    assert passive.solicited is False
