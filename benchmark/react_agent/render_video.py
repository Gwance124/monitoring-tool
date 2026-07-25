#!/usr/bin/env python3
"""Render the four aligned runs as a scrolling dashboard video.

Headless by construction: matplotlib's Agg backend, no display required. The
x-axis is a fixed trailing window that is already full at the first frame, so
the result reads as a live dashboard rather than a chart growing from an empty
axis. Every plotted point is measured -- the opening window is pre-filled with
the run's real warm-up segment, not synthesized.
"""

from __future__ import annotations

import argparse
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


class Dashboard:
    """Draws one frame. Reused across frames so artists are created once."""

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

        self._style_line_axis(self.ttft_axis, "Time to first token (s)")
        self._style_line_axis(self.e2e_axis, "End-to-end request latency (s)")
        self._build_legend()

        self.lines: dict[tuple[str, str, str], plt.Line2D] = {}
        for axis, prefix in ((self.ttft_axis, "ttft"), (self.e2e_axis, "e2e")):
            for system in SYSTEMS:
                width = 3.2 if system == "mars" else 2.0
                self.lines[(prefix, system, "mean")] = axis.plot(
                    [], [], color=COLORS[system], linewidth=width,
                    solid_capstyle="round", zorder=3,
                )[0]

    def _style_line_axis(self, axis, title: str) -> None:
        axis.set_facecolor(PANEL)
        axis.set_title(title, color=INK, fontsize=17, fontweight="bold", loc="left", pad=12)
        axis.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
        axis.set_axisbelow(True)
        axis.tick_params(colors=INK_MUTED, labelsize=12)
        for side, spine in axis.spines.items():
            spine.set_visible(side in ("left", "bottom"))
            spine.set_color(GRID)

    def _style_bar_axis(self, axis, title: str) -> None:
        axis.clear()
        axis.set_facecolor(PANEL)
        axis.set_title(title, color=INK, fontsize=15, fontweight="bold", loc="left", pad=10)
        axis.tick_params(colors=INK_MUTED, labelsize=12, left=False)
        axis.get_xaxis().set_visible(False)
        for spine in axis.spines.values():
            spine.set_visible(False)

    def _build_legend(self) -> None:
        from matplotlib.lines import Line2D

        handles = [
            Line2D([], [], color=COLORS[s], linewidth=3.0, label=LABELS[s])
            for s in SYSTEMS
        ]
        handles += [
            Line2D([], [], color=INK_MUTED, linewidth=2.5, label="mean"),
        ]
        legend = self.figure.legend(
            handles=handles, loc="lower center", ncol=6, frameon=False,
            bbox_to_anchor=(0.5, 0.0), fontsize=13,
        )
        for text in legend.get_texts():
            text.set_color(INK_MUTED)

    def _draw_bars(self, axis, metric: str, title: str, at: float) -> None:
        self._style_bar_axis(axis, title)
        values, labels, colors = [], [], []
        for system in reversed(SYSTEMS):
            value = self.replay.value_at(system, metric, round(at))
            values.append(value)
            labels.append(LABELS[system])
            colors.append(COLORS[system])

        positions = range(len(values))
        measured = [value for value in values if value is not None]
        span = max(measured) if measured else 1.0
        span = span if span > 0 else 1.0
        # Missing values get a short muted stub, not a fabricated 0.0 bar --
        # a real near-zero measurement must stay visually distinct from a gap.
        NO_DATA_STUB = span * 0.03
        bar_lengths = [NO_DATA_STUB if value is None else value for value in values]
        bar_colors = [GRID if value is None else color for value, color in zip(values, colors)]
        axis.barh(list(positions), bar_lengths, color=bar_colors, height=0.62, zorder=3)
        axis.set_yticks(list(positions))
        axis.set_yticklabels(labels, color=INK_MUTED, fontsize=13)
        axis.set_xlim(0, span * 1.28)
        for position, value, length in zip(positions, values, bar_lengths):
            label = "—" if value is None else f"{value:.2f}s"
            color = INK_MUTED if value is None else INK
            axis.text(length + span * 0.03, position, label,
                      va="center", color=color, fontsize=13, fontweight="bold")

    def draw(self, at: float) -> None:
        at = float(at)
        for axis, prefix in ((self.ttft_axis, "ttft"), (self.e2e_axis, "e2e")):
            highest = 0.0
            for system in SYSTEMS:
                xs, ys = self.replay.window(system, f"{prefix}_mean_seconds", round(at), self.window)
                # None stays None: matplotlib breaks the line at NaN, so a
                # scrape gap reads as a gap instead of a bridged segment.
                plotted = [float("nan") if y is None else y for y in ys]
                self.lines[(prefix, system, "mean")].set_data(xs, plotted)
                finite = [y for y in ys if y is not None]
                if finite:
                    highest = max(highest, max(finite))
            axis.set_xlim(at - self.window, at)
            axis.set_ylim(0, highest * 1.18 if highest else 1.0)

        self._draw_bars(self.bar_ttft_axis, "ttft_mean_seconds", "Current mean TTFT", at)
        self._draw_bars(self.bar_e2e_axis, "e2e_mean_seconds", "Current mean end-to-end", at)

        improvement = self.replay.improvement_vs(
            "mars", self.baseline, "ttft_mean_seconds", round(at)
        )
        if improvement is None:
            self.headline_text.set_text("")
            self.subline_text.set_text("")
        else:
            self.headline_text.set_text(f"{improvement * 100:.1f}%")
            # Text carries no series identity: ink for the expected-positive
            # case, a distinct non-series red for a regression that must read
            # differently. "#e66767" is not among the four series colors.
            self.headline_text.set_color(INK if improvement > 0 else "#e66767")
            runner_up = self.replay.second_best("mars", "ttft_mean_seconds", round(at))
            parts = [f"lower mean TTFT than {LABELS[self.baseline]}"]
            if runner_up and runner_up != self.baseline:
                against = self.replay.improvement_vs(
                    "mars", runner_up, "ttft_mean_seconds", round(at)
                )
                if against is not None:
                    parts.append(f"{against * 100:.1f}% vs {LABELS[runner_up]}")
            self.subline_text.set_text("  ·  ".join(parts))

        self.clock_text.set_text(f"t = {at:.0f}s / {self.replay.duration}s")


def render(
    replay: Replay,
    output: pathlib.Path,
    fps: int = 20,
    speed: int = 2,
    window: int = 60,
) -> pathlib.Path:
    """Write the animation, falling back to a GIF when ffmpeg is unavailable."""
    output = pathlib.Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    dashboard = Dashboard(replay, window=window)
    first = replay.start + window
    last = replay.duration
    seconds_per_frame = speed / fps
    frames = [
        first + index * seconds_per_frame
        for index in range(int((last - first) / seconds_per_frame) + 1)
    ]

    anim = animation.FuncAnimation(
        dashboard.figure, dashboard.draw, frames=frames, interval=1000 / fps, blit=False
    )

    if shutil.which("ffmpeg"):
        try:
            anim.save(str(output), writer=animation.FFMpegWriter(fps=fps, bitrate=6000))
            plt.close(dashboard.figure)
            return output
        except Exception as error:
            # ffmpeg can be present on PATH but non-functional (e.g. a broken
            # dynamic-library dependency from an unrelated package upgrade).
            # That is an environment fault, not a reason to fail the render.
            print(f"ffmpeg failed ({error}); falling back to GIF")

    fallback = output.with_suffix(".gif")
    print(f"writing {fallback} instead")
    anim.save(str(fallback), writer=animation.PillowWriter(fps=fps))
    plt.close(dashboard.figure)
    return fallback


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=pathlib.Path,
                        default=pathlib.Path(__file__).with_name("runs"))
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--speed", type=int, default=2,
                        help="benchmark seconds per video second")
    parser.add_argument("--window", type=int, default=60,
                        help="width of the scrolling window in seconds")
    args = parser.parse_args()
    print(render(Replay.load(args.runs_dir), args.output, args.fps, args.speed, args.window))


if __name__ == "__main__":
    main()
