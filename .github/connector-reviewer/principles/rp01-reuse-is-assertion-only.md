---
id: RP01
applies-to: [app, contract]
---
# A REUSE run only adds to an existing connection; a CREATE run owns its connection

Principle: With `connection_usage=REUSE` the run targets a connection that other sources may share.
It emits no Connection row, sets `assertion_only_enabled=True` on every path, and rejects a missing
or unsubstituted `{{...}}` qualified name. With `CREATE` it emits the Connection row exactly once
and leaves `assertion_only_enabled=False`, so publish runs its normal full diff.
Why: Publish diffs a REUSE run against the whole connection, so without assertion-only mode it
archives every asset another source put there. A Connection row re-emitted from a bare qualified
name overwrites the existing connection. A CREATE run with no Connection row gets its connection
archived. The REUSE path has been rewritten three times over these failures.
Ask:
- Can any branch, early return or new import type reach the end of `run()` with REUSE and `assertion_only_enabled` false, or with CREATE and true?
- Does the diff emit, fetch or rebuild a Connection entity on the REUSE path, or drop the Connection row on the CREATE path?
- Does a change to `connection_usage` or its default in `contract/app.pkl` keep the code in `run()` and the publish node's `assertion_only_enabled` argument in step?
Don't flag: a publish-node JSONPath that names a field missing from the Output (`K006`), removed or
retyped Output fields (`B005`). The flag's presence in the serialised output is pinned by unit
tests. Do not flag the second and later transform calls passing `emit_connection=False`; that is
how a multi-spec CREATE run emits one Connection row.
Severity: High when a REUSE run can archive assets it does not own, or overwrite the existing
connection. Medium when the guard against an unselected connection gets weaker.
