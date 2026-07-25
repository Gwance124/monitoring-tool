import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import pytest

from render_video import Dashboard, render
from replay import SYSTEMS, Replay

COLUMNS = ["timestamp", "elapsed_seconds", "system", "ttft_mean_seconds",
           "e2e_mean_seconds", "ttft_p95_seconds", "e2e_p95_seconds",
           "requests_completed", "queue_p95_seconds", "prefill_p95_seconds",
           "ext_cache_hit_ratio", "prompt_tokens_recomputed"]


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    bases = {"mars": 0.86, "lmcache": 1.13, "mooncake": 1.27, "recompute": 1.71}
    for system, base in bases.items():
        directory = tmp_path / system
        directory.mkdir(parents=True)
        with (directory / "run.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for elapsed in range(-60, 121):
                writer.writerow({
                    "timestamp": 1000 + elapsed,
                    "elapsed_seconds": elapsed,
                    "system": system,
                    "ttft_mean_seconds": base,
                    "e2e_mean_seconds": base * 3,
                    "ttft_p95_seconds": base * 2.5,
                    "e2e_p95_seconds": base * 7,
                    "requests_completed": 100,
                    "queue_p95_seconds": base * 0.4,
                    "prefill_p95_seconds": base * 1.2,
                    "ext_cache_hit_ratio": "" if system == "recompute" else 0.7,
                    "prompt_tokens_recomputed": 5000,
                })
    return tmp_path


def test_dashboard_draws_without_error(runs: Path):
    dashboard = Dashboard(Replay.load(runs))
    dashboard.draw(at=60)


def test_first_frame_window_is_already_full(runs: Path):
    replay = Replay.load(runs)
    dashboard = Dashboard(replay, window=60)
    dashboard.draw(at=0)
    left, right = dashboard.ttft_axis.get_xlim()
    assert right - left == pytest.approx(60)
    line = dashboard.ttft_axis.get_lines()[0]
    assert len(line.get_xdata()) > 50, "opening frame should be pre-filled"


def test_x_window_scrolls(runs: Path):
    dashboard = Dashboard(Replay.load(runs), window=60)
    dashboard.draw(at=0)
    first = dashboard.ttft_axis.get_xlim()
    dashboard.draw(at=60)
    second = dashboard.ttft_axis.get_xlim()
    assert second[0] > first[0]
    assert second[1] - second[0] == pytest.approx(first[1] - first[0])


def test_every_system_is_drawn_in_its_assigned_colour(runs: Path):
    from replay import COLORS

    dashboard = Dashboard(Replay.load(runs))
    dashboard.draw(at=60)
    drawn = {line.get_color() for line in dashboard.ttft_axis.get_lines()}
    for system in SYSTEMS:
        assert COLORS[system] in drawn


def test_headline_is_computed_against_recompute(runs: Path):
    dashboard = Dashboard(Replay.load(runs), baseline="recompute")
    dashboard.draw(at=60)
    # (1.71 - 0.86) / 1.71 = 0.497
    assert "49.7%" in dashboard.headline_text.get_text()


def test_render_writes_a_file(runs: Path, tmp_path: Path):
    output = tmp_path / "out.mp4"
    result = render(Replay.load(runs), output, fps=5, speed=10, window=60)
    assert result.exists()
    assert result.stat().st_size > 0


def test_line_panels_draw_only_mean_no_p95(runs: Path):
    dashboard = Dashboard(Replay.load(runs), window=60)
    dashboard.draw(at=60)
    assert set(dashboard.lines.keys()) == {
        (prefix, system, "mean")
        for prefix in ("ttft", "e2e")
        for system in SYSTEMS
    }
    assert len(dashboard.ttft_axis.get_lines()) == len(SYSTEMS)


def test_legend_has_no_orphaned_dashed_entry(runs: Path):
    dashboard = Dashboard(Replay.load(runs), window=60)
    labels = {handle.get_label() for handle in dashboard.figure.legends[0].legend_handles}
    assert "p95" not in labels
    assert "mean" in labels


def test_missing_bar_value_renders_as_no_data_not_zero(runs: Path, tmp_path: Path):
    replay = Replay.load(runs)
    dashboard = Dashboard(replay, window=60)

    original_value_at = replay.value_at
    def patched(system, metric, at):
        if system == "recompute" and metric == "ttft_mean_seconds":
            return None
        return original_value_at(system, metric, at)
    replay.value_at = patched

    dashboard.draw(at=60)
    axis = dashboard.bar_ttft_axis
    texts = [text.get_text() for text in axis.texts]
    assert "—" in texts, "missing value should render as an em dash, not a number"
    assert "0.00s" not in texts
