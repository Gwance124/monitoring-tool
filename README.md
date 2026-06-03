# Monitoring Tool

Local monitoring prototype using a custom Python Prometheus exporter, Prometheus, and Grafana.

### Running Locally

From the root of the repository:

```bash
docker compose up --build
```

This starts:

```text
custom monitor exporter
node exporter
Prometheus
Grafana
```

### Stopping Locally

```bash
docker compose down
```

### Custom monitor exporter

Exposes basic system metrics at:

<http://localhost:9101/metrics>

### Node exporter

Exposes node-level CPU, memory, filesystem, and OS metrics at:

<http://localhost:9100/metrics>

Prometheus scrapes it inside Docker Compose at:

```text
node-exporter:9100
```

### Prometheus

Prometheus runs locally in Docker Compose at:

<http://localhost:9090>

Prometheus targets can be checked at:

<http://localhost:9090/targets>

### Grafana

Grafana runs locally in Docker Compose at:

<http://localhost:3000>

Default login:

```text
username: admin
password: admin
```

When adding Prometheus as a Grafana data source, use this URL:

```text
http://prometheus:9090
```

## Testing the Exporter

Check that the custom exporter is exposing metrics:

```bash
curl localhost:9101/metrics
```

Check that node exporter is exposing metrics:

```bash
curl localhost:9100/metrics
```

Example node exporter metrics to query in Prometheus:

```text
node_memory_MemTotal_bytes
node_memory_MemAvailable_bytes
node_cpu_seconds_total
```
