import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import pytest

from render_video import (
    Dashboard,
    _interp,
    _window_interpolated,
    render,
    render_split,
)
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


def test_line_y_axis_is_fixed_across_frames(runs: Path):
    dashboard = Dashboard(Replay.load(runs), window=60)
    dashboard.draw(at=0)
    early = dashboard.ttft_axis.get_ylim()
    dashboard.draw(at=90)
    late = dashboard.ttft_axis.get_ylim()
    assert early == late, "y-limits must not reflow as the window scrolls"


def test_bar_x_axis_is_fixed_across_frames(runs: Path):
    dashboard = Dashboard(Replay.load(runs), window=60)
    dashboard.draw(at=0)
    early = dashboard.bar_ttft_axis.get_xlim()
    dashboard.draw(at=90)
    late = dashboard.bar_ttft_axis.get_xlim()
    assert early == late, "bar x-limits must not reflow frame to frame"


def test_line_edges_track_fractional_time(runs: Path):
    # A fractional frame must place the line's right edge exactly at `at`, not
    # snap it to a whole second -- this is what makes scrolling smooth.
    dashboard = Dashboard(Replay.load(runs), window=60)
    dashboard.draw(at=90.4)
    line = dashboard.lines[("ttft", "mars", "mean")]
    xs = line.get_xdata()
    assert xs[-1] == pytest.approx(90.4)
    assert xs[0] == pytest.approx(30.4)


def test_interpolation_never_bridges_a_gap():
    rows = {10: {"m": 1.0}, 12: {"m": 3.0}}  # 11 is missing
    # Every fractional x here brackets the missing sample at 11, so the value is
    # undefined -- interpolation must not invent 2.0 to bridge it.
    assert _interp(rows, "m", 11.5) is None
    assert _interp(rows, "m", 10.5) is None


def test_interpolation_between_present_samples():
    rows = {10: {"m": 1.0}, 11: {"m": 2.0}}
    assert _interp(rows, "m", 10.25) == pytest.approx(1.25)
    assert _interp(rows, "m", 10.0) == pytest.approx(1.0)


def test_window_edge_point_dropped_when_edge_falls_in_a_gap(runs: Path):
    replay = Replay.load(runs)
    # Punch a hole so the right edge at t=100.5 brackets a missing sample.
    del replay.series["mars"][100]
    xs, ys = _window_interpolated(replay, "mars", "ttft_mean_seconds", 100.5, 60)
    assert xs[-1] < 100.5, "no fabricated point at an edge sitting in a gap"


def test_render_split_writes_five_synchronised_videos(runs: Path, tmp_path: Path):
    out = tmp_path / "split"
    paths = render_split(Replay.load(runs), out, fps=5, speed=20, window=60)
    assert len(paths) == 5
    names = {p.stem for p in paths}
    assert names == {"ttft-line", "e2e-line", "ttft-bars", "e2e-bars", "headline"}
    for path in paths:
        assert path.exists() and path.stat().st_size > 0


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
