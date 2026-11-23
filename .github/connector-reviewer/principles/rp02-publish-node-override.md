---
id: RP02
applies-to: [contract, generated]
---
# The hand-written publish node keeps every argument the default node sets

Principle: `contract/app.pkl` replaces the toolkit's publish node to add `assertion_only_enabled`.
A replacement is not merged with the default, so it must keep restating every argument the default
node sets: the connection-cache and current-state flags today, and anything a newer toolkit adds.
Why: A flag missing from the override fails nothing. Publish just stops writing the connection cache
or the current-state base, and the downstream apps that read them (lineage, popularity) go stale
on every tenant with no error.
Ask:
- Does the diff remove or rename an argument of the `publish` node in `extraNodes`, or change its value from `true`?
- If the toolkit version moves, does the regenerated manifest's publish node still carry every argument the toolkit's default `PublishNode` sets?
- Does a new argument point at a JSONPath that `run()` always fills in, on both connection paths?
Don't flag: a JSONPath that names a field the Output does not declare (`K006`), stale or hand-edited
generated files, which conformance and the freshness check catch. Do not review the generated
`manifest.json` line by line: it follows the contract.
Severity: High when a cache or current-state flag is dropped or turned off. Medium for a new
argument whose value is not set on every path.
