"""Source adapters. Both yield raw samples so downstream math is identical.

The fixture adapter reads recorded ``/metrics`` scrapes; the Prometheus adapter
queries a live server. Neither computes statistics -- that is ``metrics.py``'s
job -- so the fixture path exercises the same code as production.
"""

from __future__ import annotations

import math
import pathlib
import re

from samples import Sample

_LINE = re.compile(r"^(?P<metric>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)\s*$")
_LABEL = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')


def parse_exposition(text: str, timestamp: int) -> list[Sample]:
    """Parse one Prometheus text-exposition scrape into samples."""
    parsed: list[Sample] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if match is None:
            continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        if not math.isfinite(value):
            # A NaN or +/-Inf sample value is dropped here, not passed through.
            # metrics.rate() treats "current.value >= previous.value" as a
            # counter-reset check; every comparison against NaN is False, so a
            # NaN sample takes the reset branch and does `total += NaN`, which
            # poisons the running total for the rest of that rate() call while
            # `span` stays a valid positive number -- so rate() returns NaN
            # instead of None, and that NaN silently propagates through
            # mean_rate/histogram_quantile. Dropping the sample here instead
            # means the window simply holds one fewer point, which rate()
            # already handles by returning None when fewer than two remain.
            continue
        raw_labels = match.group("labels") or ""
        labels = {m.group("key"): m.group("value") for m in _LABEL.finditer(raw_labels)}
        parsed.append(
            Sample(
                timestamp=timestamp,
                metric=match.group("metric"),
                labels=labels,
                value=value,
            )
        )
    return parsed


class FixtureSource:
    """Recorded ``/metrics`` scrapes named ``<unix_timestamp>.prom``.

    Stamps a ``server`` label onto every sample. The raw endpoint never carries
    one -- Prometheus attaches it at scrape time from ``file_sd`` -- so
    synthesizing it here keeps both adapters interchangeable and lets target
    filtering be tested without a live server.
    """

    def __init__(self, fixture_dir: pathlib.Path, server: str) -> None:
        self.fixture_dir = pathlib.Path(fixture_dir)
        self.server = server

    def fetch(self, start: int, end: int) -> list[Sample]:
        collected: list[Sample] = []
        for path in sorted(self.fixture_dir.glob("*.prom")):
            try:
                timestamp = int(path.stem)
            except ValueError:
                continue
            if not start <= timestamp <= end:
                continue
            for sample in parse_exposition(path.read_text(encoding="utf-8"), timestamp):
                collected.append(
                    Sample(
                        timestamp=sample.timestamp,
                        metric=sample.metric,
                        labels={**sample.labels, "server": self.server},
                        value=sample.value,
                    )
                )
        return collected
