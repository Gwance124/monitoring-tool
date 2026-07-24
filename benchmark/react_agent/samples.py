"""Raw metric samples shared by every source adapter."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sample:
    """One scraped value: a metric name, its labels, and a timestamp."""

    timestamp: int
    metric: str
    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0


def select(samples: list[Sample], metric: str, **label_equals: str) -> list[Sample]:
    """Samples matching a metric name and every given label, timestamp-sorted."""
    matched = [
        sample
        for sample in samples
        if sample.metric == metric
        and all(sample.labels.get(key) == value for key, value in label_equals.items())
    ]
    return sorted(matched, key=lambda sample: sample.timestamp)


def distinct_label(samples: list[Sample], key: str) -> set[str]:
    """Every value observed for a label key."""
    return {sample.labels[key] for sample in samples if key in sample.labels}
