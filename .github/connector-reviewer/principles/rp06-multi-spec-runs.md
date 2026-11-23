---
id: RP06
applies-to: [app]
---
# A run with several spec documents publishes all of them

Principle: One run can hold many documents: a ZIP archive, or a CLOUD prefix that matches several
files. `run()` transforms each one and delivers every output through `upload_refs` under its own
label. The first label stays `openapi_metadata.json`, and only the first output carries the
Connection row.
Why: The transform loop once overwrote its output reference on each pass, so only the last spec
reached publish. On a CREATE connection, full-diff publish then archived every asset from the
other specs while the run stayed green.
Ask:
- Does a loop over specs or transform results assign a single variable where it should append, or deliver only the first or last element?
- Does every output keep a distinct label, with the first unchanged, and is the Connection row still emitted exactly once across all outputs?
- Can two documents in one run now produce the same API spec name, so one silently replaces the other?
Don't flag: upload and delivery mechanics the SDK owns (`upload_refs` checking delivery against the
declaration), entity serialisation (`P052`) and the output file's declared schema (`K016`,
`K017`). The single-document URL path is covered by its own tests.
Severity: High when any document's assets can be left out of publish under a success status.
Medium when all are delivered but labels or the Connection row are duplicated.
