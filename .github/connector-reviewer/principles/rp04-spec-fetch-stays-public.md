---
id: RP04
applies-to: [app]
---
# A customer-supplied spec URL can only reach a public HTTPS host

Principle: Every outbound request to a configured spec URL first passes `validate_spec_url`
(HTTPS, a hostname that resolves only to public addresses) and never follows a redirect. Preflight
and extraction both make the request through `OpenAPIApiClient`, so the check cannot be skipped
on one surface.
Why: The app makes an HTTP request from inside the platform to any URL a tenant types in. Without
this boundary, a spec URL or a redirect from a public host can reach internal services or a cloud
metadata endpoint, and the response may come back in an error or as published assets.
Ask:
- Does the diff add an HTTP call to a configured URL (a new import type, a `$ref` follow, a retry, a second client) that does not pass `validate_spec_url` first?
- Does it turn on `follow_redirects`, or follow a `Location` header by hand?
- Does it relax the address check (allow HTTP, private ranges or a per-connection opt-out) without the PR saying who approved that product decision?
Don't flag: the CLOUD path reading a downloaded local file, which makes no network call. No
conformance rule or CI gate checks this boundary, so there is nothing to leave to them.
Severity: High for any path that lets a configured URL or redirect reach a private, loopback,
link-local or metadata address. Medium when the check stays but runs on only one of the two surfaces.
