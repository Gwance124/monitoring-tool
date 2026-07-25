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
          |
          v
    render_video.py  ->  outputs/react-serving-replay.mp4

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
    python3 render_video.py --output ../../outputs/react-serving-replay.mp4

If `ffmpeg` is missing or fails at render time (a broken system install, not a
project bug), `render_video.py` falls back to an animated GIF at the same path
with a `.gif` extension instead of `.mp4`, and prints which one it wrote. Check
the printed path, not just the exit code, to know which you got.

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

## Grafana

`replay_exporter.py` and `grafana/react-serving-benchmark.json` still work for
interactive viewing over an SSH tunnel. They are no longer the video path; the
renderer is headless and needs no browser.

## Tests

    python3 -m pytest
