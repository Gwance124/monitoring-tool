"""Load the four extracted runs and align them on elapsed benchmark time."""

from __future__ import annotations

import csv
import glob
import json
import pathlib
import statistics
from dataclasses import dataclass

SYSTEMS = ("mars", "lmcache", "mooncake", "recompute")

LABELS = {
    "mars": "MARS",
    "lmcache": "LMCache",
    "mooncake": "Mooncake",
    "recompute": "Recompute",
}

# Fixed categorical order, validated for the dark surface #1a1a19:
# worst adjacent CVD deltaE 41.3. Colour encodes the system and nothing else.
COLORS = {
    "mars": "#3987e5",
    "lmcache": "#c98500",
    "mooncake": "#199e70",
    "recompute": "#9085e9",
}


@dataclass
class Replay:
    series: dict[str, dict[int, dict[str, float | None]]]
    classifications: set[str]

    @property
    def systems(self) -> tuple[str, ...]:
        return SYSTEMS

    @property
    def start(self) -> int:
        return max(min(rows) for rows in self.series.values())

    @property
    def duration(self) -> int:
        return min(max(rows) for rows in self.series.values())

    @classmethod
    def load(cls, runs_dir: pathlib.Path) -> "Replay":
        runs_dir = pathlib.Path(runs_dir)
        series: dict[str, dict[int, dict]] = {}
        classifications: set[str] = set()

        for system in SYSTEMS:
            candidates = sorted(glob.glob(str(runs_dir / system / "*.csv")))
            if not candidates:
                raise FileNotFoundError(f"No run CSV for {system} under {runs_dir}")
            path = pathlib.Path(candidates[-1])
            rows: dict[int, dict] = {}
            with path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    elapsed = int(row["elapsed_seconds"])
                    rows[elapsed] = {
                        key: (None if value == "" else float(value))
                        for key, value in row.items()
                        if key not in ("timestamp", "elapsed_seconds", "system")
                    }
            series[system] = rows

            manifest = path.with_suffix(".json")
            if manifest.exists():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                classifications.add(data.get("data_classification", "unknown"))

        return cls(series=series, classifications=classifications)

    def value_at(self, system: str, metric: str, at: int) -> float | None:
        return self.series[system].get(int(at), {}).get(metric)

    def window(
        self, system: str, metric: str, at: int, width: int
    ) -> tuple[list[float], list[float | None]]:
        """Trailing ``width`` seconds ending at ``at``. Gaps stay ``None``."""
        rows = self.series[system]
        xs, ys = [], []
        for elapsed in range(int(at) - width, int(at) + 1):
            if elapsed in rows:
                xs.append(float(elapsed))
                ys.append(rows[elapsed].get(metric))
        return xs, ys

    def _smoothed(self, system: str, metric: str, at: int, smooth: int) -> float | None:
        _, ys = self.window(system, metric, at, smooth)
        present = [y for y in ys if y is not None]
        return statistics.fmean(present) if present else None

    def improvement_vs(
        self, system: str, baseline: str, metric: str, at: int, smooth: int = 30
    ) -> float | None:
        """Fraction by which ``system`` beats ``baseline``; 0.18 means 18% lower.

        Both sides are smoothed over a trailing window so the headline does not
        strobe when two systems trade places.
        """
        mine = self._smoothed(system, metric, at, smooth)
        theirs = self._smoothed(baseline, metric, at, smooth)
        if mine is None or theirs is None or theirs == 0:
            return None
        return (theirs - mine) / theirs

    def second_best(
        self, exclude: str, metric: str, at: int, smooth: int = 30
    ) -> str | None:
        ranked = []
        for system in SYSTEMS:
            if system == exclude:
                continue
            value = self._smoothed(system, metric, at, smooth)
            if value is not None:
                ranked.append((value, system))
        return min(ranked)[1] if ranked else None
