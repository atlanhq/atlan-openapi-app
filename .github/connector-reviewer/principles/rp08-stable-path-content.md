---
id: RP08
applies-to: [app]
---
# API path attributes follow SPEC.md and are identical for an unchanged spec

Principle: The attributes mapped onto `APISpec` and `APIPath` follow the mapping in `SPEC.md`:
the tracked methods, `available_operations` in sorted order, and the method and summary table in
`description` with path-level and operation-level descriptions around it. The same spec document
always produces byte-identical attribute values.
Why: Publish compares each asset with the last run. A value that depends on dict order, a random
id or formatting drift makes every API path on every tenant look changed on every run. A mapping
change that drops a field, as an earlier version did with operation descriptions, loses
documentation customers wrote into their specs.
Ask:
- Can any mapped value now differ between two runs on the same document (set or dict order, an unsorted list, a timestamp or id inside an attribute)?
- Does the diff drop or truncate a field `SPEC.md` maps, or change the description format, without updating `SPEC.md` and saying what existing assets will show?
- Does a change to the tracked methods (adding `head`, `options` or `trace`) come with the same change in `SPEC.md` and the tests?
Don't flag: how qualified names are built (`P028`, and FP02 for identity), serialisation through
the SDK (`P052`), sync metadata stamped per run (`lastSyncRun`, `lastSyncRunAt`), which changes
by design.
Severity: High when a mapping change drops customer documentation from every API path. Medium for
churn that makes unchanged assets look updated on every run.
