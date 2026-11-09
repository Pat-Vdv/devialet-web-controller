"""Bridges to the non-Python parts of the analyzer: JS ballistics (node) and the
C# agent self tests (Windows csc via WSL interop). Skipped when unavailable."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent" / "windows"
CSC = Path("/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
SOURCES = ["SpectrumCore.cs", "Wasapi.cs", "Agent.cs", "SelfTest.cs"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_ballistics_settings_and_fullscreen():
    """Runs every tests/js/*.test.cjs: ballistics, peak hold, themes, prefs, fullscreen."""
    files = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.cjs"))
    assert len(files) >= 2
    result = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fail 0" in result.stdout


@pytest.mark.skipif(not CSC.exists() or shutil.which("wslpath") is None,
                    reason="Windows csc not reachable (not running under WSL)")
def test_agent_compiles_and_self_tests_pass(tmp_path):
    exe = tmp_path / "agent-selftest.exe"
    win_out = subprocess.run(["wslpath", "-w", str(exe)], capture_output=True, text=True).stdout.strip()
    build = subprocess.run([str(CSC), "/nologo", "/optimize+", "/warnaserror+", "/target:exe",
                            f"/out:{win_out}", *SOURCES], cwd=AGENT, capture_output=True, text=True,
                           timeout=120)
    assert build.returncode == 0, build.stdout + build.stderr
    os.chmod(exe, 0o755)
    run = subprocess.run([str(exe), "--selftest"], capture_output=True, text=True, timeout=120)
    output = run.stdout.replace("\r", "")
    assert run.returncode == 0, output
    assert "0 failed" in output
    passed = [line for line in output.splitlines() if line.startswith("PASS ")]
    for name in ("fft matches naive DFT", "left-only signal stays on the left",
                 "silence gives floor levels", "discontinuity clears stale samples",
                 "25 frames per second", "French locale"):
        assert any(name in line for line in passed), name


def test_agent_never_records_or_listens():
    """Static guard: the agent has no file output and opens no listening socket."""
    code = "\n".join((AGENT / name).read_text() for name in SOURCES)
    for forbidden in (r"System\.IO\b", r"\bFile\.", r"FileStream", r"StreamWriter", r"BinaryWriter",
                      r"HttpListener", r"TcpListener", r"\bSocket\(", r"UdpClient"):
        assert not re.search(forbidden, code), forbidden
    # the only network use is the client WebSocket to the local backend
    assert "ClientWebSocket" in code and "ws://localhost:8000/api/spectrum/agent" in code
