# Monitoring Tool

Local monitoring prototype using a custom Python Prometheus exporter, Prometheus, and Grafana.

## Current components

### Custom monitor exporter

Exposes basic system metrics at:

<http://localhost:9101/metrics>

### Prometheus

Prometheus runs locally in Docker Compose at:

<http://localhost:9090>

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

## Running Locally

From the root of the repository:

```bash
docker compose up --build
```

This starts:

```text
custom monitor exporter
Prometheus
Grafana
```

## Stopping Locally

```bash
docker compose down
```

## Testing the Exporter

Check that the custom exporter is exposing metrics:

```bash
curl localhost:9101/metrics
```