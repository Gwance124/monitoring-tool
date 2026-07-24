# Fixture scrapes

Synthetic vLLM `/metrics` scrapes, one file per second, named
`<unix_timestamp>.prom`.

**The scrapes are not committed — generate them before anything else:**

    cd benchmark/react_agent
    python3 -m fixtures.generate --out fixtures

This writes 2076 files across four system directories, which `.gitignore`
excludes. The output is deterministic under `--seed`, so storing it would put
16MB into every clone forever to save one command.

Structure (metric names, label sets, histogram bucket boundaries) comes from a
live capture of `openai/gpt-oss-20b` taken 2026-07-24. Values are synthetic.

Four histograms are emitted per system: `vllm:time_to_first_token_seconds`,
`vllm:e2e_request_latency_seconds`, `vllm:request_queue_time_seconds`, and
`vllm:request_prefill_time_seconds`, all sharing the same bucket boundaries.
Queue delay is generated small and near zero, rising slightly under load.
Prefill time is generated as a fraction (30-60%) of that same request's TTFT
observation, so prefill can never exceed TTFT for the same request or at the
aggregate p95 -- prefill is physically part of what TTFT measures.

Deliberate shapes, each covering a failure this pipeline must survive:

| Shape | At | Guards against |
|---|---|---|
| Health-check burst, 3 requests | t+20s | An anchor pinned 80s early |
| Real workload begins | t+100s | — |
| Warm-up ramp | t+100..160s | Cold-start transients in the headline |
| Missing scrape | t+180s | Gaps being interpolated or zero-filled |
| `recompute` with zero cache queries | throughout | An undefined hit ratio reported as 0.0 |

MARS is generated with the lowest mean TTFT and `recompute` the highest, so
ordering assertions are meaningful.
