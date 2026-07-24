import csv
from pathlib import Path

import pytest

from replay import COLORS, SYSTEMS, Replay


def write_csv(runs_dir: Path, system: str, values, metric="ttft_mean_seconds"):
    directory = runs_dir / system
    directory.mkdir(parents=True, exist_ok=True)
    columns = ["timestamp", "elapsed_seconds", "system", "ttft_mean_seconds",
               "e2e_mean_seconds", "ttft_p95_seconds", "e2e_p95_seconds"]
    with (directory / "run.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for elapsed, value in values:
            row = {c: "" for c in columns}
            row.update({"timestamp": 1000 + elapsed, "elapsed_seconds": elapsed, "system": system})
            row[metric] = "" if value is None else value
            writer.writerow(row)


def four_systems(runs_dir: Path, bases=(1.0, 2.0, 3.0, 4.0)):
    for system, base in zip(SYSTEMS, bases):
        write_csv(runs_dir, system, [(e, base) for e in range(-60, 301)])


def test_colors_cover_every_system():
    assert set(COLORS) == set(SYSTEMS)
    assert COLORS["mars"] == "#3987e5"


def test_load_reads_all_four_systems(tmp_path: Path):
    four_systems(tmp_path)
    replay = Replay.load(tmp_path)
    assert set(replay.systems) == set(SYSTEMS)


def test_load_raises_when_a_system_is_missing(tmp_path: Path):
    write_csv(tmp_path, "mars", [(0, 1.0)])
    with pytest.raises(FileNotFoundError, match="lmcache"):
        Replay.load(tmp_path)


def test_window_returns_only_the_trailing_width(tmp_path: Path):
    four_systems(tmp_path)
    replay = Replay.load(tmp_path)
    xs, _ = replay.window("mars", "ttft_mean_seconds", at=120, width=60)
    assert xs[0] == 60
    assert xs[-1] == 120


def test_window_preserves_gaps_as_none(tmp_path: Path):
    write_csv(tmp_path, "mars", [(0, 1.0), (1, None), (2, 1.0)])
    for system in SYSTEMS[1:]:
        write_csv(tmp_path, system, [(0, 1.0), (1, 1.0), (2, 1.0)])
    replay = Replay.load(tmp_path)
    _, ys = replay.window("mars", "ttft_mean_seconds", at=2, width=60)
    assert ys[1] is None


def test_improvement_is_a_positive_fraction_when_faster(tmp_path: Path):
    four_systems(tmp_path, bases=(0.8, 2.0, 3.0, 1.0))
    replay = Replay.load(tmp_path)
    got = replay.improvement_vs("mars", "recompute", "ttft_mean_seconds", at=200)
    assert abs(got - 0.2) < 1e-6


def test_improvement_is_negative_when_slower(tmp_path: Path):
    four_systems(tmp_path, bases=(1.2, 2.0, 3.0, 1.0))
    replay = Replay.load(tmp_path)
    got = replay.improvement_vs("mars", "recompute", "ttft_mean_seconds", at=200)
    assert got < 0


def test_second_best_excludes_the_named_system(tmp_path: Path):
    four_systems(tmp_path, bases=(0.5, 1.0, 2.0, 3.0))
    replay = Replay.load(tmp_path)
    assert replay.second_best("mars", "ttft_mean_seconds", at=200) == "lmcache"


def test_duration_is_the_shortest_common_range(tmp_path: Path):
    four_systems(tmp_path)
    write_csv(tmp_path, "mooncake", [(e, 3.0) for e in range(-60, 200)])
    replay = Replay.load(tmp_path)
    assert replay.duration == 199
