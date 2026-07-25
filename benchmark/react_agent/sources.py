"""Source adapters. Both yield raw samples so downstream math is identical.

The fixture adapter reads recorded ``/metrics`` scrapes; the Prometheus adapter
queries a live server. Neither computes statistics -- that is ``metrics.py``'s
job -- so the fixture path exercises the same code as production.
"""

from __future__ import annotations

import math
import pathlib
import re

import requests

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


RAW_METRICS = (
    "vllm:time_to_first_token_seconds_bucket",
    "vllm:time_to_first_token_seconds_sum",
    "vllm:time_to_first_token_seconds_count",
    "vllm:e2e_request_latency_seconds_bucket",
    "vllm:e2e_request_latency_seconds_sum",
    "vllm:e2e_request_latency_seconds_count",
    "vllm:request_queue_time_seconds_bucket",
    "vllm:request_prefill_time_seconds_bucket",
    "vllm:external_prefix_cache_queries_total",
    "vllm:external_prefix_cache_hits_total",
    "vllm:prompt_tokens_recomputed_total",
    "vllm:request_success_total",
)


class PrometheusError(RuntimeError):
    """A query failed, or resolved to more than one serving host."""


class PrometheusSource:
    """Raw series from a Prometheus ``query_range``.

    Fetches counters and histogram buckets untouched -- no ``rate``, no
    ``histogram_quantile``. Derivation happens in ``metrics.py`` so the fixture
    path and this path run identical math.
    """

    _UNSAFE_SELECTOR_CHARS = ('"', "\\", "{", "}")

    def __init__(
        self,
        base_url: str,
        target: str,
        model: str | None = None,
        timeout: int = 30,
    ) -> None:
        if not target:
            raise ValueError(
                "A target host is required to pin every query to one vLLM "
                "instance; pass it with --target. An empty target builds a "
                "selector that matches no host and returns an empty result "
                "set -- indistinguishable from a successful empty window."
            )
        self._check_selector_safe("target", target)
        if model:
            self._check_selector_safe("model", model)
        self.base_url = base_url.rstrip("/")
        self.target = target
        self.model = model
        self.timeout = timeout

    @classmethod
    def _check_selector_safe(cls, field: str, value: str) -> None:
        for char in cls._UNSAFE_SELECTOR_CHARS:
            if char in value:
                raise ValueError(
                    f"{field}={value!r} contains {char!r}, which would break "
                    "out of the PromQL label selector it is interpolated "
                    "into. A legitimate hostname or model id does not "
                    "contain this character."
                )

    def _selector(self) -> str:
        parts = [f'server="{self.target}"']
        if self.model:
            parts.append(f'model_name="{self.model}"')
        return "{" + ",".join(parts) + "}"

    def fetch(self, start: int, end: int, step: int = 1) -> list[Sample]:
        """Fetch raw samples for every metric in ``RAW_METRICS`` over ``[start, end]``.

        Raises:
            PrometheusError: for a Prometheus-level problem -- a query that
                returned a non-"success" status, or a window that resolves to
                more than one serving host.
            requests.exceptions.RequestException: transport and HTTP failures
                (timeouts, connection errors, non-2xx responses via
                ``raise_for_status``) propagate unchanged from ``requests``;
                they are not wrapped as ``PrometheusError``.
        """
        selector = self._selector()
        collected: list[Sample] = []
        seen_servers: set[str] = set()
        # Per metric, the set of distinct identifying label sets seen so far,
        # excluding `le` (histogram bucket boundaries are expected to vary
        # within one series; every other label is not).
        label_sets_by_metric: dict[str, set[tuple[tuple[str, str], ...]]] = {}

        for metric in RAW_METRICS:
            payload = self._query_range(f"{metric}{selector}", start, end, step)
            for stream in payload:
                labels = {k: v for k, v in stream["metric"].items() if k != "__name__"}
                if "server" in labels:
                    seen_servers.add(labels["server"])
                identity = tuple(sorted((k, v) for k, v in labels.items() if k != "le"))
                label_sets_by_metric.setdefault(metric, set()).add(identity)
                for timestamp, value in stream["values"]:
                    try:
                        parsed_value = float(value)
                    except ValueError:
                        continue
                    if not math.isfinite(parsed_value):
                        # Same guard as parse_exposition: a NaN reaching
                        # metrics.rate() takes the counter-reset branch (every
                        # comparison against NaN is False), poisoning the
                        # running total so rate() returns nan instead of None.
                        continue
                    collected.append(
                        Sample(
                            timestamp=int(timestamp),
                            metric=metric,
                            labels=labels,
                            value=parsed_value,
                        )
                    )

        # Kept as defense-in-depth, but the selector already pins `server`, so
        # every returned series carries exactly that value in practice -- this
        # branch cannot fire against a real Prometheus. The check below is the
        # one that actually catches an unpinned dimension (typically
        # `model_name` or `engine`).
        extra = seen_servers - {self.target}
        if extra:
            raise PrometheusError(
                "Window resolves to more than one vLLM host: "
                f"{sorted(seen_servers)}. Unexpected: {sorted(extra)}. "
                "Aggregating across hosts would yield a latency figure "
                "describing no real server."
            )

        for metric, label_sets in label_sets_by_metric.items():
            if len(label_sets) > 1:
                found = [dict(labels) for labels in sorted(label_sets)]
                raise PrometheusError(
                    f"{metric!r} resolves to more than one time series within "
                    f"this window: {found}. `server` alone does not uniquely "
                    "identify a series -- `model_name` or `engine` (or another "
                    "label) also varies here. Aggregating across them would "
                    "merge unrelated series under one metric name and produce "
                    "a statistic describing neither. Pin the missing dimension "
                    "(e.g. pass --model) and re-run."
                )

        return collected

    def _query_range(self, query: str, start: int, end: int, step: int) -> list[dict]:
        response = requests.get(
            f"{self.base_url}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success":
            raise PrometheusError(f"Query failed: {query!r}: {body.get('error')}")
        return body["data"]["result"]
