import csv
import importlib
from pathlib import Path

import pytest

COLUMNS = ["timestamp", "elapsed_seconds", "system", "ttft_mean_seconds",
           "e2e_mean_seconds", "ttft_p95_seconds", "e2e_p95_seconds",
           "requests_completed", "queue_p95_seconds", "prefill_p95_seconds",
           "ext_cache_hit_ratio", "prompt_tokens_recomputed"]

BASES = {"mars": 0.86, "lmcache": 1.13, "mooncake": 1.27, "recompute": 1.71}


def _write_runs(runs_dir: Path, gap_system: str | None, gap_elapsed: int | None) -> None:
    for system, base in BASES.items():
        directory = runs_dir / system
        directory.mkdir(parents=True)
        with (directory / "run.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for elapsed in range(0, 10):
                is_gap = system == gap_system and elapsed == gap_elapsed
                writer.writerow({
                    "timestamp": 1000 + elapsed,
                    "elapsed_seconds": elapsed,
                    "system": system,
                    # A missing scrape empties every derived metric's cell for
                    # that instant -- exactly what extract_run.py writes for a
                    # genuine gap, never a 0.
                    "ttft_mean_seconds": "" if is_gap else base,
                    "e2e_mean_seconds": "" if is_gap else base * 3,
                    "ttft_p95_seconds": "" if is_gap else base * 2.5,
                    "e2e_p95_seconds": "" if is_gap else base * 7,
                    "requests_completed": 100,
                    "queue_p95_seconds": base * 0.4,
                    "prefill_p95_seconds": base * 1.2,
                    "ext_cache_hit_ratio": "" if system == "recompute" else 0.7,
                    "prompt_tokens_recomputed": 5000,
                })


@pytest.fixture
def load_exporter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Write fixture runs, then import replay_exporter fresh against them.

    The module derives ``RUNS``/``DURATION`` at import time from env vars, so
    each test gets an isolated reload rather than reusing a cached module.
    """
    def _load(gap_system: str | None = None, gap_elapsed: int | None = None):
        _write_runs(tmp_path, gap_system, gap_elapsed)
        monkeypatch.setenv("BENCHMARK_RUNS_DIR", str(tmp_path))
        monkeypatch.setenv("BENCHMARK_RUN_SELECTION", "")
        monkeypatch.setenv("BENCHMARK_REPLAY_LOOP", "false")
        import replay_exporter
        importlib.reload(replay_exporter)
        return replay_exporter

    return _load


def test_current_mean_metric_is_exposed_separately_from_p95(load_exporter):
    exporter = load_exporter()
    exporter.PAUSED = True
    exporter.PAUSED_ELAPSED = 3.0

    output = exporter.render_metrics()

    assert "react_benchmark_ttft_current_mean_seconds" in output
    assert "react_benchmark_e2e_current_mean_seconds" in output
    mars_mean_line = next(
        line for line in output.splitlines()
        if line.startswith("react_benchmark_ttft_current_mean_seconds{")
        and 'system="mars"' in line
    )
    # base=0.86 -> ttft_mean_seconds column, not the p95 column (0.86*2.5=2.15).
    assert " 0.860000" in mars_mean_line


def test_current_mean_omitted_on_a_gap(load_exporter):
    exporter = load_exporter(gap_system="mars", gap_elapsed=5)
    exporter.PAUSED = True
    exporter.PAUSED_ELAPSED = 5.0

    output = exporter.render_metrics()

    mars_mean_lines = [
        line for line in output.splitlines()
        if line.startswith("react_benchmark_ttft_current_mean_seconds{")
        and 'system="mars"' in line
    ]
    assert mars_mean_lines == []


def test_render_metrics_survives_a_scrape_gap(load_exporter):
    # Before the fix, a single empty CSV cell (a real scrape gap) crashed the
    # whole exporter on every restart with "could not convert string to float".
    exporter = load_exporter(gap_system="mars", gap_elapsed=5)
    output = exporter.render_metrics()
    assert "react_benchmark_ttft_mean_seconds" in output


def test_gap_sample_is_omitted_not_fabricated(load_exporter):
    exporter = load_exporter(gap_system="mars", gap_elapsed=5)
    exporter.PAUSED = True
    exporter.PAUSED_ELAPSED = 5.0

    output = exporter.render_metrics()

    mars_p95_lines = [
        line for line in output.splitlines()
        if line.startswith("react_benchmark_ttft_p95_seconds{") and 'system="mars"' in line
    ]
    assert mars_p95_lines == [], "a scrape gap must be an absent sample, not a 0.0"


def test_other_systems_unaffected_by_one_systems_gap(load_exporter):
    exporter = load_exporter(gap_system="mars", gap_elapsed=5)
    exporter.PAUSED = True
    exporter.PAUSED_ELAPSED = 5.0

    output = exporter.render_metrics()

    lmcache_p95_lines = [
        line for line in output.splitlines()
        if line.startswith("react_benchmark_ttft_p95_seconds{") and 'system="lmcache"' in line
    ]
    assert len(lmcache_p95_lines) == 1


def test_mean_metric_skips_gap_rows(load_exporter):
    exporter = load_exporter(gap_system="mars", gap_elapsed=5)
    # 9 real samples at 2.15s (0.86 * 2.5) plus one gap must average to 2.15,
    # not be pulled toward 0 by treating the gap as a zero.
    assert exporter.mean_metric("mars", "ttft") == pytest.approx(0.86 * 2.5)


def test_improvement_fraction_omitted_when_either_side_is_a_gap(load_exporter):
    exporter = load_exporter(gap_system="mars", gap_elapsed=5)
    exporter.PAUSED = True
    exporter.PAUSED_ELAPSED = 5.0

    output = exporter.render_metrics()

    assert "react_benchmark_ttft_improvement_fraction" not in output
    # The mean-based percent metric has no notion of "current instant" and
    # must still be emitted even while the instantaneous fraction is not.
    assert "react_benchmark_ttft_improvement_percent" in output
