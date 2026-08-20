# Telemetry exporters

Three read-only Prometheus exporters run on hermesnet, published to localhost
only. They expose host/container metrics for scraping; none of them has a
write path into the host (bind mounts are `:ro`, the podman socket is used
read-only).

## hermes-node-exporter

http://127.0.0.1:9100/metrics

Host CPU, RAM, disk, net, load. Stock `node-exporter` reading bind-mounted
`/proc`, `/sys`, `/`.

## hermes-gpu-exporter

http://127.0.0.1:9101/metrics

AMD GPU utilization, VRAM, temp, power from amdgpu sysfs (pure sysfs reads, no
ROCm runtime). Image builds from `images/gpu-exporter`:

```bash
podman build -t localhost/hermes-gpu-exporter:latest images/gpu-exporter
```

## hermes-podman-exporter

http://127.0.0.1:9102/metrics

Container, image, and volume stats via the rootless podman socket
(`systemctl --user enable --now podman.socket`).
