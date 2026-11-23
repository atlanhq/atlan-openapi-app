---
id: RP03
applies-to: [app]
---
# A spec URL is treated as a secret wherever it is written down

Principle: `spec_url` is a plain config field, but customers often supply a pre-signed URL (a SAS
token, an S3 presigned GET, a token in the query string, `user:pass@` userinfo). Every log line,
error message, evidence field and `cause` that mentions a spec URL goes through `redact_url`, or
through `_redacted_cause` for an exception whose text embeds the URL.
Why: Error messages and evidence fields reach workflow history, the Automation Engine and fleet
dashboards unredacted. The SDK's sanitiser looks for secrets by shape, and a signed URL does not
have that shape. httpx puts the full request URL into its exception text, so passing one straight
through leaks the signature.
Ask:
- Does a new or changed message, log call, evidence field (`endpoint`, `resource`, `resource_identifier`) or metric take the spec URL, or an httpx exception or response, without redaction?
- Does a change to `redact_url` let any string with a query or userinfo come back unchanged, including URLs with no host or an unparseable port?
- Does the URL reach a traceback through `exc_info` or an unsevered chain in a frame that holds it?
Don't flag: secret-named values in logs (`L010`), raw exception text in messages, contract fields
and preflight output (`E015`, `E019`, `F014`). Local file paths from the CLOUD path carry no
credential and may be logged as they are.
Severity: High when a signed URL or its query can reach a log, history or a dashboard. Medium for
a path that is safe today only because nobody supplies a signed URL there yet.
