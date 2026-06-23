# Docker Compose Deployment

This directory is for Docker Compose testing and non-Kubernetes demos. It is
normal for a monitoring repo like this to keep a small Compose setup for lab
validation while Kubernetes owns the real multi-node deployment.

Use this path to prove that Prometheus, Grafana, and the exporters can talk to
each other before moving the same monitoring idea into Kubernetes.

## Files

```text
docker-compose.yaml              Prometheus and Grafana
docker-compose.exporters.yaml    node, DCGM, and PCM exporters
.env.example                     Optional Compose variable examples
prometheus/prometheus.yml        Prometheus scrape jobs
prometheus/file_sd/local/        one-server Compose targets
prometheus/file_sd/remote/       remote server target inventory
```

## Modes

Prometheus always reads target files from `/etc/prometheus/file_sd` inside the
container. Compose mounts one of these host directories there:

```text
./prometheus/file_sd/local
./prometheus/file_sd/remote
```

Use `./prometheus/file_sd/local` for one-server tests and
`./prometheus/file_sd/remote` for monitoring exporters on other servers.

## Environment File

Docker Compose automatically reads a file named `.env` from this directory when
you run commands from `deploy/docker`.

The tracked file is:

```text
.env.example
```

Your local file should be:

```text
.env
```

Create it with:

```bash
cp .env.example .env
```

Do not commit `.env`. Commit `.env.example` only.

If there is no `.env` file, Compose uses the default values written directly in
the Compose YAML files. That means Prometheus, Grafana, and node-exporter still
use their default images and ports. For `PROMETHEUS_FILE_SD_DIR`, the default is
remote mode:

```text
./prometheus/file_sd/remote
```

For a one-server test, set this in `.env`:

```env
PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local
```

For the remote server-M/server-A test, set this in `.env`:

```env
PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/remote
```

You can also pass a value for one command without creating or editing `.env`:

```bash
sudo env PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local \
  docker compose -f docker-compose.yaml -f docker-compose.exporters.yaml up -d
```

Do not run `PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local` as a separate
line by itself unless you also export it. That only creates a shell variable for
that shell and may not be passed through `sudo`.

## Required Custom PCM Image

PCM/CXL monitoring currently requires a custom PCM image built from a modified
version of Intel's `intel/pcm` source code. In our GitLab, that source is under
`mlee.sk/pcm`.

```text
cxl-pcm:2026-06-16
```

The current built image is on server M, `solab-p7`, by default. If another
server already has the image, it can be used as the SSH source instead.

This modified PCM source adds:

```text
SPR/EMR processors:
  pcm-memory:
    system CXL write bandwidth
  pcm-sensor-server:
    system CXL read bandwidth

BHS / 6th Gen Intel Xeon processors:
  pcm-memory:
    CXL memory/cache bandwidth support
  pcm-sensor-server:
    per-socket CXL read memory bandwidth
    per-socket CXL read cache bandwidth
```

Every server that runs `pcm-exporter` must have this image available before
starting Docker Compose with `--profile pcm`.

On the source server, create the image tarball once. By default this is server
M, `solab-p7`:

```bash
sudo docker save cxl-pcm:2026-06-16 | gzip > /tmp/cxl-pcm-2026-06-16.tar.gz
```

On each exporter server, copy the tarball from the source server and load it:

```bash
scp USER@solab-p7:/tmp/cxl-pcm-2026-06-16.tar.gz /tmp/
gunzip -c /tmp/cxl-pcm-2026-06-16.tar.gz | sudo docker load
sudo docker image ls cxl-pcm
```

Then set the exporter server's `deploy/docker/.env` to use the local image name:

```env
PCM_EXPORTER_IMAGE=cxl-pcm:2026-06-16
```

Repeat the exporter-server `scp` and `docker load` steps for each server that
should run the custom PCM image.

## One-Server Test

Everything runs on one server:

```text
same server:
  Prometheus
  Grafana
  node-exporter
  dcgm-exporter
  pcm-exporter
```

Run on that server:

```bash
cd deploy/docker
sudo env PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local \
  docker compose -f docker-compose.yaml -f docker-compose.exporters.yaml --profile node --profile gpu --profile pcm up -d
```

If `.env` already has `PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local`, this
can be shortened to:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml -f docker-compose.exporters.yaml --profile node --profile gpu --profile pcm up -d
```

Use profiles only for hardware that exists on the server:

```bash
# CPU/basic host: Prometheus, Grafana, and node-exporter only.
sudo env PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local \
  docker compose -f docker-compose.yaml -f docker-compose.exporters.yaml --profile node up -d

# NVIDIA GPU host: add dcgm-exporter.
sudo env PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local \
  docker compose -f docker-compose.yaml -f docker-compose.exporters.yaml --profile node --profile gpu up -d

# Intel PCM/CXL host: add pcm-exporter.
sudo env PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local \
  docker compose -f docker-compose.yaml -f docker-compose.exporters.yaml --profile node --profile pcm up -d

# Host with both GPU and PCM/CXL access.
sudo env PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/local \
  docker compose -f docker-compose.yaml -f docker-compose.exporters.yaml --profile node --profile gpu --profile pcm up -d
```

If you omit `--profile node`, Compose does not start `node-exporter`. If you
omit `--profile gpu`, Compose does not start `dcgm-exporter`. If you omit
`--profile pcm`, Compose does not start `pcm-exporter`.

You can also set profiles once in `deploy/docker/.env` on each exporter server:

```env
COMPOSE_PROFILES=node,gpu,pcm
```

Then start the exporters without repeating `--profile` flags:

```bash
sudo docker compose -f docker-compose.exporters.yaml up -d
```

The local `file_sd` directory includes target files for node, DCGM, and PCM. If
you run without the GPU or PCM profiles, Prometheus may still list those omitted
exporters as `DOWN`. That is expected unless you remove or empty the matching
local target file for that test:

```text
prometheus/file_sd/local/dcgm-exporter.yml
prometheus/file_sd/local/pcm-exporter.yml
```

For this mode, Prometheus uses the target files under
`prometheus/file_sd/local/`. Those files use Docker Compose service names:

```yaml
- targets:
    - node-exporter:9100
  labels:
    deployment: docker-compose
```

That works because Compose gives containers on the same machine DNS names that
match the service names.

## Two-Server Test

This is the checked-in remote example:

```text
server M: 190.160.3.40  Prometheus and Grafana
server A: 190.160.3.61  exporters
```

Run Prometheus and Grafana on server M:

```bash
cd deploy/docker
sudo env PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/remote \
  docker compose -f docker-compose.yaml up -d
```

If `.env` already has `PROMETHEUS_FILE_SD_DIR=./prometheus/file_sd/remote`, this
can be shortened to:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml up -d
```

Run exporters on server A:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.exporters.yaml --profile node --profile gpu --profile pcm up -d
```

Docker Compose can also start every profiled service with:

```bash
sudo docker compose -f docker-compose.exporters.yaml --profile "*" up -d
```

Use that only on servers that should run all exporter services in
`docker-compose.exporters.yaml`.

The target files under `prometheus/file_sd/remote/` point Prometheus at exporter
ports on server A:

```text
prometheus/file_sd/remote/node-exporter.yml  -> 190.160.3.61:9100
prometheus/file_sd/remote/dcgm-exporter.yml  -> 190.160.3.61:9400
prometheus/file_sd/remote/pcm-exporter.yml   -> 190.160.3.61:9738
```

The `server: server-a` label is just metric metadata in Prometheus. It is not
DNS and it does not control networking.

## More Docker Servers

For more exporter servers, edit one inventory file on monitor M:

```text
inventory/exporters.yaml
```

Format:

```yaml
exporters:
  - name: server-a
    host: 190.160.3.61
    gpu: true
    pcm: true

  - name: server-b
    host: 190.160.3.62
    gpu: false
    pcm: false

  - name: server-c
    host: 190.160.3.63
    gpu: true
    pcm: false

  - name: server-d
    host: 190.160.3.64
    gpu: false
    pcm: true
```

`name` is the friendly Grafana/Prometheus label. `host` is the IP address or
DNS name reachable from monitor M. `node-exporter` is generated for every row.
Set `gpu=true` only when that server runs `dcgm-exporter`. Set `pcm=true` only
when that server runs `pcm-exporter`.

`inventory/exporters.yaml` controls what Prometheus scrapes. It does not start
or stop Docker containers. Docker Compose profiles control which containerized
exporters run on each server.

For services that already expose their own `/metrics` endpoint, such as vLLM,
you usually do not need a Docker Compose exporter service. Add a generated
Prometheus target for that endpoint instead. For example, the generator could
be extended with a `vllm` entry and each server could opt in with
`vllm: true`.

The generator uses PyYAML and python-dotenv. Create a small virtual
environment for those dependencies:

```bash
cd deploy/docker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install PyYAML python-dotenv
```

Generate the Prometheus target files:

```bash
./scripts/generate-file-sd.sh
```

The script writes:

```text
prometheus/file_sd/remote/node-exporter.yml
prometheus/file_sd/remote/dcgm-exporter.yml
prometheus/file_sd/remote/pcm-exporter.yml
```

Prometheus watches `file_sd` files and should pick up those target changes
automatically. You do not need to restart Prometheus for normal target additions
or removals.

Generated example for `node-exporter.yml`:

```yaml
- targets:
    - 190.160.3.61:9100
  labels:
    server: server-a

- targets:
    - 190.160.3.62:9100
  labels:
    server: server-b

- targets:
    - 190.160.3.63:9100
  labels:
    server: server-c
```

The generated files use these ports:

```text
node-exporter:   9100
dcgm-exporter:   9400
pcm-exporter:    9738
```

You do not need `extra_hosts` when target files use IP addresses. Use
`extra_hosts` only if you want Prometheus to scrape names such as `server-a` and
those names are not available through DNS.

## Exporter Ports

```text
node-exporter:   9100
dcgm-exporter:   9400
pcm-exporter:    9738
```

## Access

When Prometheus and Grafana run on server M:

```text
Prometheus: http://190.160.3.40:9090
Grafana:    http://190.160.3.40:3000
```

The default Grafana login is controlled by `.env`:

```text
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=admin
```

Inside Grafana, use the Docker Compose service name for the Prometheus data
source:

```text
http://prometheus:9090
```

Do not use `localhost:9090` as the Grafana data source URL. Inside the Grafana
container, `localhost` means the Grafana container itself.

## Grafana Provisioning

Grafana loads the Prometheus data source and dashboard automatically from these
mounted files:

```text
deploy/docker/grafana/provisioning/datasources/prometheus.yml
deploy/docker/grafana/provisioning/dashboards/hardware-monitoring.yml
dashboards/hardware-fleet-overview.json
```

The provisioned data source is named `Prometheus` and uses this internal URL:

```text
http://prometheus:9090
```

The dashboard appears in Grafana under:

```text
Dashboards -> Hardware Monitoring -> Hardware Fleet Overview
```

After changing dashboard JSON or provisioning files, restart Grafana:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml restart grafana
```

## Operations

Check the monitoring stack on server M:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml ps
```

Check the exporters on a monitored server:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.exporters.yaml --profile node --profile gpu --profile pcm ps
```

Restart Prometheus and Grafana on server M:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml restart
```

Restart only Prometheus after changing `prometheus.yml` or `file_sd` files:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml restart prometheus
```

Restart exporters on a monitored server:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.exporters.yaml --profile node --profile gpu --profile pcm restart
```

View logs:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml logs -f prometheus
sudo docker compose -f docker-compose.yaml logs -f grafana
sudo docker compose -f docker-compose.exporters.yaml --profile node --profile gpu --profile pcm logs -f
```

Stop Prometheus and Grafana on server M:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml down
```

Stop exporters on a monitored server:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.exporters.yaml --profile node --profile gpu --profile pcm down
```

Remove saved Prometheus and Grafana data only when you intentionally want a
fresh start:

```bash
cd deploy/docker
sudo docker compose -f docker-compose.yaml down -v
```

## Why Kubernetes Later

Docker Compose does not deploy containers across many servers by itself. If you
want to monitor ten servers with Docker Compose, you usually run exporter
containers on each server and manually maintain the Prometheus target files.

Kubernetes makes that easier because the control plane schedules DaemonSets on
the labeled nodes, and Prometheus discovers the exporters through
ServiceMonitors instead of manually edited `file_sd` target files.
