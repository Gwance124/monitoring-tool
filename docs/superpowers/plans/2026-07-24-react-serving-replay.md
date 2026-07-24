# ReAct Serving Benchmark Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn four separately-run vLLM serving experiments (MARS, LMCache, Mooncake, recompute) into one overlaid comparison video that reads like a live dashboard.

**Architecture:** Prometheus already records vLLM's `/metrics` continuously, so extraction is a retro-query decoupled from experiment time. Both source adapters return *raw* counter samples and all statistics are computed in Python, so the fixture path exercises the same math as production. Runs are aligned by detecting each run's first served token rather than by wall-clock start.

**Tech Stack:** Python 3.11, `requests`, `matplotlib` (Agg backend), `ffmpeg` (with Pillow GIF fallback), `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-24-react-serving-replay-design.md`

## Global Constraints

- Python 3.11. Standard library plus `requests`, `matplotlib`, `Pillow`, `pytest` only.
- All code under `benchmark/react_agent/`. Tests under `benchmark/react_agent/tests/`.
- Never fabricate data points. Gaps stay gaps; missing values are empty CSV cells, never interpolated and never zero.
- Every Prometheus query is pinned to one host via `{server="$TARGET"}`. Extraction aborts if the window still resolves to more than one instance.
- Headline improvement is computed from **mean** TTFT (`rate(_sum)/rate(_count)`), never p95.
- Series colors, fixed order, dark surface `#1a1a19`:
  `mars=#3987e5`, `lmcache=#c98500`, `mooncake=#199e70`, `recompute=#9085e9`.
  Validated: worst adjacent CVD ΔE 41.3. Do not substitute colors without re-running
  `scripts/validate_palette.js` from the `dataviz` skill.
- Color encodes system; line style encodes metric (mean solid, p95 dashed). Never encode a system by line style.
- Reference distribution from the real snapshot, used in assertions:
  `count=3140`, `sum=4476.2018349170685`, `mean=1.426s`, `p95≈3.687s`.

---

## File Structure

| File | Responsibility |
|---|---|
| `benchmark/react_agent/samples.py` | `Sample` dataclass + `SampleSeries` selection helpers |
| `benchmark/react_agent/metrics.py` | Pure math: counter `rate`, `histogram_quantile`, `mean_rate`, anchor detection |
| `benchmark/react_agent/sources.py` | `FixtureSource` and `PrometheusSource`, both yielding raw `Sample`s |
| `benchmark/react_agent/extract_run.py` | CLI: fetch → anchor → derive → resample → CSV + manifest |
| `benchmark/react_agent/replay.py` | Load the four CSVs, align on `elapsed_seconds`, expose `frame_at(t)` |
| `benchmark/react_agent/render_video.py` | matplotlib Agg scrolling dashboard → mp4 |
| `benchmark/react_agent/fixtures/generate.py` | Build synthetic fixture snapshots from the real `/metrics` capture |

---

## Task 1: Clean out the dead recording paths and scaffold tests

**Files:**
- Delete: `benchmark/react_agent/record_grafana_video.mjs`
- Delete: `benchmark/react_agent/recorder/` (entire directory, including vendored `node_modules`)
- Delete: `benchmark/react_agent/encode_frames.swift`
- Delete: `benchmark/react_agent/capture_run.py`
- Delete: `benchmark/react_agent/generate_demo_data.py`
- Delete: `benchmark/react_agent/render_replay_video.py`
- Delete: `benchmark/react_agent/__pycache__/`
- Create: `benchmark/react_agent/tests/__init__.py` (empty file)
- Create: `benchmark/react_agent/pytest.ini`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `pytest` invocation for all later tasks.

Keep `replay_exporter.py` and `grafana/react-serving-benchmark.json` — they still serve Grafana over an SSH tunnel. Keep `runs/` and `assets/`.

- [ ] **Step 1: Delete the browser and macOS-only recording paths**

```bash
cd /Users/gwance/Workspace/Github/work/monitoring-tool
git rm -r --ignore-unmatch --cached benchmark/react_agent/recorder 2>/dev/null || true
rm -rf benchmark/react_agent/recorder
rm -f benchmark/react_agent/record_grafana_video.mjs
rm -f benchmark/react_agent/encode_frames.swift
rm -f benchmark/react_agent/capture_run.py
rm -f benchmark/react_agent/generate_demo_data.py
rm -f benchmark/react_agent/render_replay_video.py
rm -rf benchmark/react_agent/__pycache__
```

- [ ] **Step 2: Verify only the intended files remain**

Run: `ls benchmark/react_agent/`
Expected exactly: `README.md  assets  grafana  replay_exporter.py  runs`

- [ ] **Step 3: Create the test package marker**

Create `benchmark/react_agent/tests/__init__.py` as an empty file:

```bash
mkdir -p benchmark/react_agent/tests
touch benchmark/react_agent/tests/__init__.py
```

- [ ] **Step 4: Create pytest config**

Create `benchmark/react_agent/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q
```

- [ ] **Step 5: Verify pytest runs and collects nothing**

Run: `cd benchmark/react_agent && python3 -m pytest`
Expected: `no tests ran` (exit code 5). This confirms discovery works.

- [ ] **Step 6: Commit**

```bash
git add -A benchmark/react_agent
git commit -m "Remove browser and macOS-only recording paths

The Playwright recorder and Swift encoder cannot run on a headless Linux
server, which is where this benchmark is captured. capture_run.py,
generate_demo_data.py, and render_replay_video.py are superseded by the
extraction and rendering pipeline in the accompanying spec.

Keeps replay_exporter.py and the Grafana dashboard, which remain usable over
an SSH tunnel."
```

---

## Task 2: Sample model

**Files:**
- Create: `benchmark/react_agent/samples.py`
- Test: `benchmark/react_agent/tests/test_samples.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Sample(timestamp: int, metric: str, labels: dict[str, str], value: float)` — frozen dataclass.
  - `select(samples: list[Sample], metric: str, **label_equals: str) -> list[Sample]` — filter by metric name and exact label matches, returned sorted by `timestamp`.
  - `distinct_label(samples: list[Sample], key: str) -> set[str]` — every value seen for a label key.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_samples.py`:

```python
import pytest

from samples import Sample, distinct_label, select


def _s(ts, metric, value, **labels):
    return Sample(timestamp=ts, metric=metric, labels=labels, value=value)


def test_select_filters_by_metric_name():
    samples = [
        _s(10, "vllm:ttft_count", 5.0),
        _s(10, "vllm:e2e_count", 7.0),
    ]
    got = select(samples, "vllm:ttft_count")
    assert [s.value for s in got] == [5.0]


def test_select_filters_by_label_equality():
    samples = [
        _s(10, "m", 1.0, server="solab-x3"),
        _s(10, "m", 2.0, server="solab-x9"),
    ]
    got = select(samples, "m", server="solab-x3")
    assert [s.value for s in got] == [1.0]


def test_select_returns_timestamp_sorted():
    samples = [_s(30, "m", 3.0), _s(10, "m", 1.0), _s(20, "m", 2.0)]
    assert [s.timestamp for s in select(samples, "m")] == [10, 20, 30]


def test_select_ignores_samples_missing_the_label():
    samples = [_s(10, "m", 1.0), _s(10, "m", 2.0, server="solab-x3")]
    assert [s.value for s in select(samples, "m", server="solab-x3")] == [2.0]


def test_distinct_label_collects_every_value():
    samples = [
        _s(10, "m", 1.0, server="solab-x3"),
        _s(11, "m", 2.0, server="solab-x9"),
        _s(12, "m", 3.0, server="solab-x3"),
    ]
    assert distinct_label(samples, "server") == {"solab-x3", "solab-x9"}


def test_sample_is_frozen():
    s = _s(10, "m", 1.0)
    with pytest.raises(Exception):
        s.value = 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_samples.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'samples'`

- [ ] **Step 3: Write the implementation**

Create `benchmark/react_agent/samples.py`:

```python
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
```

`Sample` holds a `dict`, so it is not hashable despite being frozen. That is intentional — samples are never used as set members or dict keys.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_samples.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/react_agent/samples.py benchmark/react_agent/tests/test_samples.py \
        benchmark/react_agent/tests/__init__.py benchmark/react_agent/pytest.ini
git commit -m "Add raw sample model shared by both source adapters"
```

---

## Task 3: Counter rate and mean

**Files:**
- Create: `benchmark/react_agent/metrics.py`
- Test: `benchmark/react_agent/tests/test_metrics_rate.py`

**Interfaces:**
- Consumes: `samples.Sample` from Task 2.
- Produces:
  - `rate(series: list[Sample], at: int, window: int) -> float | None` — per-second increase of a monotonic counter over `[at-window, at]`, handling counter resets. `None` when fewer than two samples fall in the window.
  - `mean_rate(sum_series, count_series, at, window) -> float | None` — `rate(sum)/rate(count)`; `None` when the count rate is zero or either rate is `None`.

Prometheus semantics being reproduced: a *decrease* between consecutive samples means the process restarted, so the post-reset value is added whole rather than subtracted.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_metrics_rate.py`:

```python
from metrics import mean_rate, rate
from samples import Sample


def counter(values, start=100, step=1):
    return [
        Sample(timestamp=start + i * step, metric="c", labels={}, value=v)
        for i, v in enumerate(values)
    ]


def test_rate_of_steady_counter():
    # +2 per second across 10 seconds
    series = counter([float(2 * i) for i in range(11)])
    assert rate(series, at=110, window=10) == 2.0


def test_rate_returns_none_with_a_single_sample_in_window():
    series = counter([0.0, 5.0], start=100)
    assert rate(series, at=100, window=10) is None


def test_rate_ignores_samples_outside_the_window():
    series = counter([float(i) for i in range(21)], start=100)
    # window covers 105..110 -> 5 increments over 5 seconds
    assert rate(series, at=110, window=5) == 1.0


def test_rate_treats_a_decrease_as_a_counter_reset():
    # 0,10,20 then process restarts at 3,6
    series = counter([0.0, 10.0, 20.0, 3.0, 6.0], start=100)
    # increases: 10 + 10 + 3 (reset, counted whole) + 3 = 26 over 4 seconds
    assert rate(series, at=104, window=4) == 26.0 / 4.0


def test_rate_of_flat_counter_is_zero():
    series = counter([7.0] * 11)
    assert rate(series, at=110, window=10) == 0.0


def test_mean_rate_divides_sum_rate_by_count_rate():
    sums = counter([0.0, 10.0, 20.0])     # +10/s
    counts = counter([0.0, 4.0, 8.0])     # +4/s
    assert mean_rate(sums, counts, at=102, window=2) == 2.5


def test_mean_rate_is_none_when_no_requests_completed():
    sums = counter([5.0, 5.0, 5.0])
    counts = counter([9.0, 9.0, 9.0])
    assert mean_rate(sums, counts, at=102, window=2) is None


def test_mean_rate_matches_the_reference_snapshot():
    # 3140 requests totalling 4476.2018349170685s -> mean 1.426s
    sums = counter([0.0, 4476.2018349170685])
    counts = counter([0.0, 3140.0])
    got = mean_rate(sums, counts, at=101, window=1)
    assert abs(got - 1.4256) < 0.001
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_metrics_rate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 3: Write the implementation**

Create `benchmark/react_agent/metrics.py`:

```python
"""Pure statistics over raw counter and histogram samples.

Every function here mirrors the corresponding PromQL operation so the fixture
path and the Prometheus path produce identical numbers.
"""

from __future__ import annotations

from samples import Sample


def rate(series: list[Sample], at: int, window: int) -> float | None:
    """Per-second increase of a monotonic counter over ``[at - window, at]``.

    A decrease between consecutive samples means the exporting process
    restarted; the post-reset value is added whole, matching PromQL ``rate``.
    Returns ``None`` when fewer than two samples fall inside the window.
    """
    inside = [s for s in series if at - window <= s.timestamp <= at]
    if len(inside) < 2:
        return None

    inside.sort(key=lambda s: s.timestamp)
    total = 0.0
    for previous, current in zip(inside, inside[1:]):
        if current.value >= previous.value:
            total += current.value - previous.value
        else:
            total += current.value

    span = inside[-1].timestamp - inside[0].timestamp
    if span <= 0:
        return None
    return total / span


def mean_rate(
    sum_series: list[Sample],
    count_series: list[Sample],
    at: int,
    window: int,
) -> float | None:
    """Exact mean observation over the window: ``rate(_sum) / rate(_count)``.

    Free of bucket quantization, unlike ``histogram_quantile``. Returns ``None``
    when no observations completed in the window, which is an undefined mean
    rather than a zero one.
    """
    sum_rate = rate(sum_series, at, window)
    count_rate = rate(count_series, at, window)
    if sum_rate is None or count_rate is None or count_rate == 0:
        return None
    return sum_rate / count_rate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_metrics_rate.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/react_agent/metrics.py benchmark/react_agent/tests/test_metrics_rate.py
git commit -m "Add counter rate and exact mean over raw samples

Reproduces PromQL rate semantics including counter-reset handling, so the
fixture path and the Prometheus path compute identical numbers. mean_rate
returns None rather than 0.0 when no requests completed -- an undefined mean
is not a zero one."
```

---

## Task 4: Histogram quantile

**Files:**
- Modify: `benchmark/react_agent/metrics.py` (append)
- Test: `benchmark/react_agent/tests/test_metrics_quantile.py`

**Interfaces:**
- Consumes: `rate` from Task 3, `samples.Sample` from Task 2.
- Produces:
  - `histogram_quantile(q: float, bucket_series: dict[float, list[Sample]], at: int, window: int) -> float | None`
    where the dict maps an `le` upper bound (`math.inf` for `+Inf`) to that bucket's cumulative counter samples.

Reproduces PromQL: find the first bucket whose cumulative rate reaches `q * total`,
then linearly interpolate between that bucket's lower and upper bound. The lower
bound of the first bucket is 0. If the target falls in the `+Inf` bucket, return the
largest finite bound — an unbounded estimate is not reportable.

This task's assertions pin the real bucket boundaries observed on the deployment,
so a future vLLM upgrade that changes them fails a test instead of quietly shifting
the benchmark.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_metrics_quantile.py`:

```python
import math

from metrics import histogram_quantile
from samples import Sample

# Real boundaries and cumulative counts observed on openai/gpt-oss-20b,
# 2026-07-24. count=3140, sum=4476.2018349170685, mean=1.426s.
REFERENCE = [
    (0.001, 0), (0.005, 0), (0.01, 0), (0.02, 0), (0.04, 0),
    (0.06, 88), (0.08, 116), (0.1, 118), (0.25, 185), (0.5, 612),
    (0.75, 1005), (1.0, 1380), (2.5, 2851), (5.0, 3129), (7.5, 3132),
    (10.0, 3134), (20.0, 3134), (40.0, 3134), (80.0, 3139), (160.0, 3140),
    (640.0, 3140), (2560.0, 3140), (math.inf, 3140),
]


def buckets_from(pairs, start=100, span=10):
    """Two samples per bucket: zero at t=start, the cumulative count at t=start+span."""
    return {
        le: [
            Sample(timestamp=start, metric="b", labels={"le": str(le)}, value=0.0),
            Sample(timestamp=start + span, metric="b", labels={"le": str(le)}, value=float(c)),
        ]
        for le, c in pairs
    }


def test_p95_matches_the_reference_snapshot():
    got = histogram_quantile(0.95, buckets_from(REFERENCE), at=110, window=10)
    assert abs(got - 3.687) < 0.01


def test_p50_matches_the_reference_snapshot():
    got = histogram_quantile(0.50, buckets_from(REFERENCE), at=110, window=10)
    assert abs(got - 1.194) < 0.01


def test_interpolates_linearly_within_a_bucket():
    # 100 observations total; 50 at or below 1.0, all 100 at or below 3.0.
    # p75 -> 75th observation -> a quarter into the (1.0, 3.0] bucket.
    pairs = [(1.0, 50), (3.0, 100), (math.inf, 100)]
    got = histogram_quantile(0.75, buckets_from(pairs), at=110, window=10)
    assert abs(got - 1.5) < 1e-9


def test_first_bucket_interpolates_from_zero():
    pairs = [(2.0, 100), (math.inf, 100)]
    got = histogram_quantile(0.5, buckets_from(pairs), at=110, window=10)
    assert abs(got - 1.0) < 1e-9


def test_target_in_the_inf_bucket_returns_the_largest_finite_bound():
    pairs = [(1.0, 10), (math.inf, 100)]
    got = histogram_quantile(0.95, buckets_from(pairs), at=110, window=10)
    assert got == 1.0


def test_returns_none_when_no_observations_in_window():
    pairs = [(1.0, 0), (math.inf, 0)]
    assert histogram_quantile(0.95, buckets_from(pairs), at=110, window=10) is None


def test_returns_none_when_window_has_too_few_samples():
    buckets = {
        1.0: [Sample(timestamp=100, metric="b", labels={}, value=5.0)],
        math.inf: [Sample(timestamp=100, metric="b", labels={}, value=5.0)],
    }
    assert histogram_quantile(0.95, buckets, at=100, window=10) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_metrics_quantile.py -v`
Expected: FAIL — `ImportError: cannot import name 'histogram_quantile' from 'metrics'`

- [ ] **Step 3: Write the implementation**

Append to `benchmark/react_agent/metrics.py`:

```python
def histogram_quantile(
    q: float,
    bucket_series: dict[float, list[Sample]],
    at: int,
    window: int,
) -> float | None:
    """Quantile estimate from cumulative histogram buckets, as PromQL computes it.

    ``bucket_series`` maps an ``le`` upper bound to that bucket's counter
    samples; use ``math.inf`` for the ``+Inf`` bucket. The estimate assumes
    observations are distributed uniformly inside the containing bucket, so its
    accuracy is bounded by that bucket's width.

    Returns ``None`` when nothing was observed in the window.
    """
    rates: list[tuple[float, float]] = []
    for upper_bound in sorted(bucket_series):
        bucket_rate = rate(bucket_series[upper_bound], at, window)
        if bucket_rate is None:
            return None
        rates.append((upper_bound, bucket_rate))

    if not rates:
        return None

    total = rates[-1][1]
    if total <= 0:
        return None

    finite_bounds = [bound for bound, _ in rates if bound != float("inf")]
    if not finite_bounds:
        return None

    target = q * total
    lower_bound = 0.0
    lower_count = 0.0
    for upper_bound, cumulative in rates:
        if cumulative >= target:
            if upper_bound == float("inf"):
                return max(finite_bounds)
            if cumulative <= lower_count:
                return upper_bound
            fraction = (target - lower_count) / (cumulative - lower_count)
            return lower_bound + (upper_bound - lower_bound) * fraction
        lower_bound = upper_bound
        lower_count = cumulative

    return max(finite_bounds)
```

Add `import math` is not required — `float("inf")` is used throughout.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_metrics_quantile.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the whole suite**

Run: `cd benchmark/react_agent && python3 -m pytest -v`
Expected: 21 passed

- [ ] **Step 6: Commit**

```bash
git add benchmark/react_agent/metrics.py benchmark/react_agent/tests/test_metrics_quantile.py
git commit -m "Add histogram quantile matching PromQL interpolation

Assertions pin the bucket boundaries observed on the live deployment, so a
future vLLM upgrade that changes them fails a test rather than quietly
shifting the benchmark. p95 lands at 3.687s inside the 2.5s-wide (2.5, 5.0]
bucket, which is why the headline claim uses the exact mean instead."
```

---

## Task 5: Anchor detection

**Files:**
- Modify: `benchmark/react_agent/metrics.py` (append)
- Test: `benchmark/react_agent/tests/test_anchor.py`

**Interfaces:**
- Consumes: `samples.Sample` from Task 2.
- Produces:
  - `find_anchor(count_series: list[Sample], sustain: int = 10) -> int | None` — the timestamp at which the TTFT counter starts rising and keeps rising for `sustain` seconds. `None` when no such point exists.

This is the alignment mechanism. `sustain` is a **duration**, not a sample count: a
count-based guard silently tightens as the scrape interval drops.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_anchor.py`:

```python
from metrics import find_anchor
from samples import Sample


def series(values, start=1000):
    """One sample per second."""
    return [
        Sample(timestamp=start + i, metric="vllm:ttft_count", labels={}, value=float(v))
        for i, v in enumerate(values)
    ]


def test_anchor_is_the_first_rising_timestamp():
    # flat for 5s, then rises for 20s
    values = [0] * 5 + list(range(1, 21))
    assert find_anchor(series(values), sustain=10) == 1004


def test_health_check_burst_before_the_real_load_is_rejected():
    # 3 probe requests at t=1010..1012, flat again, real load from t=1090
    values = [0] * 10 + [1, 2, 3] + [3] * 77 + list(range(4, 40))
    anchor = find_anchor(series(values), sustain=10)
    assert anchor == 1089, f"anchor landed on the probe burst at {anchor}"


def test_returns_none_when_the_counter_never_rises():
    assert find_anchor(series([0] * 60), sustain=10) is None


def test_returns_none_when_the_rise_never_sustains():
    # rises for 4s then stops, repeatedly -- never sustains 10s
    values = []
    for _ in range(6):
        values += [len(values), len(values) + 1, len(values) + 2, len(values) + 3]
        values += [values[-1]] * 8
    assert find_anchor(series(values), sustain=10) is None


def test_a_single_flat_second_inside_the_load_does_not_disqualify():
    # real load with one second of no completions -- still the anchor
    values = [0] * 5 + [1, 2, 3, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert find_anchor(series(values), sustain=10) == 1004


def test_sustain_window_is_measured_in_seconds_not_samples():
    # 5s scrape interval: 3 samples span 10s and must satisfy sustain=10
    coarse = [
        Sample(timestamp=1000 + i * 5, metric="c", labels={}, value=float(v))
        for i, v in enumerate([0, 0, 1, 5, 9, 14, 20])
    ]
    assert find_anchor(coarse, sustain=10) == 1005
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_anchor.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_anchor' from 'metrics'`

- [ ] **Step 3: Write the implementation**

Append to `benchmark/react_agent/metrics.py`:

```python
def find_anchor(count_series: list[Sample], sustain: int = 10) -> int | None:
    """Timestamp of the first served token of a sustained workload.

    Anchoring on observed request activity rather than wall-clock start makes
    per-system startup delay cancel out: ``elapsed = 0`` denotes the same
    physical event in every run.

    A candidate qualifies when the counter rises at that timestamp and is
    strictly higher ``sustain`` seconds later. ``sustain`` is a duration so the
    guard keeps its meaning when the scrape interval changes; it rejects
    readiness probes and warm-up bursts, which rise briefly and stop.
    """
    ordered = sorted(count_series, key=lambda s: s.timestamp)
    if len(ordered) < 2:
        return None

    for index in range(1, len(ordered)):
        previous, current = ordered[index - 1], ordered[index]
        if current.value <= previous.value:
            continue

        deadline = current.timestamp + sustain
        later = [s for s in ordered if s.timestamp >= deadline]
        if not later:
            return None
        if later[0].value > current.value:
            return current.timestamp

    return None
```

The `return None` when `later` is empty is deliberate: a rise too close to the end
of the search window cannot be confirmed, and guessing would misalign the run.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_anchor.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/react_agent/metrics.py benchmark/react_agent/tests/test_anchor.py
git commit -m "Add first-served-token anchor detection

Aligns separately-run experiments without needing to know when each was
started, so per-system startup delay cancels. The sustain guard is a duration
rather than a sample count -- a count-based guard would tighten from 45s to 3s
when the scrape interval drops to 1s, short enough for readiness probes to
satisfy. Covered by a test placing a probe burst 80s before the real load."
```

---

## Task 6: Prometheus text-exposition parser and FixtureSource

**Files:**
- Create: `benchmark/react_agent/sources.py`
- Test: `benchmark/react_agent/tests/test_fixture_source.py`

**Interfaces:**
- Consumes: `samples.Sample` from Task 2.
- Produces:
  - `parse_exposition(text: str, timestamp: int) -> list[Sample]` — parse one `/metrics` scrape into samples.
  - `FixtureSource(fixture_dir: Path, server: str)` with `.fetch(start: int, end: int) -> list[Sample]`.

Fixture layout: one file per scrape, named `<unix_timestamp>.prom`, inside
`fixture_dir`. `FixtureSource` stamps `server=<server>` onto every sample it
returns, because the raw endpoint does not carry that label — Prometheus adds it
at scrape time from `file_sd`. Synthesizing it keeps both adapters interchangeable
and lets `--target` filtering be exercised on the fixture path.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_fixture_source.py`:

```python
from pathlib import Path

from samples import select
from sources import FixtureSource, parse_exposition

SCRAPE = """\
# HELP vllm:time_to_first_token_seconds Histogram of time to first token in seconds.
# TYPE vllm:time_to_first_token_seconds histogram
vllm:time_to_first_token_seconds_bucket{engine="0",le="0.5",model_name="openai/gpt-oss-20b"} 612.0
vllm:time_to_first_token_seconds_bucket{engine="0",le="+Inf",model_name="openai/gpt-oss-20b"} 3140.0
vllm:time_to_first_token_seconds_count{engine="0",model_name="openai/gpt-oss-20b"} 3140.0
vllm:time_to_first_token_seconds_sum{engine="0",model_name="openai/gpt-oss-20b"} 4476.2018349170685
vllm:num_requests_running{engine="0",model_name="openai/gpt-oss-20b"} 3.0
"""


def test_parses_metric_names_and_values():
    got = parse_exposition(SCRAPE, timestamp=1000)
    running = select(got, "vllm:num_requests_running")
    assert len(running) == 1
    assert running[0].value == 3.0
    assert running[0].timestamp == 1000


def test_parses_labels_including_le():
    got = parse_exposition(SCRAPE, timestamp=1000)
    buckets = select(got, "vllm:time_to_first_token_seconds_bucket", le="0.5")
    assert len(buckets) == 1
    assert buckets[0].value == 612.0
    assert buckets[0].labels["model_name"] == "openai/gpt-oss-20b"
    assert buckets[0].labels["engine"] == "0"


def test_preserves_the_inf_bucket_label():
    got = parse_exposition(SCRAPE, timestamp=1000)
    assert select(got, "vllm:time_to_first_token_seconds_bucket", le="+Inf")[0].value == 3140.0


def test_skips_comment_and_blank_lines():
    got = parse_exposition(SCRAPE, timestamp=1000)
    assert all(not s.metric.startswith("#") for s in got)
    assert len(got) == 5


def test_parses_scientific_notation():
    text = 'vllm:time_to_first_token_seconds_created{engine="0"} 1.7848325232461488e+09\n'
    got = parse_exposition(text, timestamp=1000)
    assert abs(got[0].value - 1784832523.2461488) < 1e-3


def test_parses_a_metric_with_no_labels():
    got = parse_exposition("process_open_fds 42.0\n", timestamp=1000)
    assert got[0].metric == "process_open_fds"
    assert got[0].labels == {}
    assert got[0].value == 42.0


def test_fixture_source_reads_scrapes_in_the_window(tmp_path: Path):
    for ts in (1000, 1001, 1002, 1003):
        (tmp_path / f"{ts}.prom").write_text(SCRAPE)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(start=1001, end=1002)
    stamps = sorted({s.timestamp for s in got})
    assert stamps == [1001, 1002]


def test_fixture_source_synthesizes_the_server_label(tmp_path: Path):
    (tmp_path / "1000.prom").write_text(SCRAPE)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(start=1000, end=1000)
    assert all(s.labels["server"] == "solab-x3" for s in got)


def test_fixture_source_returns_empty_for_a_window_with_no_scrapes(tmp_path: Path):
    (tmp_path / "1000.prom").write_text(SCRAPE)
    assert FixtureSource(tmp_path, server="solab-x3").fetch(start=2000, end=2100) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_fixture_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sources'`

- [ ] **Step 3: Write the implementation**

Create `benchmark/react_agent/sources.py`:

```python
"""Source adapters. Both yield raw samples so downstream math is identical.

The fixture adapter reads recorded ``/metrics`` scrapes; the Prometheus adapter
queries a live server. Neither computes statistics -- that is ``metrics.py``'s
job -- so the fixture path exercises the same code as production.
"""

from __future__ import annotations

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_fixture_source.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/react_agent/sources.py benchmark/react_agent/tests/test_fixture_source.py
git commit -m "Add exposition parser and fixture source

The fixture stamps the server label that Prometheus would add at scrape time,
so both adapters emit identical label sets and target filtering is exercised
without a live server."
```

---

## Task 7: PrometheusSource

**Files:**
- Modify: `benchmark/react_agent/sources.py` (append)
- Test: `benchmark/react_agent/tests/test_prometheus_source.py`

**Interfaces:**
- Consumes: `samples.Sample`, `requests`.
- Produces:
  - `PrometheusSource(base_url: str, target: str, model: str | None = None, timeout: int = 30)`
    with `.fetch(start: int, end: int, step: int = 1) -> list[Sample]`.
  - `PrometheusError(RuntimeError)`.

Queries **raw** series via `query_range` — no `rate`, no `histogram_quantile`. All
derivation happens in `metrics.py`, identically for both sources.

Metrics fetched (each pinned with `{server="<target>"}` plus `model_name` when
`--model` is given):

```
vllm:time_to_first_token_seconds_bucket
vllm:time_to_first_token_seconds_sum
vllm:time_to_first_token_seconds_count
vllm:e2e_request_latency_seconds_bucket
vllm:e2e_request_latency_seconds_sum
vllm:e2e_request_latency_seconds_count
vllm:request_queue_time_seconds_bucket
vllm:request_prefill_time_seconds_bucket
vllm:external_prefix_cache_queries_total
vllm:external_prefix_cache_hits_total
vllm:prompt_tokens_recomputed_total
vllm:request_success_total
```

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_prometheus_source.py`:

```python
import pytest

import sources
from sources import PrometheusError, PrometheusSource


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def install_fake_get(monkeypatch, payload_for_query):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["query"])
        return FakeResponse(payload_for_query(params["query"]))

    monkeypatch.setattr(sources.requests, "get", fake_get)
    return calls


def matrix(metric_labels, values):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": metric_labels, "values": values}],
        },
    }


def test_builds_selector_pinned_to_the_target(monkeypatch):
    calls = install_fake_get(monkeypatch, lambda q: matrix({}, []))
    PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1010)
    assert all('server="solab-x3"' in query for query in calls)


def test_includes_the_model_filter_when_given(monkeypatch):
    calls = install_fake_get(monkeypatch, lambda q: matrix({}, []))
    PrometheusSource("http://p7:9090", target="solab-x3", model="openai/gpt-oss-20b").fetch(1000, 1010)
    assert all('model_name="openai/gpt-oss-20b"' in query for query in calls)


def test_never_uses_promql_functions(monkeypatch):
    calls = install_fake_get(monkeypatch, lambda q: matrix({}, []))
    PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1010)
    for query in calls:
        assert "rate(" not in query
        assert "histogram_quantile" not in query


def test_converts_matrix_results_to_samples(monkeypatch):
    payload = matrix(
        {"__name__": "vllm:time_to_first_token_seconds_count", "server": "solab-x3", "le": "0.5"},
        [[1000, "5"], [1001, "9"]],
    )
    install_fake_get(monkeypatch, lambda q: payload)
    got = PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)
    counts = [s for s in got if s.metric == "vllm:time_to_first_token_seconds_count"]
    assert (counts[0].timestamp, counts[0].value) == (1000, 5.0)
    assert counts[0].labels["le"] == "0.5"
    assert "__name__" not in counts[0].labels


def test_aborts_when_the_window_spans_multiple_instances(monkeypatch):
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {"metric": {"__name__": "m", "server": "solab-x3"}, "values": [[1000, "1"]]},
                {"metric": {"__name__": "m", "server": "solab-x9"}, "values": [[1000, "2"]]},
            ],
        },
    }
    install_fake_get(monkeypatch, lambda q: payload)
    with pytest.raises(PrometheusError, match="solab-x9"):
        PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)


def test_raises_on_prometheus_error_status(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"status": "error", "error": "parse error"})

    monkeypatch.setattr(sources.requests, "get", fake_get)
    with pytest.raises(PrometheusError, match="parse error"):
        PrometheusSource("http://p7:9090", target="solab-x3").fetch(1000, 1001)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_prometheus_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'PrometheusError' from 'sources'`

- [ ] **Step 3: Write the implementation**

Append to `benchmark/react_agent/sources.py`:

```python
import requests

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

    def __init__(
        self,
        base_url: str,
        target: str,
        model: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.target = target
        self.model = model
        self.timeout = timeout

    def _selector(self) -> str:
        parts = [f'server="{self.target}"']
        if self.model:
            parts.append(f'model_name="{self.model}"')
        return "{" + ",".join(parts) + "}"

    def fetch(self, start: int, end: int, step: int = 1) -> list[Sample]:
        selector = self._selector()
        collected: list[Sample] = []
        seen_servers: set[str] = set()

        for metric in RAW_METRICS:
            payload = self._query_range(f"{metric}{selector}", start, end, step)
            for stream in payload:
                labels = {k: v for k, v in stream["metric"].items() if k != "__name__"}
                if "server" in labels:
                    seen_servers.add(labels["server"])
                for timestamp, value in stream["values"]:
                    collected.append(
                        Sample(
                            timestamp=int(timestamp),
                            metric=metric,
                            labels=labels,
                            value=float(value),
                        )
                    )

        extra = seen_servers - {self.target}
        if extra:
            raise PrometheusError(
                "Window resolves to more than one vLLM host: "
                f"{sorted(seen_servers)}. Unexpected: {sorted(extra)}. "
                "Aggregating across hosts would yield a latency figure "
                "describing no real server."
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
```

Move `import requests` to the top of the file alongside the other imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_prometheus_source.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the whole suite**

Run: `cd benchmark/react_agent && python3 -m pytest`
Expected: 42 passed

- [ ] **Step 6: Commit**

```bash
git add benchmark/react_agent/sources.py benchmark/react_agent/tests/test_prometheus_source.py
git commit -m "Add Prometheus source fetching raw series only

Queries counters and buckets untouched so metrics.py performs the derivation
for both sources. Aborts when the window resolves to more than one server:
p7 scrapes several vLLM hosts, and an unfiltered aggregate would render a
plausible chart describing no real machine."
```

---

## Task 8: Fixture generator

**Files:**
- Create: `benchmark/react_agent/fixtures/__init__.py` (empty)
- Create: `benchmark/react_agent/fixtures/generate.py`
- Create: `benchmark/react_agent/fixtures/README.md`
- Test: `benchmark/react_agent/tests/test_fixture_generate.py`

**Interfaces:**
- Consumes: `sources.parse_exposition`.
- Produces:
  - `synthesize(system: str, seconds: int, seed: int) -> dict[int, str]` — maps relative second → exposition text.
  - `write_fixture(system: str, out_dir: Path, start: int, seconds: int, seed: int) -> None`.

Shapes required by the spec, all built into the generated data:

1. A **health-check burst** ~80s before the real load — 3 requests, then flat. The anchor must skip it.
2. A **warm-up ramp** over the first 60s after the anchor.
3. **Sample noise**, deterministic under `seed`.
4. A **scrape gap** (a missing `.prom` file) mid-run, which must survive to the CSV as an empty cell.
5. Per-system latency profiles so MARS wins on mean TTFT and `recompute` has **zero** external cache queries.

Bucket boundaries are the real ones observed on the deployment.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_fixture_generate.py`:

```python
from pathlib import Path

from fixtures.generate import SYSTEMS, synthesize, write_fixture
from metrics import find_anchor, mean_rate
from samples import select
from sources import FixtureSource


def test_every_system_is_generated():
    assert set(SYSTEMS) == {"mars", "lmcache", "mooncake", "recompute"}


def test_synthesize_is_deterministic_under_seed():
    a = synthesize("mars", seconds=120, seed=7)
    b = synthesize("mars", seconds=120, seed=7)
    assert a == b


def test_generated_scrapes_parse(tmp_path: Path):
    write_fixture("mars", tmp_path, start=1000, seconds=200, seed=1)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(1000, 1200)
    assert select(got, "vllm:time_to_first_token_seconds_count")


def test_contains_a_scrape_gap(tmp_path: Path):
    write_fixture("mars", tmp_path, start=1000, seconds=200, seed=1)
    present = {int(p.stem) for p in tmp_path.glob("*.prom")}
    missing = set(range(1000, 1200)) - present
    assert missing, "fixture must contain at least one missing scrape"


def test_anchor_skips_the_health_check_burst(tmp_path: Path):
    write_fixture("mars", tmp_path, start=1000, seconds=400, seed=1)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(1000, 1400)
    counts = select(got, "vllm:time_to_first_token_seconds_count")
    anchor = find_anchor(counts, sustain=10)
    assert anchor is not None
    # the probe burst sits near t=1020; the real load starts at t=1100
    assert anchor >= 1095, f"anchor landed on the probe burst at {anchor}"


def test_mars_has_the_lowest_mean_ttft(tmp_path: Path):
    means = {}
    for system in SYSTEMS:
        directory = tmp_path / system
        directory.mkdir()
        write_fixture(system, directory, start=1000, seconds=400, seed=1)
        got = FixtureSource(directory, server="solab-x3").fetch(1000, 1400)
        means[system] = mean_rate(
            select(got, "vllm:time_to_first_token_seconds_sum"),
            select(got, "vllm:time_to_first_token_seconds_count"),
            at=1350,
            window=30,
        )
    assert means["mars"] == min(means.values())
    assert means["recompute"] == max(means.values())


def test_recompute_has_no_external_cache_queries(tmp_path: Path):
    write_fixture("recompute", tmp_path, start=1000, seconds=400, seed=1)
    got = FixtureSource(tmp_path, server="solab-x3").fetch(1000, 1400)
    queries = select(got, "vllm:external_prefix_cache_queries_total")
    assert all(sample.value == 0.0 for sample in queries)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_fixture_generate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fixtures'`

- [ ] **Step 3: Write the implementation**

Create `benchmark/react_agent/fixtures/__init__.py` (empty), then `benchmark/react_agent/fixtures/generate.py`:

```python
"""Generate fixture scrapes shaped like the real vLLM endpoint.

Bucket boundaries and label sets come from a live capture taken 2026-07-24
against openai/gpt-oss-20b (count=3140, sum=4476.2s, mean=1.426s, p95~3.687s).
Values are synthetic; the structure is real.

The data deliberately contains the cases that break naive implementations: a
health-check burst before the real load, a warm-up ramp, sample noise, and a
missing scrape.
"""

from __future__ import annotations

import pathlib
import random

BUCKETS = (
    0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.25, 0.5, 0.75, 1.0,
    2.5, 5.0, 7.5, 10.0, 20.0, 40.0, 80.0, 160.0, 640.0, 2560.0,
)

MODEL = "openai/gpt-oss-20b"

# mean TTFT (seconds), mean e2e (seconds), external cache hit ratio
SYSTEMS = {
    "mars":      (0.86, 2.71, 0.82),
    "lmcache":   (1.13, 3.29, 0.64),
    "mooncake":  (1.27, 3.55, 0.58),
    "recompute": (1.71, 4.42, 0.00),
}

PROBE_AT = 20        # seconds after start: health-check burst
LOAD_AT = 100        # seconds after start: real workload begins
WARMUP = 60          # seconds of elevated latency after LOAD_AT
GAP_AT = 250         # seconds after start: one missing scrape


def _ttft_for(second: int, base: float, rng: random.Random) -> float:
    """Per-request TTFT, elevated during warm-up, with noise."""
    elapsed = second - LOAD_AT
    warm = 1.0 + 0.65 * max(0.0, 1.0 - elapsed / WARMUP)
    return max(0.02, base * warm * rng.uniform(0.72, 1.34))


def _render(counters: dict, buckets: dict) -> str:
    lines = [
        "# HELP vllm:time_to_first_token_seconds Histogram of time to first token in seconds.",
        "# TYPE vllm:time_to_first_token_seconds histogram",
    ]
    labels = f'engine="0",model_name="{MODEL}"'
    for family in ("time_to_first_token_seconds", "e2e_request_latency_seconds"):
        for bound in BUCKETS:
            count = buckets[family][bound]
            lines.append(
                f'vllm:{family}_bucket{{engine="0",le="{bound}",model_name="{MODEL}"}} {count}.0'
            )
        total = buckets[family]["count"]
        lines.append(f'vllm:{family}_bucket{{engine="0",le="+Inf",model_name="{MODEL}"}} {total}.0')
        lines.append(f"vllm:{family}_count{{{labels}}} {total}.0")
        lines.append(f"vllm:{family}_sum{{{labels}}} {buckets[family]['sum']:.6f}")
    for name, value in counters.items():
        lines.append(f"vllm:{name}{{{labels}}} {value}")
    return "\n".join(lines) + "\n"


def synthesize(system: str, seconds: int, seed: int) -> dict[int, str]:
    """Relative second -> exposition text. Deterministic for a given seed."""
    base_ttft, base_e2e, hit_ratio = SYSTEMS[system]
    rng = random.Random(f"{system}:{seed}")

    families = {
        family: {"count": 0, "sum": 0.0, **{bound: 0 for bound in BUCKETS}}
        for family in ("time_to_first_token_seconds", "e2e_request_latency_seconds")
    }
    queries = hits = recomputed = successes = 0
    scrapes: dict[int, str] = {}

    for second in range(seconds):
        if second == PROBE_AT:
            arrivals = 3
        elif second < LOAD_AT:
            arrivals = 0
        else:
            arrivals = rng.randint(9, 15)

        for _ in range(arrivals):
            for family, base in (
                ("time_to_first_token_seconds", base_ttft),
                ("e2e_request_latency_seconds", base_e2e),
            ):
                observation = _ttft_for(second, base, rng)
                bucket = families[family]
                bucket["count"] += 1
                bucket["sum"] += observation
                for bound in BUCKETS:
                    if observation <= bound:
                        bucket[bound] += 1
            successes += 1
            recomputed += rng.randint(40, 120) if hit_ratio == 0 else rng.randint(4, 30)
            if hit_ratio > 0:
                queries += 1
                if rng.random() < hit_ratio:
                    hits += 1

        counters = {
            "external_prefix_cache_queries_total": queries,
            "external_prefix_cache_hits_total": hits,
            "prompt_tokens_recomputed_total": recomputed,
            "request_success_total": successes,
        }
        scrapes[second] = _render(counters, families)

    scrapes.pop(GAP_AT, None)
    return scrapes


def write_fixture(
    system: str,
    out_dir: pathlib.Path,
    start: int,
    seconds: int,
    seed: int,
) -> None:
    """Write ``<unix_timestamp>.prom`` scrapes for one system."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for offset, text in synthesize(system, seconds, seed).items():
        (out_dir / f"{start + offset}.prom").write_text(text, encoding="utf-8")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path(__file__).parent)
    parser.add_argument("--start", type=int, default=1784912400)
    parser.add_argument("--seconds", type=int, default=520)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    for system in SYSTEMS:
        write_fixture(system, args.out / system, args.start, args.seconds, args.seed)
        print(args.out / system)


if __name__ == "__main__":
    main()
```

The cumulative bucket counts are built by incrementing every bound at or above each
observation, which is what makes them cumulative — matching real histogram semantics.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_fixture_generate.py -v`
Expected: 7 passed

- [ ] **Step 5: Generate the committed fixture**

```bash
cd benchmark/react_agent && python3 -m fixtures.generate --out fixtures
ls fixtures/mars | head -3
```

Expected: `1784912400.prom` and successors.

- [ ] **Step 6: Write the fixture README**

Create `benchmark/react_agent/fixtures/README.md`:

```markdown
# Fixture scrapes

Synthetic vLLM `/metrics` scrapes, one file per second, named
`<unix_timestamp>.prom`. Regenerate with:

    python3 -m fixtures.generate --out fixtures

Structure (metric names, label sets, histogram bucket boundaries) comes from a
live capture of `openai/gpt-oss-20b` taken 2026-07-24. Values are synthetic.

Deliberate shapes, each covering a failure this pipeline must survive:

| Shape | At | Guards against |
|---|---|---|
| Health-check burst, 3 requests | t+20s | An anchor pinned 80s early |
| Real workload begins | t+100s | — |
| Warm-up ramp | t+100..160s | Cold-start transients in the headline |
| Missing scrape | t+250s | Gaps being interpolated or zero-filled |
| `recompute` with zero cache queries | throughout | An undefined hit ratio reported as 0.0 |

MARS is generated with the lowest mean TTFT and `recompute` the highest, so
ordering assertions are meaningful.
```

- [ ] **Step 7: Commit**

```bash
git add benchmark/react_agent/fixtures benchmark/react_agent/tests/test_fixture_generate.py
git commit -m "Add fixture generator built on the real endpoint's structure

Bucket boundaries, label sets, and metric names come from a live capture; only
values are synthetic. Embeds a health-check burst, warm-up ramp, scrape gap,
and a zero-external-cache system so the pipeline's edge cases are exercised
before any GPU time is spent."
```

---

## Task 9: extract_run.py

**Files:**
- Create: `benchmark/react_agent/extract_run.py`
- Test: `benchmark/react_agent/tests/test_extract_run.py`

**Interfaces:**
- Consumes: `sources.FixtureSource`, `sources.PrometheusSource`, `metrics.*`, `samples.select`.
- Produces:
  - `CSV_COLUMNS: tuple[str, ...]`
  - `derive_row(samples, at, window, server) -> dict[str, float | None]`
  - `build_rows(samples, anchor, interval, duration, warmup, server, system) -> list[dict]`
  - `write_run(rows, manifest, runs_dir, system, run_id, force) -> Path`
  - `main()` CLI

CSV columns, in order:

```
timestamp, elapsed_seconds, system,
ttft_mean_seconds, e2e_mean_seconds,
ttft_p95_seconds, e2e_p95_seconds,
requests_completed,
queue_p95_seconds, prefill_p95_seconds,
ext_cache_hit_ratio, prompt_tokens_recomputed
```

`elapsed_seconds` runs from `-warmup` (default -60) to `duration` (default 300).
Any metric that cannot be computed is written as an **empty cell**, never `0.0`.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_extract_run.py`:

```python
import csv
import json
from pathlib import Path

import pytest

from extract_run import CSV_COLUMNS, build_rows, write_run
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_extract_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extract_run'`

- [ ] **Step 3: Write the implementation**

Create `benchmark/react_agent/extract_run.py`:

```python
#!/usr/bin/env python3
"""Extract one isolated benchmark run into a normalized CSV.

Prometheus records vLLM continuously, so this runs whenever convenient after an
experiment -- it never has to be started alongside one. ``--start`` and
``--duration`` define a search window that merely has to contain the run; the
true ``t=0`` is detected from the first sustained rise in served tokens.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib

import metrics
from samples import Sample, select
from sources import FixtureSource, PrometheusSource

CSV_COLUMNS = (
    "timestamp",
    "elapsed_seconds",
    "system",
    "ttft_mean_seconds",
    "e2e_mean_seconds",
    "ttft_p95_seconds",
    "e2e_p95_seconds",
    "requests_completed",
    "queue_p95_seconds",
    "prefill_p95_seconds",
    "ext_cache_hit_ratio",
    "prompt_tokens_recomputed",
)

RATE_WINDOW = 30


def _buckets(samples: list[Sample], metric: str, server: str) -> dict[float, list[Sample]]:
    """Group a histogram's bucket samples by their ``le`` upper bound."""
    grouped: dict[float, list[Sample]] = {}
    for sample in select(samples, metric, server=server):
        raw = sample.labels.get("le")
        if raw is None:
            continue
        bound = float("inf") if raw in ("+Inf", "Inf") else float(raw)
        grouped.setdefault(bound, []).append(sample)
    return grouped


def _last_value(samples: list[Sample], metric: str, server: str, at: int) -> float | None:
    candidates = [s for s in select(samples, metric, server=server) if s.timestamp <= at]
    return candidates[-1].value if candidates else None


def derive_row(samples: list[Sample], at: int, window: int, server: str) -> dict:
    """Every derived metric at one instant. Unavailable values are ``None``."""
    ttft_sum = select(samples, "vllm:time_to_first_token_seconds_sum", server=server)
    ttft_count = select(samples, "vllm:time_to_first_token_seconds_count", server=server)
    e2e_sum = select(samples, "vllm:e2e_request_latency_seconds_sum", server=server)
    e2e_count = select(samples, "vllm:e2e_request_latency_seconds_count", server=server)

    queries = select(samples, "vllm:external_prefix_cache_queries_total", server=server)
    hits = select(samples, "vllm:external_prefix_cache_hits_total", server=server)
    query_rate = metrics.rate(queries, at, window)
    hit_rate = metrics.rate(hits, at, window)
    if query_rate is None or hit_rate is None or query_rate == 0:
        # An absent external KV store has an undefined hit ratio, not a zero one.
        hit_ratio = None
    else:
        hit_ratio = hit_rate / query_rate

    return {
        "ttft_mean_seconds": metrics.mean_rate(ttft_sum, ttft_count, at, window),
        "e2e_mean_seconds": metrics.mean_rate(e2e_sum, e2e_count, at, window),
        "ttft_p95_seconds": metrics.histogram_quantile(
            0.95, _buckets(samples, "vllm:time_to_first_token_seconds_bucket", server), at, window
        ),
        "e2e_p95_seconds": metrics.histogram_quantile(
            0.95, _buckets(samples, "vllm:e2e_request_latency_seconds_bucket", server), at, window
        ),
        "requests_completed": _last_value(samples, "vllm:request_success_total", server, at),
        "queue_p95_seconds": metrics.histogram_quantile(
            0.95, _buckets(samples, "vllm:request_queue_time_seconds_bucket", server), at, window
        ),
        "prefill_p95_seconds": metrics.histogram_quantile(
            0.95, _buckets(samples, "vllm:request_prefill_time_seconds_bucket", server), at, window
        ),
        "ext_cache_hit_ratio": hit_ratio,
        "prompt_tokens_recomputed": _last_value(
            samples, "vllm:prompt_tokens_recomputed_total", server, at
        ),
    }


def build_rows(
    samples: list[Sample],
    anchor: int | None,
    interval: int,
    duration: int,
    warmup: int,
    server: str,
    system: str,
) -> list[dict]:
    """One row per interval from ``-warmup`` through ``duration``.

    ``t=0`` is ``anchor + warmup``: the leading segment pre-fills the scrolling
    window with real data and is excluded from headline statistics as warm-up.
    """
    if anchor is None:
        raise ValueError(
            "No anchor found: the TTFT counter never rose and sustained inside "
            "the search window. Widen --start/--duration, or confirm the run "
            "actually served traffic."
        )

    zero = anchor + warmup
    rows = []
    for elapsed in range(-warmup, duration + 1, interval):
        at = zero + elapsed
        row = {"timestamp": at, "elapsed_seconds": elapsed, "system": system}
        row.update(derive_row(samples, at, RATE_WINDOW, server))
        rows.append({column: row.get(column) for column in CSV_COLUMNS})
    return rows


def write_run(
    rows: list[dict],
    manifest: dict,
    runs_dir: pathlib.Path,
    system: str,
    run_id: str,
    force: bool,
) -> pathlib.Path:
    """Write ``<runs_dir>/<system>/<run_id>.csv`` and its JSON manifest."""
    directory = pathlib.Path(runs_dir) / system
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.csv"
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to replace it")

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: ("" if value is None else value)
                    for key, value in row.items()
                }
            )
    path.with_suffix(".json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", required=True, choices=("mars", "lmcache", "mooncake", "recompute"))
    parser.add_argument("--source", required=True, choices=("fixture", "prometheus"))
    parser.add_argument("--target", required=True, help="value of the vLLM server label, e.g. solab-x3")
    parser.add_argument("--fixture", type=pathlib.Path, help="fixture directory (--source fixture)")
    parser.add_argument("--prometheus-url", help="e.g. http://solab-p7:9090 (--source prometheus)")
    parser.add_argument("--model", help="optional model_name filter")
    parser.add_argument("--start", help="ISO-8601 search window start (--source prometheus)")
    parser.add_argument("--duration", type=int, default=900, help="search window length in seconds")
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--benchmark-duration", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--anchor-sustain", type=int, default=10)
    parser.add_argument("--runs-dir", type=pathlib.Path, default=pathlib.Path(__file__).with_name("runs"))
    parser.add_argument("--run-id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.source == "fixture":
        if not args.fixture:
            parser.error("--fixture is required with --source fixture")
        stems = sorted(int(p.stem) for p in args.fixture.glob("*.prom"))
        if not stems:
            parser.error(f"no .prom scrapes found in {args.fixture}")
        start, end = stems[0], stems[-1]
        source = FixtureSource(args.fixture, server=args.target)
        classification = "fixture"
    else:
        if not (args.prometheus_url and args.start):
            parser.error("--prometheus-url and --start are required with --source prometheus")
        start = int(dt.datetime.fromisoformat(args.start).timestamp())
        end = start + args.duration
        source = PrometheusSource(args.prometheus_url, target=args.target, model=args.model)
        classification = "captured"

    samples = source.fetch(start, end)
    if not samples:
        raise SystemExit(
            f"No samples in {start}..{end} for server={args.target!r}. "
            "Most likely the vLLM scrape job was not running during that window."
        )

    counts = select(samples, "vllm:time_to_first_token_seconds_count", server=args.target)
    anchor = metrics.find_anchor(counts, sustain=args.anchor_sustain)
    if anchor is None:
        raise SystemExit(
            f"No anchor found in {start}..{end}. The TTFT counter never rose and "
            f"sustained for {args.anchor_sustain}s. Widen the window, or confirm "
            "the run served traffic."
        )

    rows = build_rows(
        samples, anchor, args.interval, args.benchmark_duration,
        args.warmup, args.target, args.system,
    )
    run_id = args.run_id or dt.datetime.fromtimestamp(anchor, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    truncated = max(s.timestamp for s in samples) < anchor + args.warmup + args.benchmark_duration
    manifest = {
        "schema_version": 2,
        "system": args.system,
        "run_id": run_id,
        "server": args.target,
        "model": args.model,
        "source": args.source,
        "data_classification": classification,
        "search_window": [start, end],
        "anchor_unix": anchor,
        "benchmark_zero_unix": anchor + args.warmup,
        "warmup_seconds": args.warmup,
        "benchmark_duration_seconds": args.benchmark_duration,
        "interval_seconds": args.interval,
        "anchor_sustain_seconds": args.anchor_sustain,
        "rate_window_seconds": RATE_WINDOW,
        "sample_count": len(rows),
        "truncated": truncated,
    }
    if truncated:
        print("WARNING: run ends before the requested benchmark duration; manifest marks it truncated")
    print(write_run(rows, manifest, args.runs_dir, args.system, run_id, args.force))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_extract_run.py -v`
Expected: 11 passed

- [ ] **Step 5: Extract all four fixture runs end to end**

```bash
cd benchmark/react_agent
rm -rf runs
for s in mars lmcache mooncake recompute; do
  python3 extract_run.py --system "$s" --source fixture \
    --fixture "fixtures/$s" --target solab-x3
done
head -3 runs/mars/*.csv
```

Expected: four CSVs written; the header matches `CSV_COLUMNS`; `elapsed_seconds` starts at `-60`.

- [ ] **Step 6: Commit**

```bash
git add benchmark/react_agent/extract_run.py benchmark/react_agent/tests/test_extract_run.py benchmark/react_agent/runs
git commit -m "Add run extraction with anchor alignment

Detects t=0 from the first sustained rise in served tokens, so extraction is
decoupled from experiment time and per-system startup delay cancels.
Unavailable metrics are written as empty cells rather than zeros -- notably
the external cache hit ratio, which is undefined for recompute rather than 0%."
```

---

## Task 10: replay.py

**Files:**
- Create: `benchmark/react_agent/replay.py`
- Test: `benchmark/react_agent/tests/test_replay.py`

**Interfaces:**
- Consumes: CSVs written by Task 9.
- Produces:
  - `SYSTEMS: tuple[str, ...]` = `("mars", "lmcache", "mooncake", "recompute")`
  - `LABELS: dict[str, str]`, `COLORS: dict[str, str]`
  - `Replay.load(runs_dir: Path) -> Replay`
  - `Replay.window(system, metric, at, width) -> tuple[list[float], list[float | None]]`
  - `Replay.value_at(system, metric, at) -> float | None`
  - `Replay.improvement_vs(system, baseline, metric, at, smooth=30) -> float | None`
  - `Replay.second_best(exclude, metric, at, smooth=30) -> str | None`
  - `Replay.duration: int`, `Replay.start: int`, `Replay.classifications: set[str]`

`improvement_vs` returns a **fraction** (0.18 = 18% better), positive when `system`
is lower than `baseline`. Both sides are smoothed over a trailing window so the
headline does not strobe.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_replay.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'replay'`

- [ ] **Step 3: Write the implementation**

Create `benchmark/react_agent/replay.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_replay.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add benchmark/react_agent/replay.py benchmark/react_agent/tests/test_replay.py
git commit -m "Add replay loader aligning the four runs on elapsed time

Improvement figures are smoothed over a trailing window so the headline does
not strobe when two systems trade places. Gaps are preserved as None through
the window API so the renderer can break the line rather than bridge it."
```

---

## Task 11: render_video.py

**Files:**
- Create: `benchmark/react_agent/render_video.py`
- Test: `benchmark/react_agent/tests/test_render_video.py`

**Interfaces:**
- Consumes: `replay.Replay`, `replay.COLORS`, `replay.LABELS`, `replay.SYSTEMS`.
- Produces:
  - `Dashboard(replay, window=60, baseline="recompute")` with `.draw(at: float) -> None`
  - `render(replay, output: Path, fps=20, speed=2, window=60) -> Path`

Layout, 1920×1080 at 100 dpi, dark surface `#1a1a19`:

```
┌──────────────────────────────────────────────┬──────────────────┐
│ ReAct AI agent serving benchmark             │  MARS vs Recompute│
│ four isolated runs aligned on first token    │      31.4%        │
├──────────────────────────────────────────────┤  lower mean TTFT  │
│                                              │  vs 2nd best      │
│  TTFT (s)          [scrolling 60s window]    │  (LMCache) 18.2%  │
│                                              ├──────────────────┤
│                                              │ Current TTFT      │
├──────────────────────────────────────────────┤  MARS     ▌ 0.86  │
│                                              │  LMCache  ▌▌1.13  │
│  End-to-end latency (s)                      │  Mooncake ▌▌1.27  │
│                                              │  Recompute▌▌▌1.71 │
│                                              ├──────────────────┤
│                                              │ Current E2E       │
│                                              │  ...bars...       │
├──────────────────────────────────────────────┴──────────────────┤
│ ● MARS  ● LMCache  ● Mooncake  ● Recompute   — mean  ·· p95      │
│                                        t = 143s / 300s  [FIXTURE]│
└─────────────────────────────────────────────────────────────────┘
```

Rules carried from the `dataviz` skill:

- Colour encodes system only; line style encodes metric (mean solid, p95 dashed at 45% alpha).
- The legend is always present and names all four systems, so identity is never colour-alone. The bar rows are direct-labelled with values.
- Grid and axes are recessive (`#3a3a38`, 0.8pt); text uses ink colours, never a series colour.
- Line width 2.0pt; MARS 3.2pt.
- Two panels, never a dual axis.
- Bars carry a 2px surface gap; each bar row is direct-labelled with its number.
- The x-axis is a fixed-width trailing window that is already full at the first frame.

- [ ] **Step 1: Write the failing test**

Create `benchmark/react_agent/tests/test_render_video.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_render_video.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'render_video'`

- [ ] **Step 3: Write the implementation**

Create `benchmark/react_agent/render_video.py`:

```python
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
                self.lines[(prefix, system, "p95")] = axis.plot(
                    [], [], color=COLORS[system], linewidth=width * 0.6,
                    linestyle=(0, (4, 3)), alpha=0.45, zorder=2,
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
            Line2D([], [], color=INK_MUTED, linewidth=1.6,
                   linestyle=(0, (4, 3)), label="p95"),
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
            values.append(0.0 if value is None else value)
            labels.append(LABELS[system])
            colors.append(COLORS[system])

        positions = range(len(values))
        axis.barh(list(positions), values, color=colors, height=0.62, zorder=3)
        axis.set_yticks(list(positions))
        axis.set_yticklabels(labels, color=INK_MUTED, fontsize=13)
        span = max(values) if max(values) > 0 else 1.0
        axis.set_xlim(0, span * 1.28)
        for position, value in zip(positions, values):
            axis.text(value + span * 0.03, position, f"{value:.2f}s",
                      va="center", color=INK, fontsize=13, fontweight="bold")

    def draw(self, at: float) -> None:
        at = float(at)
        for axis, prefix in ((self.ttft_axis, "ttft"), (self.e2e_axis, "e2e")):
            highest = 0.0
            for system in SYSTEMS:
                for kind, column in (
                    ("mean", f"{prefix}_mean_seconds"),
                    ("p95", f"{prefix}_p95_seconds"),
                ):
                    xs, ys = self.replay.window(system, column, round(at), self.window)
                    # None stays None: matplotlib breaks the line at NaN, so a
                    # scrape gap reads as a gap instead of a bridged segment.
                    plotted = [float("nan") if y is None else y for y in ys]
                    self.lines[(prefix, system, kind)].set_data(xs, plotted)
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
            self.headline_text.set_color(COLORS["mars"] if improvement > 0 else "#e66767")
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
        anim.save(str(output), writer=animation.FFMpegWriter(fps=fps, bitrate=6000))
        plt.close(dashboard.figure)
        return output

    fallback = output.with_suffix(".gif")
    print(f"ffmpeg not found; writing {fallback} instead")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd benchmark/react_agent && python3 -m pytest tests/test_render_video.py -v`
Expected: 6 passed

- [ ] **Step 5: Render from the fixture runs and look at the result**

```bash
cd benchmark/react_agent
python3 render_video.py --runs-dir runs --output ../../outputs/react-serving-replay.mp4 --speed 4
```

Then extract a mid-video frame and **open it**:

```bash
ffmpeg -y -ss 00:00:20 -i ../../outputs/react-serving-replay.mp4 -frames:v 1 /tmp/frame.png
```

Inspect for: label collisions, bar numbers overflowing the panel, the y-axis
rescaling jarringly between frames, the legend overlapping the x-axis, and the
scrolling window being full at the first frame. The validator checks colour, not
layout — this step is the layout check.

- [ ] **Step 6: Commit**

```bash
git add benchmark/react_agent/render_video.py benchmark/react_agent/tests/test_render_video.py
git commit -m "Add headless scrolling dashboard renderer

Agg backend, no display required. The trailing window is already full at the
first frame using the run's real warm-up segment, so nothing is synthesized to
achieve the live-dashboard look. Gaps become NaN so matplotlib breaks the line
rather than bridging a missing scrape. Colour encodes system, line style
encodes metric; palette validated at worst adjacent CVD deltaE 41.3."
```

---

## Task 12: Operator runbook and Prometheus prerequisite

**Files:**
- Rewrite: `benchmark/react_agent/README.md`
- Modify: `deploy/docker/prometheus/prometheus.yml`

**Interfaces:**
- Consumes: everything above.
- Produces: the documented procedure an operator follows on experiment day.

- [ ] **Step 1: Add the 1s scrape override to the local Prometheus config**

The work-server config is not in this repo, but the local one must match so the
compose stack reproduces the capture conditions. Add to
`deploy/docker/prometheus/prometheus.yml`, after the `react-benchmark-replay` job:

```yaml
  # vLLM exposes /metrics natively. 1s here, not in `global`, because the node,
  # DCGM, and PCM jobs are high-cardinality and gain nothing from sub-15s
  # scraping. The benchmark's 60s scrolling window needs 60 points, not 4.
  - job_name: vllm-exporter
    scrape_interval: 1s
    file_sd_configs:
      - files:
          - /etc/prometheus/file_sd/vllm-exporter.yml
```

- [ ] **Step 2: Verify the config parses**

Run:
```bash
docker run --rm -v "$PWD/deploy/docker/prometheus/prometheus.yml:/p.yml" \
  --entrypoint promtool prom/prometheus:latest check config /p.yml
```
Expected: `SUCCESS`. If `file_sd` files are absent locally the check may warn about
missing files — a warning is acceptable, a parse error is not.

- [ ] **Step 3: Rewrite the README**

Replace `benchmark/react_agent/README.md` entirely:

```markdown
# ReAct serving benchmark

Compares MARS, LMCache, Mooncake, and recompute on ReAct agent serving when only
one system can occupy the vLLM host at a time.

## How the runs get onto one axis

Each run happens at a different wall-clock time, and each system takes a
different amount of time to become ready. So wall-clock start is not the anchor.

Instead, `t=0` is derived from the data: the first timestamp at which
`vllm:time_to_first_token_seconds_count` rises and keeps rising for
`--anchor-sustain` seconds (default 10). Startup delay cancels, because each run
anchors on its own first served token.

**You do not need to time anything.** `--start` and `--duration` define a search
window that only has to *contain* the run. Pad it generously.

## Data flow

    vLLM /metrics  (192.168.3.x:8000, native endpoint -- not logs)
          |  Prometheus PULLS every 1s, continuously
          v
    Prometheus on solab-p7  (15 day retention)
          |  extract_run.py queries query_range, any time afterwards
          v
    runs/<system>/<run-id>.csv  +  .json manifest
          |
          v
    render_video.py  ->  outputs/react-serving-replay.mp4

`extract_run.py` never contacts the GPU host. By extraction time that machine can
be powered off or running something else entirely.

## Prerequisites, before any experiment

1. The vLLM job must be scraped at `scrape_interval: 1s`. Prometheus cannot
   collect retroactively, so this must be in place *before* the runs.
2. Retention is 15 days. **Extract each run the same day you run it** -- the CSV
   is the durable record, after which retention stops mattering.

## Running an experiment

1. Start one serving system and its ReAct workload. Let it run ~360 seconds.
2. Note roughly when you started. "Around 10am" is precise enough.
3. Extract:

       python3 extract_run.py --system mars --source prometheus \
           --prometheus-url http://solab-p7:9090 \
           --target solab-x3 \
           --model openai/gpt-oss-20b \
           --start 2026-08-01T09:55:00-07:00 --duration 900

   `--target` is required. Prometheus scrapes several vLLM hosts; without it the
   histogram would aggregate across machines and report a latency describing none
   of them. Extraction aborts if the window still resolves to more than one host.

4. Repeat for `lmcache`, `mooncake`, `recompute`.

## Trying it without real data

    python3 -m fixtures.generate --out fixtures
    for s in mars lmcache mooncake recompute; do
      python3 extract_run.py --system "$s" --source fixture \
        --fixture "fixtures/$s" --target solab-x3
    done
    python3 render_video.py --output ../../outputs/react-serving-replay.mp4

## What is measured

The headline improvement uses **mean** TTFT (`rate(_sum)/rate(_count)`), which is
exact. p95 is charted alongside but not claimed: vLLM's histogram places this
deployment's p95 (~3.7s) inside a 2.5s-wide bucket, so a realistic improvement is
smaller than the quantization. See the design doc for the full argument.

Also captured, not charted: queue time, prefill time, external prefix cache hit
ratio, and recomputed prompt tokens. These decompose TTFT into queueing versus
prefill, which is what lets the result be defended.

`ext_cache_hit_ratio` is empty for `recompute` -- an absent KV store has an
undefined hit rate, not a zero one.

## Grafana

`replay_exporter.py` and `grafana/react-serving-benchmark.json` still work for
interactive viewing over an SSH tunnel. They are no longer the video path; the
renderer is headless and needs no browser.

## Tests

    python3 -m pytest
```

- [ ] **Step 4: Verify the documented fixture flow works verbatim**

Run each command block from the "Trying it without real data" section in order.
Expected: four CSVs, then a video file at `outputs/react-serving-replay.mp4`.

- [ ] **Step 5: Run the full suite**

Run: `cd benchmark/react_agent && python3 -m pytest`
Expected: 75 passed (6+8+7+6+9+6+7+11+9+6).

- [ ] **Step 6: Commit**

```bash
git add benchmark/react_agent/README.md deploy/docker/prometheus/prometheus.yml
git commit -m "Document the extraction runbook and add the 1s vLLM scrape job

Leads with the two things that are unrecoverable if missed: the scrape interval
must be set before the experiments, and Prometheus retention is 15 days.
Explains that extraction is decoupled from experiment time, which is the point
most likely to be misread."
```

---

## Verification

After Task 12, confirm end to end:

- [ ] `cd benchmark/react_agent && python3 -m pytest` — all green.
- [ ] Fixture pipeline runs clean from an empty `runs/`.
- [ ] A mid-video frame has been extracted and **visually inspected** for layout defects.
- [ ] `git log --oneline` shows one commit per task.
- [ ] The opening frame's scrolling window is full, and no plotted point is synthetic.

## Open items carried from the spec

These do not block implementation; each surfaces on the first real extraction.

1. `vllm:external_prefix_cache_*` and `vllm:prompt_tokens_recomputed_total` are
   confirmed to exist by name, but it is unverified whether LMCache and Mooncake
   populate them. If they do not, the mechanism columns will be empty — the
   headline is unaffected. Check with
   `curl -s http://<vllm-host>:8000/metrics | grep external_prefix` during the
   first run.
2. The `server` label is assumed present on vLLM series in p7's TSDB. It comes
   from `file_sd`, but only the raw endpoint has been observed, never a query
   result. If absent, `--target` should filter on `instance` instead — a one-line
   change in `PrometheusSource._selector`.
3. `--anchor-sustain 10` is a default, not a measured value. Confirm the anchor
   lands where expected on the first real run before trusting all four.
