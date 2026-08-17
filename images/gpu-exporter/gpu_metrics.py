#!/usr/bin/env python3
"""AMD GPU metrics exporter — reads amdgpu sysfs, serves Prometheus format on :9101.

Zero dependencies beyond Python stdlib. Reads amdgpu kernel driver sysfs
attributes directly — no ROCm runtime, no rocm-smi binary, no /dev/kfd.
Designed for rootless Podman with /sys bind-mounted read-only at /host/sys.

Metrics exposed (per AMD GPU, labeled by PCI address + card name):
  amd_gpu_busy_percent         — GPU utilization (0-100)
  amd_gpu_memory_busy_percent  — memory controller utilization (0-100)
  amd_gpu_vram_used_bytes      — VRAM used
  amd_gpu_vram_total_bytes     — VRAM total
  amd_gpu_gtt_used_bytes       — GTT (system-shared) memory used
  amd_gpu_gtt_total_bytes      — GTT total
  amd_gpu_temp_celsius         — junction temperature
  amd_gpu_power_watts          — power draw
  amd_gpu_gpu_clock_mhz        — GPU clock
  amd_gpu_mem_clock_mhz        — memory clock
  amd_gpu_fan_percent          — fan duty cycle (0-100)
  amd_gpu_rebar_optimal        — Resizable BAR engaged (1) or not (0)
  amd_gpu_bar_bytes            — largest PCI BAR size (bytes)
"""
from __future__ import annotations

import glob
import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler

SYSFS_BASE = os.environ.get("GPU_SYSFS_PATH", "/host/sys/class/drm")
PCI_SYSFS = os.environ.get("GPU_PCI_SYSFS_PATH", "/host/sys/bus/pci/devices")
PORT = int(os.environ.get("GPU_EXPORTER_PORT", "9101"))


def read_int(path: str) -> int | None:
    try:
        return int(open(path).read().strip())
    except (IOError, ValueError):
        return None


def read_str(path: str) -> str | None:
    try:
        return open(path).read().strip()
    except IOError:
        return None


def largest_bar_bytes(pci_addr: str) -> int | None:
    """Largest PCI BAR (bytes) for a GPU, read from sysfs `resource`. With
    Resizable BAR engaged this is the full VRAM; otherwise the legacy 256 MiB
    aperture. Returns None if the BAR can't be read."""
    try:
        max_bar = 0
        with open(f"{PCI_SYSFS}/{pci_addr}/resource") as fh:
            for n, line in enumerate(fh):
                if n >= 7:  # first 7 lines are BAR0..BAR6
                    break
                parts = line.split()
                if len(parts) < 2:
                    continue
                start, end = int(parts[0], 16), int(parts[1], 16)
                if start and end > start:
                    max_bar = max(max_bar, end - start + 1)
        return max_bar or None
    except IOError:
        return None


def find_amd_gpus() -> list[dict]:
    """Enumerate AMD GPU devices under /sys/class/drm/."""
    gpus = []
    for entry in sorted(glob.glob(f"{SYSFS_BASE}/card[0-9]*")):
        if not re.match(r"card\d+$", entry.rsplit("/", 1)[-1]):
            continue
        device_path = f"{entry}/device"
        vendor = read_str(f"{device_path}/vendor")
        if vendor != "0x1002":
            continue
        pci_addr = os.path.basename(os.path.realpath(device_path))
        if not re.match(r"0000:[0-9a-f]+:[0-9a-f]+\.[0-9a-f]+", pci_addr):
            pci_addr = "unknown"
        card_name = entry.rsplit("/", 1)[-1]
        hwmons = sorted(glob.glob(f"{device_path}/hwmon/hwmon*"))
        gpus.append({
            "path": device_path,
            "pci": pci_addr,
            "card": card_name,
            "hwmon": hwmons[0] if hwmons else None,
            "bar": largest_bar_bytes(pci_addr),
        })
    return gpus


def collect_metrics() -> str:
    gpus = find_amd_gpus()
    lines: list[str] = []

    def emit(name: str, help_text: str, mtype: str, values: list[tuple[str, float]]):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        for labels, val in values:
            lines.append(f"{name}{{{labels}}} {val}")

    for gpu in gpus:
        lbl = f'pci="{gpu["pci"]}",card="{gpu["card"]}"'
        p = gpu["path"]

        emit("amd_gpu_busy_percent", "GPU utilization (0-100)", "gauge",
             [(lbl, v)] if (v := read_int(f"{p}/gpu_busy_percent")) is not None else [])
        emit("amd_gpu_memory_busy_percent", "Memory controller utilization (0-100)", "gauge",
             [(lbl, v)] if (v := read_int(f"{p}/memory_busy_percent")) is not None else [])
        emit("amd_gpu_vram_used_bytes", "VRAM used (bytes)", "gauge",
             [(lbl, v)] if (v := read_int(f"{p}/mem_info_vram_used")) is not None else [])
        emit("amd_gpu_vram_total_bytes", "VRAM total (bytes)", "gauge",
             [(lbl, v)] if (v := read_int(f"{p}/mem_info_vram_total")) is not None else [])
        emit("amd_gpu_gtt_used_bytes", "GTT used (bytes)", "gauge",
             [(lbl, v)] if (v := read_int(f"{p}/mem_info_gtt_used")) is not None else [])
        emit("amd_gpu_gtt_total_bytes", "GTT total (bytes)", "gauge",
             [(lbl, v)] if (v := read_int(f"{p}/mem_info_gtt_total")) is not None else [])

        h = gpu["hwmon"]
        if h:
            emit("amd_gpu_temp_celsius", "GPU temperature (Celsius)", "gauge",
                 [(lbl, v / 1000)] if (v := read_int(f"{h}/temp1_input")) is not None else [])
            pw = read_int(f"{h}/power1_average") or read_int(f"{h}/power1_input")
            emit("amd_gpu_power_watts", "GPU power draw (watts)", "gauge",
                 [(lbl, pw / 1_000_000)] if pw is not None else [])
            emit("amd_gpu_gpu_clock_mhz", "GPU clock speed (MHz)", "gauge",
                 [(lbl, v / 1_000_000)] if (v := read_int(f"{h}/freq1_input")) is not None else [])
            emit("amd_gpu_mem_clock_mhz", "Memory clock speed (MHz)", "gauge",
                 [(lbl, v / 1_000_000)] if (v := read_int(f"{h}/freq2_input")) is not None else [])
            emit("amd_gpu_fan_percent", "Fan duty cycle (0-100)", "gauge",
                 [(lbl, v / 255 * 100)] if (v := read_int(f"{h}/pwm1")) is not None else [])

        bar = gpu.get("bar")
        if bar is not None:
            emit("amd_gpu_bar_bytes", "Largest PCI BAR size (bytes)", "gauge",
                 [(lbl, bar)])
            emit("amd_gpu_rebar_optimal",
                 "Resizable BAR engaged (1=yes, 0=no; optimal when largest BAR >= 1GiB)",
                 "gauge", [(lbl, 1.0 if bar >= (1 << 30) else 0.0)])

    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(collect_metrics().encode())
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b'<a href="/metrics">metrics</a>')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    print(f"AMD GPU metrics exporter on :{PORT} (sysfs: {SYSFS_BASE})", flush=True)
    for gpu in find_amd_gpus():
        bar = gpu.get("bar")
        if bar is not None and bar < (1 << 30):
            print(f"WARN: GPU {gpu['pci']} ({gpu['card']}) Resizable BAR not "
                  f"engaged (largest BAR {bar // (1 << 20)} MiB) — model "
                  f"load/unload is slower; steady inference is unaffected "
                  f"while the model fits in VRAM. Enable Above 4G Decoding + "
                  f"Re-Size BAR in BIOS", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
