# ReAct Serving Benchmark Replay — Design

Date: 2026-07-24
Status: Approved for planning

## Problem

Compare TTFT and end-to-end latency for four ReAct AI agent serving systems —
MARS, LMCache, Mooncake, and recompute — when only one system can occupy the
serving host at a time. The four runs therefore happen at different wall-clock
times and must be overlaid on a shared axis.

Two constraints drive the design:

1. The work servers are headless. Any solution that requires a browser to
   render or record Grafana is unusable.
2. The runs cannot be started at a coordinated instant, and each system has a
   different startup delay, so wall-clock start time is not a usable anchor.

The deliverable is a video that reads as a live dashboard: a scrolling,
already-full time-series window with live stat tiles, not a chart that grows
from an empty axis.

## Data source: the /metrics endpoint, not logs

vLLM natively exposes `GET /metrics` on its API port — a Prometheus
text-exposition page of counters and histograms, regenerated per request.
Several vLLM hosts are scraped on the work fleet; `http://192.168.3.4:8000/metrics`
is the one verified while writing this design.

Prometheus scrapes that endpoint and stores numeric samples in its TSDB. It
does not store logs. vLLM's stdout engine logs are a separate stream and
contain neither TTFT nor end-to-end latency, so nothing in this design reads
them.

Consequently, whether vLLM runs under Docker or as a bare `vllm serve` process
does not affect this design. It changes only the target address in
`file_sd/remote/vllm-exporter.yml`.

## Prerequisite: scrape interval

The work-server Prometheus already scrapes vLLM via
`file_sd/remote/vllm-exporter.yml`. Its `global.scrape_interval` is `15s`.

15s is too coarse. A 300s benchmark yields 20 points, and the 60-second
scrolling window would hold four points per line — a jagged polyline rather
than a continuous trace.

Set `scrape_interval: 1s` **as a per-job override on the vLLM job only**:

```yaml
  - job_name: vllm-exporter
    scrape_interval: 1s
    file_sd_configs:
      - files:
          - /etc/prometheus/file_sd/vllm-exporter.yml
```

`global` stays at 15s. It governs the node, DCGM, and PCM exporter jobs, which
are high-cardinality and whose metrics do not change meaningfully at 1s;
lowering it globally multiplies their write volume for no benefit. This
per-job override mirrors the existing `react-benchmark-replay` job, which
already runs at 1s.

1s yields 60 points across the scrolling window and 300 across the benchmark.
Going below 1s is not useful — vLLM updates these metrics on the engine loop,
so sub-second scrapes largely resample unchanged values.

Prometheus cannot collect retroactively. The interval override must be in place
**before** the four experiments run.

The scrape interval is the floor on CSV resolution; `--interval` values finer
than it resample without adding information.

## Alignment: anchor on first served token

`--start` and `--duration` define a **search window** that only has to
*contain* the run. The window may be padded generously; precision is not
required.

```
anchor := the first timestamp at which vllm:time_to_first_token_seconds_count
          begins increasing AND continues increasing throughout the following
          --anchor-sustain window (default 10s)

elapsed_seconds := timestamp - anchor
```

Properties:

- **Startup delay cancels.** If MARS needs 40s to warm its CXL pool and
  recompute needs 4s, each run anchors on its own first served token, so
  `elapsed = 0` denotes the same physical event across all four runs.
- **Operator timing precision is unnecessary.** A 900s search window around a
  360s test is sufficient.
- **The sustain guard** rejects false anchors from health checks and readiness
  probes, which would otherwise pin `t=0` minutes early and silently misalign
  the entire comparison.

The guard is expressed as a **duration** (`--anchor-sustain`, default `10s`),
not a sample count. A sample-count guard silently tightens as the scrape
interval drops — "3 consecutive scrapes" means 45s at a 15s interval but only
3s at 1s, which is short enough for a burst of readiness probes to satisfy.

This is valid because only one system occupies the vLLM instance at a time, so
the only sustained request load in the window is the run itself.

### Benchmark clock and warm-up exclusion

Each run captures **360 seconds** of serving load. The reporting clock is
offset from the anchor:

```
benchmark t=0 := anchor + 60s
```

The `anchor .. anchor+60s` segment serves two purposes: it pre-fills the
scrolling window with real data on the first video frame, and it is excluded
from headline statistics as warm-up. No synthetic samples are generated at any
point. The reported benchmark is `t = 0 .. 300s`.

## Components

All under `benchmark/react_agent/`.

| File | Responsibility | Depends on |
|---|---|---|
| `sources.py` | `fixture` and `prometheus` source adapters, both returning `list[(unix_ts, metric_name, value)]` | `requests` (prometheus path only) |
| `extract_run.py` | CLI: fetch samples, detect anchor, resample to `--interval`, write CSV + manifest | `sources.py` |
| `replay.py` | Load all four CSVs, align on `elapsed_seconds`, expose `frame_at(t)` | — |
| `render_video.py` | matplotlib Agg → `FFMpegWriter` → mp4 | `replay.py` |

`sources.py` is the seam that makes fixture-to-production a single flag change.
Each file has one purpose and is testable without the others.

### Source adapters

```bash
# dummy data, available today
extract_run.py --system mars --source fixture \
    --fixture fixtures/mars/

# real capture, after experiments run
extract_run.py --system mars --source prometheus \
    --prometheus-url http://solab-p7:9090 \
    --target solab-x3 \
    --start 2026-08-01T10:00:00-07:00 --duration 900 --interval 1
```

Both emit an identical CSV schema. The fixture consists of Prometheus
text-exposition snapshots, one per scrape interval, so the histogram-parsing
code path is exercised by the dummy data as well. The parser is not bypassed
during development and will not first fail on experiment day.

### Fixture derivation

The fixture is generated from a **real idle snapshot** of
`http://192.168.3.4:8000/metrics`, captured before any experiments run. An
idle endpoint still emits every `# HELP`/`# TYPE` line and every histogram
bucket boundary, so the snapshot supplies the authentic metric names and `le`
buckets for the deployed vLLM version. Synthetic values are then layered onto
that real structure.

This matters because metric names vary across vLLM versions — the V1 engine
renamed several — and because a job named `vllm-exporter` could denote either
vLLM's native endpoint or a third-party sidecar with entirely different names.
Deriving the fixture from the live endpoint removes the risk of building the
parser against names the deployment does not have.

### Confirmed metric names

Verified 2026-07-24 against `http://192.168.3.4:8000/metrics`. The deployment
runs the **vLLM V1 engine** — evidenced by `vllm:kv_cache_usage_perc` (V0 named
it `gpu_cache_usage_perc`), `vllm:prefix_cache_queries_total`, and
`vllm:engine_sleep_state`.

Charted metrics. `$TARGET` is the value of `--target`, matched against the
`server` label (see "Target selection is mandatory" below):

```promql
histogram_quantile(0.95, sum by (le) (rate(
  vllm:time_to_first_token_seconds_bucket{server="$TARGET"}[30s])))

histogram_quantile(0.95, sum by (le) (rate(
  vllm:e2e_request_latency_seconds_bucket{server="$TARGET"}[30s])))

vllm:time_to_first_token_seconds_count{server="$TARGET"}   # anchor detection
```

Supporting metrics — captured to CSV, not charted:

| Metric | Purpose |
|---|---|
| `vllm:request_queue_time_seconds` | Decomposes TTFT into queueing vs prefill |
| `vllm:request_prefill_time_seconds` | Isolates the KV-reuse benefit from queue effects |
| `vllm:external_prefix_cache_hits_total` / `_queries_total` | KV-connector hit rate — the path LMCache, Mooncake, and MARS plug into |
| `vllm:prompt_tokens_recomputed_total` / `_cached_total` | Recompute volume vs cache reuse; defines the `recompute` baseline |

These cost four extra columns and nothing at experiment time, whereas
recovering them later would require re-running all four experiments on GPU
hardware. They exist so the result can be defended: p95 TTFT alone conflates
queue time with prefill time, and a reviewer may reasonably ask whether MARS's
advantage is genuine KV reuse or lower queue occupancy under that load.

The headline claim remains p95 TTFT versus recompute. The supporting metrics
are evidence, not chart content.

### Target selection is mandatory

p7 scrapes **multiple vLLM hosts** through `file_sd/remote/vllm-exporter.yml`.
Each target carries a `server` label naming the host:

```yaml
- targets:
  - 192.168.3.73:8000
  labels:
    server: solab-x3
```

Every query must therefore be pinned to exactly one serving host.
`--target solab-x3` becomes the selector `{server="solab-x3"}`.

The `server` label is preferred over Prometheus's automatic `instance` label:
it is readable at the command line and survives a host changing IP address.

This is not a convenience. `sum by (le) (rate(...))` over an unfiltered
selector aggregates histogram buckets across every scraped vLLM instance and
returns a p95 that describes no real server. The query succeeds, the chart
renders, and the number is meaningless — a silent wrong answer rather than an
error.

`extract_run.py` therefore:

- requires a target selector (`--target`), and
- **fails loudly** if the search window contains samples from more than one
  vLLM instance after the selector is applied, listing the instances found.

The manifest records the resolved selector so every CSV states which physical
host produced it.

`--model` is a second, independent filter on `model_name`, needed only when one
host serves more than one model in the window. It is optional; `--target` is
not.

Health and readiness probes hit the ASGI layer (`http_requests_total`) without
entering the engine, so they do not increment `vllm:` request metrics. The
`--anchor-sustain` guard is retained regardless, since engine warm-up and
profiling runs remain possible sources of a false anchor.

## Data format

`runs/<system>/<run-id>.csv`:

Charted columns first, supporting columns after:

```
timestamp,elapsed_seconds,system,ttft_p95_seconds,e2e_p95_seconds,requests_completed,queue_p95_seconds,prefill_p95_seconds,ext_cache_hit_ratio,prompt_tokens_recomputed
1784912400,-60,mars,21.87,50.07,1043,3.10,18.60,0.71,140233
1784912401,-59,mars,21.77,49.95,1049,3.08,18.54,0.71,140698
1784912460,0,mars,20.14,47.30,2287,2.94,17.11,0.74,171904
```

`ext_cache_hit_ratio` is derived at extraction time as
`rate(external_prefix_cache_hits_total) / rate(external_prefix_cache_queries_total)`,
and is empty when the denominator is zero rather than reported as `0.0` — a
system with no external KV store has an *undefined* hit rate, not a zero one.
Writing `0.0` there would make `recompute` look like a failing cache instead of
an absent one.

`elapsed_seconds` is negative across the warm-up pre-fill segment and zero at
benchmark start. Empty metric cells are legal and represent scrape gaps. Gaps
render as breaks in the line, never as interpolated segments — a missing scrape
must look missing.

Each CSV is accompanied by a `.json` manifest recording system, run id, model,
search window, detected anchor, interval, sample count, and a
`data_classification` field (`fixture` or `captured`). `extract_run.py` refuses
to overwrite an existing run without `--force`.

## Video

- **Charts:** two stacked scrolling line panels — p95 TTFT and p95 end-to-end
  latency. Fixed 60-second trailing x-window, always full, scrolling left.
  Four lines per panel; MARS drawn at heavier weight.
- **Right rail:**
  - Headline: MARS improvement in p95 TTFT versus **recompute** as the fixed
    naive baseline.
  - Subline: MARS improvement versus the current second-best system, with that
    system named.
  - Horizontal bar groups showing current TTFT and current E2E per system, with
    numeric value labels.
  - Elapsed clock, `t = 143s / 300s`.
- **Stability:** the second-best system is determined from a 30-second trailing
  mean rather than the instantaneous value, so the subline does not strobe when
  two systems trade places.
- **Encoding:** frames stream directly to ffmpeg via matplotlib's
  `FFMpegWriter`; no per-frame temporary image files. If ffmpeg is absent, the
  renderer falls back to a PNG sequence plus a Pillow-generated GIF so a
  headless host without ffmpeg still produces output.
- `--speed` controls playback rate; `--speed 2` renders a 360s run as a ~150s
  video.

Colors and chart form follow the `dataviz` skill at implementation time.

## Disposition of existing code

| Path | Action | Reason |
|---|---|---|
| `capture_run.py` | Replace with `extract_run.py` | No pluggable source, no anchor detection |
| `generate_demo_data.py` | Replace with fixtures | Produces CSVs directly, bypassing the parser |
| `render_replay_video.py` | Replace with `render_video.py` | Hand-built SVG strings, requires `rsvg-convert`, and draws a growing line on a fixed 0–300s axis |
| `record_grafana_video.mjs` | Delete | Requires a browser; cannot run headless |
| `recorder/` | Delete | Vendored Playwright for the above |
| `encode_frames.swift` | Delete | macOS-only; servers are Linux |
| `replay_exporter.py` | Keep | Still feeds Grafana for live viewing over an SSH tunnel |
| `grafana/react-serving-benchmark.json` | Keep | Same |

Grafana remains useful for interactive inspection. It is simply no longer the
video path.

## Error handling

- **No anchor found** in the search window: fail with the observed
  `time_to_first_token_seconds_count` series so the operator can widen
  `--start`/`--duration` or confirm the run actually served traffic. Never fall
  back to using the window start as the anchor, which would produce a
  plausible-looking but wrongly aligned run.
- **Prometheus unreachable or returns an empty result:** fail explicitly,
  naming the query. An empty result most likely means the vLLM scrape job was
  missing during the run.
- **Run shorter than 360s** (anchor too close to the window end): warn, emit what
  exists, and mark the manifest `truncated: true`. The renderer trims all
  systems to the shortest common `elapsed_seconds` range.
- **Mixed `data_classification`** across the four runs: the renderer stamps a
  visible badge on the video rather than silently presenting fixture data as
  measured.

## Testing

- Anchor detection over synthetic counter series, including a health-check
  request burst 90 seconds before the real load. This is the case that would
  otherwise silently misalign every comparison.
- Histogram bucket to p95 parsing against the checked-in fixture.
- Resampling behaviour when `--interval` is coarser and finer than the scrape
  interval.
- Scrape-gap handling: verify gaps survive to the CSV as empty cells and reach
  the renderer as line breaks.
- Fixture to CSV golden-file comparison.
- `ext_cache_hit_ratio` is empty, not `0.0`, when external cache queries are
  zero — the case that distinguishes an absent KV store from a failing one.
- Render smoke test: produce a 10-second clip, assert the output exists and has
  the expected frame count.

Fixtures are realistic rather than smooth: warm-up ramp, sample noise, a scrape
gap, and the pre-load health-check burst described above.

## Out of scope

- Automating the experiment runs themselves. The operator starts each system
  and its workload manually.
- Metrics beyond TTFT and end-to-end latency.
- Live streaming of the comparison. Output is a rendered video file.
