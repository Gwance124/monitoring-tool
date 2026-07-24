# ReAct Serving Benchmark

This package compares MARS, LMCache, Mooncake, and recompute even though only
one system can occupy the serving host at a time.

## Data flow

```text
vLLM /metrics
      |
      v
Prometheus query_range -- one isolated five-minute run per system
      |
      v
runs/<system>/<run-id>.csv
  timestamp          original Unix timestamp
  elapsed_seconds    timestamp - first timestamp
  ttft_p95_seconds   p95 vLLM time to first token
  e2e_p95_seconds    p95 vLLM end-to-end request latency
      |
      v
replay_exporter.py -- re-emits all saved systems at the same wall-clock instant
      |
      v
Prometheus -> Grafana shared comparison panels
```

The replay exporter is necessary because Prometheus time-series panels use
wall-clock timestamps. It preserves the normalized CSV as the benchmark record,
then maps equal `elapsed_seconds` values onto equal scrape times for display and
recording.

## 1. Capture each isolated run

Start the selected serving stack and its workload, note the test start time,
then run:

```bash
python3 benchmark/react_agent/capture_run.py \
  --system mars \
  --prometheus-url http://localhost:9090 \
  --start 2026-07-24T10:00:00-07:00 \
  --duration 300
```

Repeat with `--system lmcache`, `--system mooncake`, and
`--system recompute`. The default queries are:

```promql
histogram_quantile(
  0.95,
  sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[30s]))
)
```

```promql
histogram_quantile(
  0.95,
  sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[30s]))
)
```

Use `--ttft-query` or `--e2e-query` if the deployment needs model, instance, or
workload label filters. vLLM exposes both histograms from its `/metrics`
endpoint; see the
[official vLLM production metrics reference](https://docs.vllm.ai/en/latest/usage/metrics/).

The command writes an immutable CSV plus a JSON manifest under `runs/`. It
refuses to overwrite an existing run unless `--force` is supplied.

## 2. Start the dashboard

Generate the illustrative dataset once if no real captures exist:

```bash
python3 benchmark/react_agent/generate_demo_data.py
```

Then start the existing monitoring stack:

```bash
docker compose -f deploy/docker/docker-compose.yaml up -d
```

Open Grafana at <http://localhost:3000> and select:

```text
ReAct AI Agent Serving / ReAct Serving Benchmark Comparison
```

The replay exporter selects the latest CSV for each system and loops through
the common elapsed-time range. Set `BENCHMARK_REPLAY_SPEED=10` to replay a
five-minute test in 30 seconds.

The replay stops at its final sample by default, so a recording ends with the
progress bar and all metric values visibly held at `300 s`. Set
`BENCHMARK_REPLAY_LOOP=true` only for a continuously looping wall display.
`POST http://localhost:9108/reset` starts a new replay at `t=0`.
For a clean recording, call `/prepare`, wait for one Prometheus scrape while
the exporter is held at `t=0`, and then call `/start`.

MARS improvement is exported as the live fraction:

```text
(recompute TTFT - MARS TTFT) / recompute TTFT
```

Grafana formats that fraction as a percentage. For example, `0.20` is shown as
`20%`; the underlying value remains a calculated ratio rather than a hard-coded
percentage.

## 3. Select exact run files

By default, the lexicographically latest CSV per system is used. To pin a
comparison, mount a JSON file and set `BENCHMARK_RUN_SELECTION`:

```json
{
  "mars": "mars/20260724T170000Z.csv",
  "lmcache": "lmcache/20260724T172000Z.csv",
  "mooncake": "mooncake/20260724T173000Z.csv",
  "recompute": "recompute/20260724T174000Z.csv"
}
```

Paths are relative to `BENCHMARK_RUNS_DIR`. Restart the replay exporter after
changing the selection.

## 4. Generate the replay video and PowerPoint

```bash
python3 benchmark/react_agent/render_replay_video.py \
  --runs-dir benchmark/react_agent/runs \
  --output outputs/react-serving-benchmark-replay.mp4
```

The repository deliverable also includes a PowerPoint with that MP4 embedded.
The included dataset is explicitly labeled illustrative; regenerate both
artifacts after capturing real runs.

## Record the native Grafana dashboard

This is the preferred video path when Grafana is available. It captures Grafana
in kiosk mode instead of redrawing its charts:

```bash
npm install --prefix benchmark/react_agent/recorder
BENCHMARK_REPLAY_SPEED=10 \
  docker compose -f deploy/docker/docker-compose.yaml up -d \
  prometheus benchmark-replay grafana
npm run --prefix benchmark/react_agent/recorder record
```

The recorder holds the exporter at `t=0`, waits for the first scrape, starts the
replay, and records through the held `t=300` endpoint. Its output is:

```text
outputs/react-serving-grafana-replay.mp4
```
