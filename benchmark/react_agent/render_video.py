#!/usr/bin/env python3
"""Render the four aligned runs as a scrolling dashboard video.

Headless by construction: matplotlib's Agg backend, no display required. The
x-axis is a fixed trailing window that is already full at the first frame, so
the result reads as a live dashboard rather than a chart growing from an empty
axis. Every plotted point is measured -- the opening window is pre-filled with
the run's real warm-up segment, not synthesized.

Two output modes:
  * combined (default) -- one figure with both line panels, both bar panels,
    and the headline, written to ``--output``.
  * split (``--split``) -- five separate, time-synchronised videos (two line
    panels, two bar panels, the headline number) into an output directory, so
    the pieces can be composed into a presentation by hand.
"""

from __future__ import annotations

import argparse
import math
import pathlib
import shutil

import matplotlib

matplotlib.use("Agg")

import matplotlib.animation as animation
import matplotlib.pyplot as plt

from replay import COLORS, LABELS, SYSTEMS, Replay

SURFACE = "#1a1a19"
PANEL = "#232322"
INK = "#ffffff"
INK_MUTED = "#c3c2b7"
GRID = "#3a3a38"

# A regression must read differently from the expected improvement, but the
# headline still carries no series identity: this red is none of the four
# series colours.
REGRESSION_INK = "#e66767"


def _nice_ceiling(value: float) -> float:
    """Smallest 'round' number >= value, for stable axis ticks."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10 ** exponent
    for step in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if step * base >= value:
            return step * base
    return 10 * base


def _metric_max(replay: Replay, metric: str) -> float:
    """Largest measured value of ``metric`` across every system and second."""
    values = [
        value
        for system in SYSTEMS
        for value in (row.get(metric) for row in replay.series[system].values())
        if value is not None
    ]
    return max(values) if values else 1.0


def _interp(rows: dict[int, dict[str, float | None]], metric: str, x: float) -> float | None:
    """Value of ``metric`` at a possibly-fractional elapsed second ``x``.

    Linear interpolation between the two bracketing whole-second samples, used
    only to place the moving window's edges continuously. Returns None -- never
    a fabricated value -- if either bracketing sample is absent, so a scrape gap
    is never bridged and the edge simply has no point there.
    """
    lo = math.floor(x)
    hi = math.ceil(x)
    if lo == hi:
        return rows.get(lo, {}).get(metric)
    a = rows.get(lo, {}).get(metric)
    b = rows.get(hi, {}).get(metric)
    if a is None or b is None:
        return None
    return a + (b - a) * (x - lo)


def _window_interpolated(
    replay: Replay, system: str, metric: str, at: float, width: int
) -> tuple[list[float], list[float | None]]:
    """Trailing ``width`` seconds ending at fractional ``at``.

    Whole-second samples strictly inside the window, plus interpolated points at
    the exact moving edges so the line slides in and out of view smoothly rather
    than popping one second at a time. Gaps stay None.
    """
    rows = replay.series[system]
    left = at - width
    right = at
    xs: list[float] = []
    ys: list[float | None] = []

    left_value = _interp(rows, metric, left)
    if left_value is not None:
        xs.append(left)
        ys.append(left_value)

    for elapsed in range(math.floor(left) + 1, math.ceil(right)):
        if elapsed <= left or elapsed >= right:
            continue
        if elapsed in rows:
            xs.append(float(elapsed))
            ys.append(rows[elapsed].get(metric))

    right_value = _interp(rows, metric, right)
    if right_value is not None:
        xs.append(right)
        ys.append(right_value)

    return xs, ys


def compute_headline(
    replay: Replay, baseline: str, at: float
) -> tuple[str, str, str]:
    """(headline, colour, subline) for MARS versus ``baseline`` at ``at``."""
    improvement = replay.improvement_vs("mars", baseline, "ttft_mean_seconds", round(at))
    if improvement is None:
        return "", INK, ""
    colour = INK if improvement > 0 else REGRESSION_INK
    parts = [f"lower mean TTFT than {LABELS[baseline]}"]
    runner_up = replay.second_best("mars", "ttft_mean_seconds", round(at))
    if runner_up and runner_up != baseline:
        against = replay.improvement_vs("mars", runner_up, "ttft_mean_seconds", round(at))
        if against is not None:
            parts.append(f"{against * 100:.1f}% vs {LABELS[runner_up]}")
    return f"{improvement * 100:.1f}%", colour, "  ·  ".join(parts)


def _style_line_axis(axis, title: str) -> None:
    axis.set_facecolor(PANEL)
    axis.set_title(title, color=INK, fontsize=17, fontweight="bold", loc="left", pad=12)
    axis.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
    axis.set_axisbelow(True)
    axis.tick_params(colors=INK_MUTED, labelsize=12)
    for side, spine in axis.spines.items():
        spine.set_visible(side in ("left", "bottom"))
        spine.set_color(GRID)


class LinePanel:
    """One scrolling line panel (mean only), drawn on a supplied axis."""

    def __init__(self, replay: Replay, axis, prefix: str, title: str, window: int) -> None:
        self.replay = replay
        self.axis = axis
        self.prefix = prefix
        self.window = window
        self.metric = f"{prefix}_mean_seconds"

        _style_line_axis(axis, title)
        # Fixed y-range for the whole run: the axis ticks never reflow as the
        # window scrolls. Headroom comes from rounding the global max up.
        axis.set_ylim(0, _nice_ceiling(_metric_max(replay, self.metric)))

        self.lines: dict[tuple[str, str, str], plt.Line2D] = {}
        for system in SYSTEMS:
            width = 3.2 if system == "mars" else 2.0
            self.lines[(prefix, system, "mean")] = axis.plot(
                [], [], color=COLORS[system], linewidth=width,
                solid_capstyle="round", solid_joinstyle="round", zorder=3,
            )[0]

    def draw(self, at: float) -> None:
        at = float(at)
        for system in SYSTEMS:
            xs, ys = _window_interpolated(self.replay, system, self.metric, at, self.window)
            # None stays None: matplotlib breaks the line at NaN, so a scrape gap
            # reads as a gap instead of a bridged segment.
            plotted = [float("nan") if y is None else y for y in ys]
            self.lines[(self.prefix, system, "mean")].set_data(xs, plotted)
        self.axis.set_xlim(at - self.window, at)


class BarPanel:
    """One 'current value' horizontal bar panel, drawn on a supplied axis.

    All artists (bars, tick labels, value text) are created once here and
    mutated in place by ``draw()``. The previous implementation called
    ``axis.clear()`` every frame and rebuilt everything from scratch, which is
    far more expensive than updating a handful of existing artists.
    """

    def __init__(
        self, replay: Replay, axis, metric: str, title: str, span: float | None = None
    ) -> None:
        self.replay = replay
        self.axis = axis
        self.metric = metric
        self.title = title
        # Fixed x-range for the whole run so bar geometry and the value labels
        # do not jitter frame to frame.
        self.span = span if span is not None else _metric_max(replay, metric)
        span = self.span if self.span > 0 else 1.0
        self.no_data_stub = span * 0.03

        axis.set_facecolor(PANEL)
        axis.set_title(title, color=INK, fontsize=15, fontweight="bold", loc="left", pad=10)
        axis.tick_params(colors=INK_MUTED, labelsize=12, left=False)
        axis.get_xaxis().set_visible(False)
        for spine in axis.spines.values():
            spine.set_visible(False)
        axis.set_xlim(0, span * 1.28)

        self.systems = list(reversed(SYSTEMS))
        positions = list(range(len(self.systems)))
        axis.set_yticks(positions)
        axis.set_yticklabels([LABELS[system] for system in self.systems],
                             color=INK_MUTED, fontsize=13)

        self.bars = axis.barh(positions, [self.no_data_stub] * len(positions),
                              color=GRID, height=0.62, zorder=3)
        self.value_texts = [
            axis.text(self.no_data_stub + span * 0.03, position, "",
                      va="center", color=INK_MUTED, fontsize=13, fontweight="bold")
            for position in positions
        ]

    def draw(self, at: float) -> None:
        span = self.span if self.span > 0 else 1.0
        for system, bar, text in zip(self.systems, self.bars, self.value_texts):
            value = self.replay.value_at(system, self.metric, round(at))
            # Missing values get a short muted stub, not a fabricated 0.0 bar --
            # a real near-zero measurement must stay visually distinct from a gap.
            length = self.no_data_stub if value is None else value
            bar.set_width(length)
            bar.set_color(GRID if value is None else COLORS[system])
            text.set_x(length + span * 0.03)
            text.set_text("—" if value is None else f"{value:.2f}s")
            text.set_color(INK_MUTED if value is None else INK)


class HeadlinePanel:
    """The big improvement percentage plus its subline, drawn as figure text."""

    def __init__(
        self,
        replay: Replay,
        figure,
        baseline: str,
        big_xy: tuple[float, float],
        sub_xy: tuple[float, float],
        big_size: float,
        sub_size: float,
        ha: str = "center",
    ) -> None:
        self.replay = replay
        self.baseline = baseline
        self.big = figure.text(*big_xy, "", color=INK, fontsize=big_size,
                               fontweight="bold", ha=ha, va="center")
        self.sub = figure.text(*sub_xy, "", color=INK_MUTED, fontsize=sub_size,
                               ha=ha, va="center")

    def draw(self, at: float) -> None:
        headline, colour, subline = compute_headline(self.replay, self.baseline, at)
        self.big.set_text(headline)
        self.big.set_color(colour)
        self.sub.set_text(subline)


class Dashboard:
    """The combined figure. Artists are created once and reused across frames."""

    def __init__(self, replay: Replay, window: int = 60, baseline: str = "recompute") -> None:
        self.replay = replay
        self.window = window
        self.baseline = baseline

        self.figure = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor=SURFACE)
        grid = self.figure.add_gridspec(
            2, 2, width_ratios=[2.6, 1.0], height_ratios=[1, 1],
            left=0.055, right=0.975, top=0.86, bottom=0.09, hspace=0.28, wspace=0.14,
        )
        self.ttft_axis = self.figure.add_subplot(grid[0, 0])
        self.e2e_axis = self.figure.add_subplot(grid[1, 0])
        self.bar_ttft_axis = self.figure.add_subplot(grid[0, 1])
        self.bar_e2e_axis = self.figure.add_subplot(grid[1, 1])

        self.figure.text(0.055, 0.945, "ReAct AI agent serving benchmark",
                         color=INK, fontsize=30, fontweight="bold")
        self.figure.text(0.055, 0.905, "Four isolated runs aligned on first served token",
                         color=INK_MUTED, fontsize=16)

        self.headline_text = self.figure.text(
            0.70, 0.945, "", color=INK, fontsize=34, fontweight="bold"
        )
        self.subline_text = self.figure.text(0.70, 0.905, "", color=INK_MUTED, fontsize=14)
        self.clock_text = self.figure.text(
            0.975, 0.025, "", color=INK_MUTED, fontsize=15, ha="right"
        )

        badge = "FIXTURE DATA" if "fixture" in replay.classifications else "CAPTURED RUNS"
        self.figure.text(0.055, 0.025, badge, color=INK_MUTED, fontsize=12, fontweight="bold")

        self.ttft_panel = LinePanel(replay, self.ttft_axis, "ttft",
                                    "Time to first token (s)", window)
        self.e2e_panel = LinePanel(replay, self.e2e_axis, "e2e",
                                   "End-to-end request latency (s)", window)
        self.lines = {**self.ttft_panel.lines, **self.e2e_panel.lines}

        self.bar_ttft_panel = BarPanel(replay, self.bar_ttft_axis,
                                       "ttft_mean_seconds", "Current mean TTFT")
        self.bar_e2e_panel = BarPanel(replay, self.bar_e2e_axis,
                                      "e2e_mean_seconds", "Current mean end-to-end")

        self._build_legend()

    def _build_legend(self) -> None:
        from matplotlib.lines import Line2D

        handles = [
            Line2D([], [], color=COLORS[s], linewidth=3.0, label=LABELS[s])
            for s in SYSTEMS
        ]
        handles += [Line2D([], [], color=INK_MUTED, linewidth=2.5, label="mean")]
        legend = self.figure.legend(
            handles=handles, loc="lower center", ncol=6, frameon=False,
            bbox_to_anchor=(0.5, 0.0), fontsize=13,
        )
        for text in legend.get_texts():
            text.set_color(INK_MUTED)

    def draw(self, at: float) -> None:
        at = float(at)
        self.ttft_panel.draw(at)
        self.e2e_panel.draw(at)
        self.bar_ttft_panel.draw(at)
        self.bar_e2e_panel.draw(at)

        headline, colour, subline = compute_headline(self.replay, self.baseline, at)
        self.headline_text.set_text(headline)
        self.headline_text.set_color(colour)
        self.subline_text.set_text(subline)

        self.clock_text.set_text(f"t = {at:.0f}s / {self.replay.duration}s")


def _frames(replay: Replay, window: int, fps: int, speed: int) -> list[float]:
    first = replay.start + window
    last = replay.duration
    seconds_per_frame = speed / fps
    return [
        first + index * seconds_per_frame
        for index in range(int((last - first) / seconds_per_frame) + 1)
    ]


def _save(anim: animation.FuncAnimation, output: pathlib.Path, fps: int) -> pathlib.Path:
    """Write the animation, falling back to a GIF when ffmpeg is unavailable."""
    output = pathlib.Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg"):
        try:
            anim.save(str(output), writer=animation.FFMpegWriter(fps=fps, bitrate=6000))
            return output
        except Exception as error:
            # ffmpeg can be present on PATH but non-functional (e.g. a broken
            # dynamic-library dependency from an unrelated package upgrade).
            # That is an environment fault, not a reason to fail the render.
            print(f"ffmpeg failed ({error}); falling back to GIF")
    fallback = output.with_suffix(".gif")
    print(f"writing {fallback} instead")
    anim.save(str(fallback), writer=animation.PillowWriter(fps=fps))
    return fallback


def render(
    replay: Replay,
    output: pathlib.Path,
    fps: int = 20,
    speed: int = 2,
    window: int = 60,
) -> pathlib.Path:
    """Render the combined dashboard to a single video."""
    dashboard = Dashboard(replay, window=window)
    frames = _frames(replay, window, fps, speed)
    anim = animation.FuncAnimation(
        dashboard.figure, dashboard.draw, frames=frames, interval=1000 / fps, blit=False
    )
    result = _save(anim, pathlib.Path(output), fps)
    plt.close(dashboard.figure)
    return result


def _render_component(
    figure, draw, frames: list[float], output: pathlib.Path, fps: int
) -> pathlib.Path:
    anim = animation.FuncAnimation(
        figure, draw, frames=frames, interval=1000 / fps, blit=False
    )
    result = _save(anim, output, fps)
    plt.close(figure)
    return result


def render_split(
    replay: Replay,
    out_dir: pathlib.Path,
    fps: int = 20,
    speed: int = 2,
    window: int = 60,
    baseline: str = "recompute",
) -> list[pathlib.Path]:
    """Render each panel as its own time-synchronised video into ``out_dir``."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = _frames(replay, window, fps, speed)
    written: list[pathlib.Path] = []

    for prefix, title, name in (
        ("ttft", "Time to first token (s)", "ttft-line"),
        ("e2e", "End-to-end request latency (s)", "e2e-line"),
    ):
        figure = plt.figure(figsize=(12.8, 6.4), dpi=100, facecolor=SURFACE)
        axis = figure.add_axes([0.09, 0.12, 0.88, 0.80])
        panel = LinePanel(replay, axis, prefix, title, window)
        written.append(_render_component(figure, panel.draw, frames, out_dir / f"{name}.mp4", fps))

    for metric, title, name in (
        ("ttft_mean_seconds", "Current mean TTFT", "ttft-bars"),
        ("e2e_mean_seconds", "Current mean end-to-end", "e2e-bars"),
    ):
        figure = plt.figure(figsize=(7.2, 6.4), dpi=100, facecolor=SURFACE)
        axis = figure.add_axes([0.14, 0.08, 0.83, 0.84])
        panel = BarPanel(replay, axis, metric, title)
        written.append(_render_component(figure, panel.draw, frames, out_dir / f"{name}.mp4", fps))

    figure = plt.figure(figsize=(10.0, 3.2), dpi=100, facecolor=SURFACE)
    headline = HeadlinePanel(
        replay, figure, baseline,
        big_xy=(0.5, 0.64), sub_xy=(0.5, 0.26), big_size=92, sub_size=18,
    )
    written.append(_render_component(figure, headline.draw, frames, out_dir / "headline.mp4", fps))

    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=pathlib.Path,
                        default=pathlib.Path(__file__).with_name("runs"))
    parser.add_argument("--output", type=pathlib.Path, required=True,
                        help="output video file, or output directory when --split is set")
    parser.add_argument("--split", action="store_true",
                        help="write five separate, time-synchronised panel videos "
                             "into --output (treated as a directory) instead of one combined video")
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--speed", type=int, default=2,
                        help="benchmark seconds per video second")
    parser.add_argument("--window", type=int, default=60,
                        help="width of the scrolling window in seconds")
    args = parser.parse_args()

    replay = Replay.load(args.runs_dir)
    if args.split:
        for path in render_split(replay, args.output, args.fps, args.speed, args.window):
            print(path)
    else:
        print(render(replay, args.output, args.fps, args.speed, args.window))


if __name__ == "__main__":
    main()
