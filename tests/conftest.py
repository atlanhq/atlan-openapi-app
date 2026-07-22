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

    _trampoline.record_trampoline_hit = _record_trampoline_hit_without_cwd_resolve
