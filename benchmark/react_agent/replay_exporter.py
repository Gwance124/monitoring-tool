#!/usr/bin/env python3
"""Prometheus exporter that replays normalized benchmark runs in lock-step."""

from __future__ import annotations

import csv
import glob
import json
import math
import os
import pathlib
import statistics
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SYSTEMS = ("mars", "lmcache", "mooncake", "recompute")
RUNS_DIR = pathlib.Path(os.getenv("BENCHMARK_RUNS_DIR", "/data/runs"))
SELECTION_PATH = os.getenv("BENCHMARK_RUN_SELECTION", "")
SPEED = float(os.getenv("BENCHMARK_REPLAY_SPEED", "1"))
LOOP = os.getenv("BENCHMARK_REPLAY_LOOP", "false").lower() in ("1", "true", "yes")
PORT = int(os.getenv("BENCHMARK_REPLAY_PORT", "9108"))
COLORS = {
    "mars": "14b8a6",
    "recompute": "2563eb",
    "mooncake": "8b5cf6",
    "lmcache": "f59e0b",
}


def selected_paths() -> dict[str, pathlib.Path]:
    selection: dict[str, str] = {}
    if SELECTION_PATH:
        selection = json.loads(pathlib.Path(SELECTION_PATH).read_text())
    result = {}
    for system in SYSTEMS:
        if system in selection:
            result[system] = RUNS_DIR / selection[system]
            continue
        candidates = sorted(glob.glob(str(RUNS_DIR / system / "*.csv")))
        if not candidates:
            raise FileNotFoundError(f"No CSV runs found for {system}")
        result[system] = pathlib.Path(candidates[-1])
    return result


def _optional_float(value: str) -> float | None:
    """An empty cell marks a genuine scrape gap, not zero -- keep it as None."""
    return None if value == "" else float(value)


def load_runs() -> dict[str, list[dict[str, float | None]]]:
    runs = {}
    for system, path in selected_paths().items():
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


RUNS = load_runs()
DURATION = min(rows[-1]["elapsed"] for rows in RUNS.values())
STARTED = time.monotonic()
PAUSED = False
PAUSED_ELAPSED = 0.0


def nearest(rows: list[dict[str, float]], elapsed: float) -> dict[str, float]:
    # Capture output is time-ordered and normally sampled once per second.
    index = min(range(len(rows)), key=lambda i: abs(rows[i]["elapsed"] - elapsed))
    return rows[index]


def mean_metric(system: str, metric: str) -> float:
    values = [
        row[metric] for row in RUNS[system]
        if row[metric] is not None and math.isfinite(row[metric])
    ]
    return statistics.fmean(values)


def second_best(metric: str) -> tuple[str, float]:
    # "Second best" is the best non-MARS competitor when MARS leads. If MARS
    # does not lead, the negative percentage makes that regression explicit.
    ranking = sorted(
        (mean_metric(system, metric), system)
        for system in SYSTEMS
        if system != "mars"
    )
    return ranking[0][1], ranking[0][0]


def render_metrics() -> str:
    raw_elapsed = (
        PAUSED_ELAPSED if PAUSED else (time.monotonic() - STARTED) * SPEED
    )
    elapsed = (
        raw_elapsed % max(DURATION, 1)
        if LOOP
        else min(raw_elapsed, DURATION)
    )
    lines = [
        "# HELP react_benchmark_elapsed_seconds Normalized seconds since replay start.",
        "# TYPE react_benchmark_elapsed_seconds gauge",
        f"react_benchmark_elapsed_seconds {elapsed:.6f}",
        "# HELP react_benchmark_run_duration_seconds Common replay duration.",
        "# TYPE react_benchmark_run_duration_seconds gauge",
        f"react_benchmark_run_duration_seconds {DURATION:.6f}",
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
    ]
    for system in SYSTEMS:
        point = nearest(RUNS[system], elapsed)
        labels = f'system="{system}",color="{COLORS[system]}"'
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
            f"{mean_metric(system, 'ttft'):.6f}"
        )
        lines.append(
            f"react_benchmark_e2e_mean_seconds{{{labels}}} "
            f"{mean_metric(system, 'e2e'):.6f}"
        )

    for metric in ("ttft", "e2e"):
        competitor, value = second_best(metric)
        mars_value = mean_metric("mars", metric)
        improvement = 100.0 * (value - mars_value) / value
        lines.extend(
            [
                f"# HELP react_benchmark_{metric}_improvement_percent "
                f"MARS latency reduction versus the second-best system.",
                f"# TYPE react_benchmark_{metric}_improvement_percent gauge",
                f'react_benchmark_{metric}_improvement_percent'
                f'{{second_best="{competitor}"}} {improvement:.6f}',
            ]
        )

        mars_current = nearest(RUNS["mars"], elapsed)[metric]
        competitor_current = nearest(RUNS[competitor], elapsed)[metric]
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
                    f'{{second_best="{competitor}"}} {improvement_fraction:.8f}',
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
    print(f"Replaying {', '.join(SYSTEMS)} on :{PORT} at {SPEED}x ({mode})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
