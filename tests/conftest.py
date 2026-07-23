"""Global test configuration."""

import os

# mutmut sandbox compat (mutation-testing baseline): mutmut 3.6.0's
# record_trampoline_hit() resolves the relative [tool.mutmut] source_paths
# against the current working directory on every mutated-function call
# during its stats phase, so any test that chdirs crashes with
# FileNotFoundError. The resolve only feeds the max_stack_depth feature,
# which we leave at its disabled default — replace with the equivalent
# minus the resolve. Only active under `mutmut run`; plain pytest runs
# never import mutmut.
if os.environ.get("MUTANT_UNDER_TEST") is not None:
    import mutmut
    import mutmut.mutation.trampoline as _trampoline

    def _record_trampoline_hit_without_cwd_resolve(name: str) -> None:
        assert not name.startswith("src."), (
            "Failed trampoline hit. Module name starts with `src.`, which is invalid"
        )
        mutmut._stats.add(name)

    setattr(  # noqa: B010 — name is not exported; setattr avoids pyright private-import error
        _trampoline, "record_trampoline_hit", _record_trampoline_hit_without_cwd_resolve
    )


# --------------------------------------------------------------------------
# Mutation-testing lane selection (BLDX-1562)
#
# mutmut allows only one [tool.mutmut] config, but we run three lanes that
# differ only in which tests provide the kill signal. Rather than rewrite the
# config per lane (fragile: a SIGKILL — e.g. hitting a spend limit — leaves
# pyproject.toml corrupted), the lane is chosen by the MUTATION_LANE env var,
# which is process-wide and survives mutmut's fork. This hook narrows
# collection to the selected lane's tests; the static [tool.mutmut] config
# selects the whole tests/ tree and clears addopts so this hook has full
# control. Unset (a normal `pytest` run) => no-op, behaviour unchanged.
#
#   unit         -> tests/unit only (the fast, mocked tier)
#   integration  -> only tests marked `mutation_smoke` (deterministic DAG
#                   subset; excludes the reuse test that spawns a second
#                   workflow and hangs)
# --------------------------------------------------------------------------
def pytest_configure(config):  # noqa: ANN001, ANN201
    config.addinivalue_line(
        "markers",
        "mutation_smoke: deterministic integration test used as a mutation "
        "kill-signal for the integration lane (MUTATION_LANE=integration)",
    )


def pytest_collection_modifyitems(config, items):  # noqa: ANN001, ANN201
    lane = os.environ.get("MUTATION_LANE")
    if not lane:
        return

    kept, deselected = [], []
    for item in items:
        if lane == "unit":
            keep = "/unit/" in str(item.fspath).replace("\\", "/")
        elif lane == "integration":
            keep = item.get_closest_marker("mutation_smoke") is not None
        else:
            keep = True
        (kept if keep else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept
