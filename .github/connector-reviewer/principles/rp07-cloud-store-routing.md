---
id: RP07
applies-to: [app]
---
# The CLOUD path reads the bucket its credential selects, and both surfaces agree which one

Principle: Which object store a CLOUD import reads is decided in one place: `openapi_credential`
first, then the legacy `cloud_source` guid, and `has_valid_auth` picks the customer's store or
falls back to the tenant store. Preflight probes the same store extraction will read, and only a
failure on the customer's own store is reported to the customer through `classify_store_failure`.
Why: A credential that quietly fails `has_valid_auth` (a malformed `extra`, a renamed field) makes
the run read Atlan's tenant store instead of the customer's bucket. The run then finds nothing or
the wrong files. A tenant-store or gateway 401 blamed on the customer sends them to rotate a
credential that is fine.
Ask:
- Does the diff change the credential order, `has_valid_auth` or the field names it reads on one surface (preflight or `download_cloud_spec`) but not the other?
- Can a credential the customer meant for their own bucket now fall back to the tenant store, and does the run still say so in its log and the preflight row?
- Is a failure on the tenant store or the storage gateway now reported as a customer credential or permission error?
Don't flag: local credential routing and agent-mode handling, which conformance reports (`P053`,
`P037`), preflight gate input parity (`F010`). Typed-error mechanics are covered by FP09 and
pinned by unit tests.
Severity: High when a run can read the wrong bucket, or report a customer fault for Atlan's store.
Medium when preflight and extraction would disagree only in their messages.
