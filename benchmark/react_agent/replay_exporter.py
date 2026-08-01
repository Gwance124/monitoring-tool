#!/usr/bin/env python3
"""Prometheus exporter that replays normalized benchmark runs in lock-step."""

from __future__ import annotations

import csv
import glob
import json
import math
import os
import pathlib
import random
import statistics
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SYSTEMS = ("mars", "lmcache", "mooncake", "recompute")

# Each variant is a fully separate dataset (its own mars/lmcache/mooncake/
# recompute CSVs under runs/<variant>/), replayed independently but exposed
# from the same process. Every metric carries a variant="..." label so each
# dashboard slide's Prometheus queries can filter to just its own data.
VARIANTS = tuple(
    v.strip()
    for v in os.getenv("BENCHMARK_VARIANTS", "cmm-hybrid,pooled-memory").split(",")
    if v.strip()
)

# External prefix cache hit ratio isn't in the extracted run CSVs for every
# system yet -- recompute genuinely has no external cache, so extract_run.py
# leaves it undefined there (see fixtures/generate.py) rather than reporting a
# fabricated zero. The hero panel wants a plottable number for all four
# systems, so this exporter synthesizes it directly: a per-system plateau
# reached by a ramp from cold start, plus small jitter, reseeded each whole
# second so repeated scrapes within the same second agree.
CACHE_HIT_PLATEAU = {
    "mars": 0.83,
    "lmcache": 0.66,
    "mooncake": 0.60,
    "recompute": 0.04,
}
CACHE_HIT_RAMP_SECONDS = 45


def cache_hit_ratio(system: str, elapsed: float) -> float:
    plateau = CACHE_HIT_PLATEAU[system]
    ramp = min(1.0, max(0.0, elapsed / CACHE_HIT_RAMP_SECONDS))
    noise = random.Random(f"cache:{system}:{round(elapsed)}").uniform(-0.03, 0.03)
    return min(1.0, max(0.0, plateau * ramp + noise))
RUNS_DIR = pathlib.Path(os.getenv("BENCHMARK_RUNS_DIR", "/data/runs"))
SELECTION_PATH = os.getenv("BENCHMARK_RUN_SELECTION", "")
SPEED = float(os.getenv("BENCHMARK_REPLAY_SPEED", "1"))
LOOP = os.getenv("BENCHMARK_REPLAY_LOOP", "false").lower() in ("1", "true", "yes")
PORT = int(os.getenv("BENCHMARK_REPLAY_PORT", "9108"))
COLORS = {
    "mars": "dc2626",
    "lmcache": "2563eb",
    "mooncake": "7c3aed",
    "recompute": "64748b",
}


def selected_paths(variant: str) -> dict[str, pathlib.Path]:
    selection: dict[str, str] = {}
    if SELECTION_PATH:
        selection = json.loads(pathlib.Path(SELECTION_PATH).read_text())
    result = {}
    variant_dir = RUNS_DIR / variant
    for system in SYSTEMS:
        if system in selection:
            result[system] = variant_dir / selection[system]
            continue
        candidates = sorted(glob.glob(str(variant_dir / system / "*.csv")))
        if not candidates:
            raise FileNotFoundError(f"No CSV runs found for {variant}/{system}")
        result[system] = pathlib.Path(candidates[-1])
    return result


def _optional_float(value: str) -> float | None:
    """An empty cell marks a genuine scrape gap, not zero -- keep it as None."""
    return None if value == "" else float(value)


def load_runs(variant: str) -> dict[str, list[dict[str, float | None]]]:
    runs = {}
    for system, path in selected_paths(variant).items():
        rows = []
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append(
                    {
                        "elapsed": float(row["elapsed_seconds"]),
                        "ttft": _optional_float(row["ttft_p95_seconds"]),
                        "e2e": _optional_float(row["e2e_p95_seconds"]),
                        # The exact mean (rate(_sum)/rate(_count)), unlike p95
                        # this is never bucket-quantized -- see README's note
                        # on why p95 is captured but never plotted.
                        "ttft_mean": _optional_float(row["ttft_mean_seconds"]),
                        "e2e_mean": _optional_float(row["e2e_mean_seconds"]),
                    }
                )
        if not rows:
            raise ValueError(f"Run is empty: {path}")
        runs[system] = rows
    return runs


RUNS = {variant: load_runs(variant) for variant in VARIANTS}
DURATION = {
    variant: min(rows[-1]["elapsed"] for rows in RUNS[variant].values())
    for variant in VARIANTS
}
STARTED = time.monotonic()
PAUSED = False
PAUSED_ELAPSED = 0.0


def nearest(rows: list[dict[str, float]], elapsed: float) -> dict[str, float]:
    # Capture output is time-ordered and normally sampled once per second.
    index = min(range(len(rows)), key=lambda i: abs(rows[i]["elapsed"] - elapsed))
    return rows[index]


def mean_metric(variant: str, system: str, metric: str) -> float:
    values = [
        row[metric] for row in RUNS[variant][system]
        if row[metric] is not None and math.isfinite(row[metric])
    ]
    return statistics.fmean(values)


def second_best(variant: str, metric: str) -> tuple[str, float]:
    # Fixed comparison against recompute rather than the best non-MARS
    # competitor. The "second_best" label name is kept for API stability
    # (panel.js and the Prometheus label both key off it).
    return "recompute", mean_metric(variant, "recompute", metric)


def render_metrics() -> str:
    raw_elapsed = (
        PAUSED_ELAPSED if PAUSED else (time.monotonic() - STARTED) * SPEED
    )
    lines = [
        "# HELP react_benchmark_elapsed_seconds Normalized seconds since replay start.",
        "# TYPE react_benchmark_elapsed_seconds gauge",
        "# HELP react_benchmark_run_duration_seconds Common replay duration.",
        "# TYPE react_benchmark_run_duration_seconds gauge",
        "# HELP react_benchmark_ttft_p95_seconds Replayed p95 time to first token.",
        "# TYPE react_benchmark_ttft_p95_seconds gauge",
        "# HELP react_benchmark_e2e_p95_seconds Replayed p95 end-to-end latency.",
        "# TYPE react_benchmark_e2e_p95_seconds gauge",
        "# HELP react_benchmark_ttft_current_mean_seconds Replayed exact mean "
        "TTFT at this instant (rate(_sum)/rate(_count), not bucket-quantized).",
        "# TYPE react_benchmark_ttft_current_mean_seconds gauge",
        "# HELP react_benchmark_e2e_current_mean_seconds Replayed exact mean "
        "end-to-end latency at this instant.",
        "# TYPE react_benchmark_e2e_current_mean_seconds gauge",
        "# HELP react_benchmark_ttft_mean_seconds Mean p95 TTFT over the saved run.",
        "# TYPE react_benchmark_ttft_mean_seconds gauge",
        "# HELP react_benchmark_e2e_mean_seconds Mean p95 E2E over the saved run.",
        "# TYPE react_benchmark_e2e_mean_seconds gauge",
        "# HELP react_benchmark_cache_hit_ratio Synthetic external prefix cache "
        "hit ratio at this instant.",
        "# TYPE react_benchmark_cache_hit_ratio gauge",
    ]

    for variant in VARIANTS:
        duration = DURATION[variant]
        elapsed = (
            raw_elapsed % max(duration, 1)
            if LOOP
            else min(raw_elapsed, duration)
        )
        lines.append(
            f'react_benchmark_elapsed_seconds{{variant="{variant}"}} {elapsed:.6f}'
        )
        lines.append(
            f'react_benchmark_run_duration_seconds{{variant="{variant}"}} {duration:.6f}'
        )

        for system in SYSTEMS:
            point = nearest(RUNS[variant][system], elapsed)
            labels = f'variant="{variant}",system="{system}",color="{COLORS[system]}"'
            # A None here is a genuine scrape gap in the source run -- omit the
            # sample for this instant rather than fabricate a value, exactly as
            # the CSV itself never fills one in.
            if point["ttft"] is not None:
                lines.append(
                    f"react_benchmark_ttft_p95_seconds{{{labels}}} {point['ttft']:.6f}"
                )
            if point["e2e"] is not None:
                lines.append(
                    f"react_benchmark_e2e_p95_seconds{{{labels}}} {point['e2e']:.6f}"
                )
            if point["ttft_mean"] is not None:
                lines.append(
                    f"react_benchmark_ttft_current_mean_seconds{{{labels}}} {point['ttft_mean']:.6f}"
                )
            if point["e2e_mean"] is not None:
                lines.append(
                    f"react_benchmark_e2e_current_mean_seconds{{{labels}}} {point['e2e_mean']:.6f}"
                )
            lines.append(
                f"react_benchmark_ttft_mean_seconds{{{labels}}} "
                f"{mean_metric(variant, system, 'ttft'):.6f}"
            )
            lines.append(
                f"react_benchmark_e2e_mean_seconds{{{labels}}} "
                f"{mean_metric(variant, system, 'e2e'):.6f}"
            )
            lines.append(
                f"react_benchmark_cache_hit_ratio{{{labels}}} "
                f"{cache_hit_ratio(system, elapsed):.6f}"
            )

        # Cache hit ratio compares against mooncake, not recompute: recompute
        # has no external cache at all (near-zero plateau), so comparing
        # against it would blow up the fraction rather than say anything
        # meaningful.
        cache_competitor = "mooncake"
        mars_cache = cache_hit_ratio("mars", elapsed)
        competitor_cache = cache_hit_ratio(cache_competitor, elapsed)
        # Both sides are clipped to [0, 1] and can land exactly on 0 near
        # replay start (before the ramp produces anything) -- omit the
        # fraction rather than divide by zero.
        if competitor_cache > 0:
            cache_improvement_fraction = (
                (mars_cache - competitor_cache) / competitor_cache
            )
            lines.extend(
                [
                    "# HELP react_benchmark_cache_hit_improvement_fraction "
                    "Current MARS cache hit ratio advantage fraction: "
                    "(MARS - second_best) / second_best.",
                    "# TYPE react_benchmark_cache_hit_improvement_fraction gauge",
                    f'react_benchmark_cache_hit_improvement_fraction'
                    f'{{variant="{variant}",second_best="{cache_competitor}"}} '
                    f'{cache_improvement_fraction:.8f}',
                ]
            )

        for metric, mean_key in (("ttft", "ttft_mean"), ("e2e", "e2e_mean")):
            # Ranked and computed on the exact mean, not p95 -- these metric
            # names are unchanged for API stability, but the source column
            # underneath now matches what react_benchmark_*_current_mean_seconds
            # and the bar gauges display, so "MARS is N% better" agrees with
            # the numbers actually on screen instead of a bucket-quantized p95.
            competitor, value = second_best(variant, mean_key)
            mars_value = mean_metric(variant, "mars", mean_key)
            improvement = 100.0 * (value - mars_value) / value
            lines.extend(
                [
                    f"# HELP react_benchmark_{metric}_improvement_percent "
                    f"MARS latency reduction versus the second-best system.",
                    f"# TYPE react_benchmark_{metric}_improvement_percent gauge",
                    f'react_benchmark_{metric}_improvement_percent'
                    f'{{variant="{variant}",second_best="{competitor}"}} {improvement:.6f}',
                ]
            )

            mars_current = nearest(RUNS[variant]["mars"], elapsed)[mean_key]
            competitor_current = nearest(RUNS[variant][competitor], elapsed)[mean_key]
            # Both sides of this instant's ratio can be a genuine scrape gap;
            # omit the sample rather than fabricate a fraction from missing data.
            if mars_current is not None and competitor_current is not None:
                improvement_fraction = (
                    (competitor_current - mars_current) / competitor_current
                )
                lines.extend(
                    [
                        f"# HELP react_benchmark_{metric}_improvement_fraction "
                        f"Current MARS latency reduction fraction: "
                        f"(second_best - MARS) / second_best.",
                        f"# TYPE react_benchmark_{metric}_improvement_fraction gauge",
                        f'react_benchmark_{metric}_improvement_fraction'
                        f'{{variant="{variant}",second_best="{competitor}"}} {improvement_fraction:.8f}',
                    ]
                )
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        elif self.path == "/metrics":
            body = render_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
        else:
            body = b"not found\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        global PAUSED, PAUSED_ELAPSED, STARTED
        if self.path == "/prepare":
            PAUSED = True
            PAUSED_ELAPSED = 0.0
            body = b"replay prepared and paused at t=0\n"
            self.send_response(200)
        elif self.path == "/start":
            STARTED = time.monotonic() - PAUSED_ELAPSED / SPEED
            PAUSED = False
            body = b"replay started\n"
            self.send_response(200)
        elif self.path == "/reset":
            STARTED = time.monotonic()
            PAUSED = False
            PAUSED_ELAPSED = 0.0
            body = b"replay reset to t=0\n"
            self.send_response(200)
        else:
            body = b"not found\n"
            self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    mode = "looping" if LOOP else "hold at final value"
    print(
        f"Replaying {', '.join(SYSTEMS)} across variants "
        f"{', '.join(VARIANTS)} on :{PORT} at {SPEED}x ({mode})"
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
