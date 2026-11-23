---
id: RP05
applies-to: [app]
---
# Every valid OpenAPI document shape still produces its paths

Principle: Extraction accepts the document shapes customers really publish and maps each one fully:
Swagger 2.0 (`swagger` key) and OpenAPI 3.0 and 3.1, operations under `webhooks` as well as
`paths`, keys present with a `null` value, JSON or YAML told apart by content type or file suffix,
and ZIP archives of several documents.
Why: An enterprise spec with `"paths": null` once crashed extraction, and a 3.1 spec that put its
operations under `webhooks` produced no API paths at all. Code that works on the Petstore fixture
can fail on, or silently empty, real specs.
Ask:
- Does new parsing use `.get(key, default)` on a key a spec may set to `null`, or call a dict method on a value that may be a list, a string or `null`?
- Does the diff read only `paths`, only OpenAPI 3 keys, or only JSON, where the code now also handles `webhooks`, `swagger` or YAML?
- Can a document that parses, or a ZIP entry, now be skipped with only a log line instead of a typed error or a count the run reports?
Don't flag: typed errors for unparseable or title-less documents, which are deliberate. Conformance
reports HTTP failures returned as empty results (`E020`) and untyped raises (`E012`). Do not ask
for support of a spec feature the app has never mapped, such as `$ref` path items.
Severity: High when a valid spec shape can crash the run or yield fewer API paths under a success
status. Medium when an unusual shape fails loudly with an unclear message.
