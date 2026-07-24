#!/usr/bin/env python3
"""Extract one isolated benchmark run into a normalized CSV.

Prometheus records vLLM continuously, so this runs whenever convenient after an
experiment -- it never has to be started alongside one. ``--start`` and
``--duration`` define a search window that merely has to contain the run; the
true ``t=0`` is detected from the first sustained rise in served tokens.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib

import metrics
from samples import Sample, select
from sources import FixtureSource, PrometheusSource

CSV_COLUMNS = (
    "timestamp",
    "elapsed_seconds",
    "system",
    "ttft_mean_seconds",
    "e2e_mean_seconds",
    "ttft_p95_seconds",
    "e2e_p95_seconds",
    "requests_completed",
    "queue_p95_seconds",
    "prefill_p95_seconds",
    "ext_cache_hit_ratio",
    "prompt_tokens_recomputed",
)

RATE_WINDOW = 30


def _buckets(samples: list[Sample], metric: str, server: str) -> dict[float, list[Sample]]:
    """Group a histogram's bucket samples by their ``le`` upper bound."""
    grouped: dict[float, list[Sample]] = {}
    for sample in select(samples, metric, server=server):
        raw = sample.labels.get("le")
        if raw is None:
            continue
        bound = float("inf") if raw in ("+Inf", "Inf") else float(raw)
        grouped.setdefault(bound, []).append(sample)
    return grouped


def _last_value(samples: list[Sample], metric: str, server: str, at: int) -> float | None:
    candidates = [s for s in select(samples, metric, server=server) if s.timestamp <= at]
    return candidates[-1].value if candidates else None


def derive_row(samples: list[Sample], at: int, window: int, server: str) -> dict:
    """Every derived metric at one instant. Unavailable values are ``None``."""
    ttft_sum = select(samples, "vllm:time_to_first_token_seconds_sum", server=server)
    ttft_count = select(samples, "vllm:time_to_first_token_seconds_count", server=server)
    e2e_sum = select(samples, "vllm:e2e_request_latency_seconds_sum", server=server)
    e2e_count = select(samples, "vllm:e2e_request_latency_seconds_count", server=server)

    queries = select(samples, "vllm:external_prefix_cache_queries_total", server=server)
    hits = select(samples, "vllm:external_prefix_cache_hits_total", server=server)
    query_rate = metrics.rate(queries, at, window)
    hit_rate = metrics.rate(hits, at, window)
    if query_rate is None or hit_rate is None or query_rate == 0:
        # An absent external KV store has an undefined hit ratio, not a zero one.
        hit_ratio = None
    else:
        hit_ratio = hit_rate / query_rate

    return {
        "ttft_mean_seconds": metrics.mean_rate(ttft_sum, ttft_count, at, window),
        "e2e_mean_seconds": metrics.mean_rate(e2e_sum, e2e_count, at, window),
        "ttft_p95_seconds": metrics.histogram_quantile(
            0.95, _buckets(samples, "vllm:time_to_first_token_seconds_bucket", server), at, window
        ),
        "e2e_p95_seconds": metrics.histogram_quantile(
            0.95, _buckets(samples, "vllm:e2e_request_latency_seconds_bucket", server), at, window
        ),
        "requests_completed": _last_value(samples, "vllm:request_success_total", server, at),
        "queue_p95_seconds": metrics.histogram_quantile(
            0.95, _buckets(samples, "vllm:request_queue_time_seconds_bucket", server), at, window
        ),
        "prefill_p95_seconds": metrics.histogram_quantile(
            0.95, _buckets(samples, "vllm:request_prefill_time_seconds_bucket", server), at, window
        ),
        "ext_cache_hit_ratio": hit_ratio,
        "prompt_tokens_recomputed": _last_value(
            samples, "vllm:prompt_tokens_recomputed_total", server, at
        ),
    }


def build_rows(
    samples: list[Sample],
    anchor: int | None,
    interval: int,
    duration: int,
    warmup: int,
    server: str,
    system: str,
) -> list[dict]:
    """One row per interval from ``-warmup`` through ``duration``.

    ``t=0`` is ``anchor + warmup``: the leading segment pre-fills the scrolling
    window with real data and is excluded from headline statistics as warm-up.
    """
    if anchor is None:
        raise ValueError(
            "No anchor found: the TTFT counter never rose and sustained inside "
            "the search window. Widen --start/--duration, or confirm the run "
            "actually served traffic."
        )

    zero = anchor + warmup
    rows = []
    for elapsed in range(-warmup, duration + 1, interval):
        at = zero + elapsed
        row = {"timestamp": at, "elapsed_seconds": elapsed, "system": system}
        row.update(derive_row(samples, at, RATE_WINDOW, server))
        rows.append({column: row.get(column) for column in CSV_COLUMNS})
    return rows


def write_run(
    rows: list[dict],
    manifest: dict,
    runs_dir: pathlib.Path,
    system: str,
    run_id: str,
    force: bool,
) -> pathlib.Path:
    """Write ``<runs_dir>/<system>/<run_id>.csv`` and its JSON manifest."""
    directory = pathlib.Path(runs_dir) / system
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.csv"
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to replace it")

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: ("" if value is None else value)
                    for key, value in row.items()
                }
            )
    path.with_suffix(".json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", required=True, choices=("mars", "lmcache", "mooncake", "recompute"))
    parser.add_argument("--source", required=True, choices=("fixture", "prometheus"))
    parser.add_argument("--target", required=True, help="value of the vLLM server label, e.g. solab-x3")
    parser.add_argument("--fixture", type=pathlib.Path, help="fixture directory (--source fixture)")
    parser.add_argument("--prometheus-url", help="e.g. http://solab-p7:9090 (--source prometheus)")
    parser.add_argument("--model", help="optional model_name filter")
    parser.add_argument("--start", help="ISO-8601 search window start (--source prometheus)")
    parser.add_argument("--duration", type=int, default=900, help="search window length in seconds")
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--benchmark-duration", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--anchor-sustain", type=int, default=10)
    parser.add_argument("--runs-dir", type=pathlib.Path, default=pathlib.Path(__file__).with_name("runs"))
    parser.add_argument("--run-id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.source == "fixture":
        if not args.fixture:
            parser.error("--fixture is required with --source fixture")
        stems = sorted(int(p.stem) for p in args.fixture.glob("*.prom"))
        if not stems:
            parser.error(f"no .prom scrapes found in {args.fixture}")
        start, end = stems[0], stems[-1]
        source = FixtureSource(args.fixture, server=args.target)
        classification = "fixture"
    else:
        if not (args.prometheus_url and args.start):
            parser.error("--prometheus-url and --start are required with --source prometheus")
        start = int(dt.datetime.fromisoformat(args.start).timestamp())
        end = start + args.duration
        source = PrometheusSource(args.prometheus_url, target=args.target, model=args.model)
        classification = "captured"

    samples = source.fetch(start, end)
    if not samples:
        raise SystemExit(
            f"No samples in {start}..{end} for server={args.target!r}. "
            "Most likely the vLLM scrape job was not running during that window."
        )

    counts = select(samples, "vllm:time_to_first_token_seconds_count", server=args.target)
    anchor = metrics.find_anchor(counts, sustain=args.anchor_sustain)
    if anchor is None:
        raise SystemExit(
            f"No anchor found in {start}..{end}. The TTFT counter never rose and "
            f"sustained for {args.anchor_sustain}s. Widen the window, or confirm "
            "the run served traffic."
        )

    rows = build_rows(
        samples, anchor, args.interval, args.benchmark_duration,
        args.warmup, args.target, args.system,
    )
    run_id = args.run_id or dt.datetime.fromtimestamp(anchor, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    truncated = max(s.timestamp for s in samples) < anchor + args.warmup + args.benchmark_duration
    manifest = {
        "schema_version": 2,
        "system": args.system,
        "run_id": run_id,
        "server": args.target,
        "model": args.model,
        "source": args.source,
        "data_classification": classification,
        "search_window": [start, end],
        "anchor_unix": anchor,
        "benchmark_zero_unix": anchor + args.warmup,
        "warmup_seconds": args.warmup,
        "benchmark_duration_seconds": args.benchmark_duration,
        "interval_seconds": args.interval,
        "anchor_sustain_seconds": args.anchor_sustain,
        "rate_window_seconds": RATE_WINDOW,
        "sample_count": len(rows),
        "truncated": truncated,
    }
    if truncated:
        print("WARNING: run ends before the requested benchmark duration; manifest marks it truncated")
    print(write_run(rows, manifest, args.runs_dir, args.system, run_id, args.force))


if __name__ == "__main__":
    main()
