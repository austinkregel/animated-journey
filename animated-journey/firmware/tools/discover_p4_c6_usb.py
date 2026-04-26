#!/usr/bin/env python3
"""Probe USB serial ports with esptool; emit P4_PORT= and C6_PORT= for bash eval."""

from __future__ import annotations

import glob
import platform
import re
import subprocess
import sys
from typing import Optional


def candidate_ports() -> list[str]:
    if platform.system() == "Darwin":
        return sorted(glob.glob("/dev/tty.usb*"))
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


def chip_on_port(port: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "esptool",
                "--port",
                port,
                "--baud",
                "115200",
                "--connect-attempts",
                "2",
                "chip-id",
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    if re.search(r"ESP32[- ]?P4", out, re.I):
        return "esp32p4"
    if re.search(r"ESP32[- ]?C6", out, re.I):
        return "esp32c6"
    return None


def main() -> int:
    p4: list[str] = []
    c6: list[str] = []
    for port in candidate_ports():
        kind = chip_on_port(port)
        if kind == "esp32p4":
            p4.append(port)
        elif kind == "esp32c6":
            c6.append(port)

    if len(p4) == 1 and len(c6) == 1:
        print(f"P4_PORT={p4[0]}")
        print(f"C6_PORT={c6[0]}")
        return 0

    print(
        "Expected exactly one ESP32-P4 and one ESP32-C6 on USB serial.",
        file=sys.stderr,
    )
    print(f"  Detected P4 ({len(p4)}): {p4}", file=sys.stderr)
    print(f"  Detected C6 ({len(c6)}): {c6}", file=sys.stderr)
    if not candidate_ports():
        print(
            "  No /dev/tty.usb* (macOS) or ttyACM*/ttyUSB* (Linux) devices found.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
