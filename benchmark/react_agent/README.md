# ReAct serving benchmark

Compares MARS, LMCache, Mooncake, and recompute on ReAct agent serving when only
one system can occupy the vLLM host at a time.

## How the runs get onto one axis

Each run happens at a different wall-clock time, and each system takes a
different amount of time to become ready. So wall-clock start is not the anchor.

Instead, `t=0` is derived from the data: the first timestamp at which
`vllm:time_to_first_token_seconds_count` rises and keeps rising for
`--anchor-sustain` seconds (default 10). Startup delay cancels, because each run
anchors on its own first served token.

**You do not need to time anything.** `--start` and `--duration` define a search
window that only has to *contain* the run. Pad it generously.

**`--anchor-sustain` has an operational limit.** An anchor is confirmed only if
the request counter never stalls for more than half the sustain window. At the
default of 10s, that means a workload completing a request less often than once
per 5 seconds will never confirm an anchor, and `extract_run.py` fails outright
rather than guess at one. This is expected behavior for a low-concurrency ReAct
workload, not a bug: raise `--anchor-sustain` until it comfortably exceeds twice
the gap between completions, and re-run extraction.

## Data flow

    vLLM /metrics  (192.168.3.x:8000, native endpoint -- not logs)
          |  Prometheus PULLS every 1s, continuously
          v
    Prometheus on solab-p7  (15 day retention)
          |  extract_run.py queries query_range, any time afterwards
          v
    runs/<system>/<run-id>.csv  +  .json manifest

`extract_run.py` never contacts the GPU host. By extraction time that machine can
be powered off or running something else entirely.

## Prerequisites, before any experiment

1. The vLLM job must be scraped at `scrape_interval: 1s`. Prometheus cannot
   collect retroactively, so this must be in place *before* the runs. The local
   compose stack's `deploy/docker/prometheus/prometheus.yml` already has a
   `vllm-exporter` job at 1s; if you are pointing at a different Prometheus
   (e.g. `solab-p7`), confirm that instance's config matches.

   The job reads targets from `file_sd/local/vllm-exporter.yml` or
   `file_sd/remote/vllm-exporter.yml` (whichever `PROMETHEUS_FILE_SD_DIR`
   selects). Both ship with a placeholder host and `server: solab-x3` --
   **edit the real host:port and `server` label in before starting
   Prometheus**, or the job scrapes nothing and Prometheus starts healthy
   with zero targets. The `server` label must match `--target` below.
2. Retention is 15 days. **Extract each run the same day you run it** -- the CSV
   is the durable record, after which retention stops mattering.

## Running an experiment

1. Start one serving system and its ReAct workload. Let it run ~360 seconds.
2. Note roughly when you started. "Around 10am" is precise enough.
3. Extract:

       python3 extract_run.py --system mars --source prometheus \
           --prometheus-url http://solab-p7:9090 \
           --target solab-x3 \
           --model openai/gpt-oss-20b \
           --start 2026-08-01T09:55:00-07:00 --duration 900

   `--target` is required. Several vLLM hosts share one `file_sd` file, each
   carrying its own `server` label. Without `--target`, the query would
   aggregate across every host Prometheus knows about and report a latency
   describing no real machine -- and it would do this *silently*: the query
   still succeeds and produces a perfectly normal-looking number. Extraction
   aborts instead if the resolved window still spans more than one instance.

   `server` alone does not fully pin a query, either: a host serving two
   models, or running two engine processes, still matches once per
   `model_name` or `engine`. If the extracted window resolves to more than
   one time series for the same metric, extraction aborts with an error
   naming the metric and the distinct label sets found -- pass `--model` (as
   in the example above) or otherwise narrow the query, then re-run.

4. Repeat for `lmcache`, `mooncake`, `recompute`.

## Trying it without real data

The fixture scrapes are generated, not committed (2076 files, ~16MB, and fully
deterministic from the generator) -- generate them first, from
`benchmark/react_agent`:

    python3 -m fixtures.generate --out fixtures
    for s in mars lmcache mooncake recompute; do
      python3 extract_run.py --system "$s" --source fixture \
        --fixture "fixtures/$s" --target solab-x3
    done

## What is measured

The headline improvement uses **mean** TTFT (`rate(_sum)/rate(_count)`), which is
exact. p95 is captured in every CSV alongside the mean but is deliberately never
the headline and never plotted: vLLM's histogram buckets near this deployment's
p95 (~3.7s) are 2.5s wide, wide enough to swallow the effect being measured. A
p95 quoted from this histogram would be quantization noise, not a measurement.
See the design doc for the full argument.

Also captured, not charted: queue time, prefill time, external prefix cache hit
ratio, and recomputed prompt tokens. These decompose TTFT into queueing versus
prefill, which is what lets the result be defended.

**Empty CSV cells are meaningful -- do not fill them in.** An empty cell marks a
genuinely undefined value, not a missing zero: either a scrape gap, or (for
`ext_cache_hit_ratio` on `recompute`) a ratio that has no denominator because
`recompute` has no external KV cache to query. Treating either as `0` fabricates
a data point that was never measured.

## Grafana live dashboard

The Grafana dashboard is a self-contained presentation panel built with the
[HTML Graphics](https://grafana.com/grafana/plugins/gapit-htmlgraphics-panel/)
plugin. It reads Prometheus data directly (no iframes) and renders bar charts,
time-series plots, and improvement percentages in a single full-screen view.

### File structure

    grafana/
      hero-panel/
        panel.html          # markup
        panel.css           # styles
        panel.js            # chart rendering (onRender callback)
      dashboard-template.json.tmpl   # dashboard JSON with placeholders
      build-dashboard.py             # injects html/css/js into the template
      react-serving-benchmark.json   # generated output (do not edit directly)

### Making changes

Edit the files under `grafana/hero-panel/`, then rebuild:

    python3 grafana/build-dashboard.py

This writes `grafana/react-serving-benchmark.json`. Grafana's file provisioner
picks up changes within its `updateIntervalSeconds` (default 30s), or
immediately on container restart.

Use `--check` to verify the output is up to date without writing (useful in CI):

    python3 grafana/build-dashboard.py --check

### Viewing the presentation

Start the stack with `docker compose up`, then open in a browser:

    http://localhost:3000/d/react-serving-benchmark/react-serving-benchmark-comparison?orgId=1&from=now-5m&to=now&timezone=browser&refresh=5s&_dash.hideTimePicker=true&kiosk=true

Press **F11** (or your browser's fullscreen shortcut) for a clean, chrome-free
presentation view. The dashboard auto-refreshes every 5 seconds.

## Tests

    python3 -m pytest
