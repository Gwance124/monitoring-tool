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

## Prerequisite: Prometheus must scrape vLLM before the runs

`deploy/docker/prometheus/prometheus.yml` currently defines jobs for
`react-benchmark-replay`, `node-exporter`, `dcgm-exporter`,
`intel-pcm-exporter`, and `amd-pcm-exporter`. There is no vLLM job.

Prometheus cannot collect retroactively. A vLLM scrape job must be added and
running **before** the four experiments execute, or no data will exist to
extract. This is step one of implementation, not a later concern.

Global `scrape_interval` is `5s`. That is the floor on achievable CSV
resolution; `--interval` values finer than the scrape interval resample
without adding information.

## Alignment: anchor on first served token

`--start` and `--duration` define a **search window** that only has to
*contain* the run. The window may be padded generously; precision is not
required.

```
anchor := the first timestamp at which vllm:time_to_first_token_seconds_count
          begins increasing AND continues increasing for >= 3 consecutive
          scrapes

elapsed_seconds := timestamp - anchor
```

Properties:

- **Startup delay cancels.** If MARS needs 40s to warm its CXL pool and
  recompute needs 4s, each run anchors on its own first served token, so
  `elapsed = 0` denotes the same physical event across all four runs.
- **Operator timing precision is unnecessary.** A 900s search window around a
  360s test is sufficient.
- **The three-consecutive-scrape guard** rejects false anchors from health
  checks and readiness probes, which would otherwise pin `t=0` minutes early
  and silently misalign the entire comparison.

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
    --model <model-id> \
    --start 2026-08-01T10:00:00-07:00 --duration 900 --interval 5
```

Both emit an identical CSV schema. The fixture consists of Prometheus
text-exposition snapshots, one per scrape interval, so the histogram-parsing
code path is exercised by the dummy data as well. The parser is not bypassed
during development and will not first fail on experiment day.

Queried metrics:

```promql
histogram_quantile(0.95,
  sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[30s])))

histogram_quantile(0.95,
  sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[30s])))

vllm:time_to_first_token_seconds_count   # anchor detection
```

`--model` applies a label filter so a shared Prometheus serving multiple models
can be disambiguated.

## Data format

`runs/<system>/<run-id>.csv`:

```
timestamp,elapsed_seconds,system,ttft_p95_seconds,e2e_p95_seconds,requests_completed
1784912400,-60,mars,21.87,50.07,1043
1784912405,-55,mars,21.77,49.95,1102
1784912460,0,mars,20.14,47.30,2287
```

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
- Render smoke test: produce a 10-second clip, assert the output exists and has
  the expected frame count.

Fixtures are realistic rather than smooth: warm-up ramp, sample noise, a scrape
gap, and the pre-load health-check burst described above.

## Out of scope

- Automating the experiment runs themselves. The operator starts each system
  and its workload manually.
- Metrics beyond TTFT and end-to-end latency.
- Live streaming of the comparison. Output is a rendered video file.
