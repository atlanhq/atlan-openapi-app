# Mutation testing (three-lane pilot)

Mutation testing seeds small faults ("mutants") into `app/` and checks the
tests catch them. A **surviving** mutant is a bug class that would ship with a
green build — a direct measure of test *meaningfulness* that line coverage
cannot give. This app is the pilot for validating meaningfulness across
**all three test tiers**, not just unit. Ticket: BLDX-1562.

## Why three lanes

A single mutation run uses one kill-signal. But unit, integration, and e2e
tests defend different things, so we run one lane per tier — each engineered
around that tier's cost:

| Lane | Mutates | Kill-signal | Answers | Cadence |
|---|---|---|---|---|
| **unit** | all of `app/` | mocked unit suite (~30s) | do unit tests defend the logic? | weekly |
| **integration** | `app/connector.py` | `mutation_smoke` DAG subset via embedded runtime (~6s) | do integration tests defend the orchestration unit tests can't reach? | weekly |
| **e2e (sentinels)** | hand-picked patches | the e2e suite vs a live tenant | would e2e even notice representative real bugs? | per-release |

## One config, lane by env var

mutmut allows only one `[tool.mutmut]` block, and the lanes differ only in
*which tests* provide the kill-signal. Rather than rewrite the config per lane
(fragile — a killed process leaves `pyproject.toml` corrupted), the lane is
chosen by the **`MUTATION_LANE` env var**, read by `tests/conftest.py` in
`pytest_collection_modifyitems`:

- `unit` → collection narrowed to `tests/unit`
- `integration` → collection narrowed to tests marked `@pytest.mark.mutation_smoke`
- unset (a normal `pytest` run) → no-op, behaviour unchanged

The static config selects the whole `tests/` tree and clears `addopts` so the
hook has full control. The env var is process-wide, so it survives mutmut's
`fork`, and there's nothing to restore if a run is killed.

## The `mutation_smoke` subset

The integration kill-signal is a curated subset (the CREATE-path DAG
assertions in `tests/integration/test_openapi.py`), not the whole integration
suite — deterministic and ~6s. The reuse test is deliberately excluded: it
spawns a second workflow and is not needed to detect orchestration faults.
Mark a test with `@pytest.mark.mutation_smoke` to add it to the signal.

## Lane 3 — sentinel drills

Per-mutant mutation of the e2e tier is off the table (~20–30 min/run against a
live tenant). Instead, `tests/mutation_sentinels/manifest.toml` lists a few
**sentinel** mutants — each a real bug class as a hand-written patch (robust to
mutmut key renumbering). `.github/scripts/mutation_sentinel.py` applies each,
runs its target tier, and requires the tests to go **red**. A sentinel that
stays green is a documented blind spot, with a concrete fix (add the
assertion). e2e sentinels self-skip where no live tenant is configured.

## Running locally

```bash
uv run --all-groups mutmut run                              # unit lane (default)
MUTATION_LANE=integration uv run --all-groups mutmut run "app.connector.*"
uv run --all-groups python .github/scripts/mutation_sentinel.py --tier integration
uv run --group mutation mutmut show <mutant-name>           # inspect a survivor
```

## Known coverage gap

`app/run_dev.py` (the local dev entrypoint) is exercised by **no** automated
tier — neither unit nor integration imports it. It is out of scope for the
mutation lanes and is a real, documented gap, not a mutation-testing artifact.
