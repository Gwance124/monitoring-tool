import csv
import json
import sys
from pathlib import Path

import pytest

from extract_run import CSV_COLUMNS, build_rows, main, write_run
from fixtures.generate import write_fixture
from metrics import find_anchor
from samples import select
from sources import FixtureSource

START = 1784912400


def load(system, tmp_path, seconds=520):
    directory = tmp_path / system
    write_fixture(system, directory, start=START, seconds=seconds, seed=1)
    return FixtureSource(directory, server="solab-x3").fetch(START, START + seconds)


def rows_for(system, tmp_path):
    got = load(system, tmp_path)
    anchor = find_anchor(select(got, "vllm:time_to_first_token_seconds_count"), sustain=10)
    return build_rows(
        got, anchor=anchor, interval=1, duration=300, warmup=60,
        server="solab-x3", system=system,
    )


def test_elapsed_runs_from_negative_warmup_to_duration(tmp_path: Path):
    rows = rows_for("mars", tmp_path)
    assert rows[0]["elapsed_seconds"] == -60
    assert rows[-1]["elapsed_seconds"] == 300


def test_benchmark_zero_is_warmup_seconds_after_the_anchor(tmp_path: Path):
    got = load("mars", tmp_path)
    anchor = find_anchor(select(got, "vllm:time_to_first_token_seconds_count"), sustain=10)
    rows = rows_for("mars", tmp_path)
    zero = [r for r in rows if r["elapsed_seconds"] == 0][0]
    assert zero["timestamp"] == anchor + 60


def test_every_column_is_present_in_order(tmp_path: Path):
    rows = rows_for("mars", tmp_path)
    assert tuple(rows[0].keys()) == CSV_COLUMNS


def test_mean_is_populated_and_plausible(tmp_path: Path):
    rows = rows_for("mars", tmp_path)
    means = [r["ttft_mean_seconds"] for r in rows if r["ttft_mean_seconds"] is not None]
    assert means, "no mean TTFT computed"
    assert all(0.05 < value < 20.0 for value in means)


def test_recompute_cache_ratio_is_none_not_zero(tmp_path: Path):
    rows = rows_for("recompute", tmp_path)
    assert all(r["ext_cache_hit_ratio"] is None for r in rows)


def test_cache_ratio_is_populated_for_a_caching_system(tmp_path: Path):
    rows = rows_for("mars", tmp_path)
    ratios = [r["ext_cache_hit_ratio"] for r in rows if r["ext_cache_hit_ratio"] is not None]
    assert ratios
    assert all(0.0 <= value <= 1.0 for value in ratios)


def test_none_is_written_as_an_empty_cell(tmp_path: Path):
    rows = rows_for("recompute", tmp_path)
    path = write_run(rows, {"system": "recompute"}, tmp_path / "runs", "recompute", "r1", force=False)
    with path.open(encoding="utf-8", newline="") as handle:
        first = next(csv.DictReader(handle))
    assert first["ext_cache_hit_ratio"] == ""


def test_write_run_emits_a_manifest(tmp_path: Path):
    rows = rows_for("mars", tmp_path)
    manifest = {"system": "mars", "anchor": 123, "data_classification": "fixture"}
    path = write_run(rows, manifest, tmp_path / "runs", "mars", "r1", force=False)
    loaded = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert loaded["data_classification"] == "fixture"


def test_write_run_refuses_to_overwrite_without_force(tmp_path: Path):
    rows = rows_for("mars", tmp_path)
    write_run(rows, {}, tmp_path / "runs", "mars", "r1", force=False)
    with pytest.raises(FileExistsError):
        write_run(rows, {}, tmp_path / "runs", "mars", "r1", force=False)


def test_write_run_overwrites_with_force(tmp_path: Path):
    rows = rows_for("mars", tmp_path)
    write_run(rows, {}, tmp_path / "runs", "mars", "r1", force=False)
    path = write_run(rows, {}, tmp_path / "runs", "mars", "r1", force=True)
    assert path.exists()


def test_missing_anchor_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="anchor"):
        build_rows([], anchor=None, interval=1, duration=300, warmup=60,
                   server="solab-x3", system="mars")


def test_queue_and_prefill_p95_are_populated(tmp_path: Path):
    rows = rows_for("mars", tmp_path)
    queue = [r["queue_p95_seconds"] for r in rows if r["queue_p95_seconds"] is not None]
    prefill = [r["prefill_p95_seconds"] for r in rows if r["prefill_p95_seconds"] is not None]
    assert queue, "no queue p95 computed"
    assert prefill, "no prefill p95 computed"
    assert all(isinstance(v, (int, float)) and v > 0 for v in queue)
    assert all(isinstance(v, (int, float)) and v > 0 for v in prefill)


def test_fixture_manifest_does_not_claim_an_applied_model_filter(tmp_path: Path, monkeypatch):
    system = "mars"
    fixture_dir = tmp_path / "fixture"
    write_fixture(system, fixture_dir, start=START, seconds=400, seed=1)
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "extract_run.py",
            "--system", system,
            "--source", "fixture",
            "--target", "solab-x3",
            "--fixture", str(fixture_dir),
            "--model", "openai/gpt-oss-20b",
            "--benchmark-duration", "50",
            "--warmup", "60",
            "--runs-dir", str(runs_dir),
            "--run-id", "r1",
        ],
    )
    main()
    manifest = json.loads((runs_dir / system / "r1.json").read_text(encoding="utf-8"))
    # FixtureSource never filters by model, so a "model" field would read as
    # an applied filter that never happened.
    assert "model" not in manifest
