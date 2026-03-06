# AGENTS.md — Atlan AI Agent Guidelines

> **Version:** 4.0  
> **Last Updated:** 2026-02-11  
> **Applies To:** All AI agents (Claude, GPT, Copilot, Cursor, Cline, etc.) working on Atlan codebases  
> **Companion File:** See `CLAUDE.md` for the lean version optimized for Claude Code.

**All AI agents must follow these guidelines when generating, modifying, or reviewing code in Atlan repositories.**

---

## Project Overview

<!-- 
Teams: Add your project description, architecture overview, and key context here.
This section helps AI agents understand what this repo does and how it fits into Atlan's architecture.

Example:
- This is the [service-name] service, responsible for [purpose].
- It's a [Python/Go/Java/Node.js] microservice deployed on Kubernetes.
- It communicates with [other services] via [REST/gRPC/Kafka/Temporal].
- Key data stores: [PostgreSQL/Redis/Elasticsearch/ClickHouse].
-->

---

## Project Commands

<!-- 
Teams: Add your build, test, lint, and deploy commands here.
AI agents use these to validate changes and run tests.

Example:
- Build: `make build`
- Test: `make test`
- Lint: `make lint`
- Run locally: `make run`
- Docker build: `docker build -t service-name .`
-->

---

## Coding Conventions

<!-- 
Teams: Add language-specific style guides, naming conventions, and patterns here.

Example:
- Language: Python 3.11+
- Style: Black formatter, isort for imports, flake8 for linting
- Naming: snake_case for functions/variables, PascalCase for classes
- Error handling: Always use custom exception classes from `app/exceptions.py`
- Logging: Use structlog with `get_logger(__name__)`
-->

---

## Architecture Notes

<!-- 
Teams: Add key design decisions, service boundaries, data flow, and important patterns here.

Example:
- This service follows the hexagonal architecture pattern.
- All database access goes through the repository layer (`app/repositories/`).
- Auth is handled by the middleware in `app/middleware/auth.py` — do not create new auth flows.
- Tenant context is extracted in middleware and available via `get_current_tenant()`.
-->

---

## Security

> **This is the core section. All AI agents must follow these guidelines for every code change.**

### Owners & Contact

- **Security Team:** For questions, concerns, or proposed changes to `AGENTS.md` / `CLAUDE.md`, reach out to the **Atlan Security Team** (on Slack #collab-platform-security).
- **Manual Security Review:** If your changes are risky or touch critical security surfaces (auth flows, multi-tenant isolation, secrets management, new external integrations, new API endpoints with data access), **request a manual security review** from the Security team before merging.
- **Escalation:** When in doubt about whether a change needs security review, err on the side of requesting one. It's always better to ask than to ship a vulnerability.

---

### Quickstart (Read This First)

**Use this section in 60 seconds:**

1) Identify what you're changing: **Backend/API**, **Frontend**, **K8s/Helm**, **CI/CD**, **Shell**, **Config**, **IaC**, **Docker**, **Dependencies**, **AI/LLM**, **Data/Workers**.  
2) Apply **Security Invariants** (below) to **every** change.  
3) Use the **Code Type Security Matrix** to jump to only the relevant subsections.  
4) If you find issues, use the severity rules:
   - **CRITICAL:** must fix before proceeding (**block**)  
   - **HIGH/MEDIUM/LOW:** explain clearly + recommend fix (don't block)
5) For **MEDIUM+** issues, use the **🔒 SECURITY REVIEW** template (§Review Output Format).

#### Tag Legend (used throughout)
- **[MUST]** required for security/compliance  
- **[REDLINE]** explicitly forbidden unless Security approves  
- **[SHOULD]** strongly recommended, best practice  
- **[NICE]** defense-in-depth improvement

---

### Security Invariants (Always Apply)

- **[MUST] No secrets in code or logs** (keys, tokens, passwords, private URLs, customer credentials).  
- **[MUST] Multi-tenant isolation is non-negotiable:** `tenant_id` comes from **authenticated context only**, never request input.  
- **[MUST] Parameterize data access:** never concatenate user input into SQL/queries/filters.  
- **[MUST] Authentication & authorization must be real:** avoid "phantom auth" (imported but unused middleware/decorators).  
- **[MUST] Avoid wildcards:** no `CORS: *`, no IAM `Action:"*"`, no K8s RBAC `resources:["*"]`, no GitHub `write-all` unless explicitly justified.  
- **[MUST] Don't execute untrusted input:** no `eval`, no unsafe deserialization, no command injection, no untrusted input in CI `run:` blocks.  
- **[MUST] Pin the supply chain:** actions to **SHA**, images to **version/SHA**, avoid `latest`, verify new deps exist and are reputable.  
- **[MUST] Safe error handling:** clients get generic errors; internal details stay server-side.  
- **[MUST] Log safely:** never log auth tokens/cookies, secrets, or sensitive bodies by default (see "Logging Redlines").
- **[MUST] Validate outbound URLs (SSRF):** validate and allowlist all outbound URLs constructed from user input; deny internal/private IP ranges (10.x, 172.16-31.x, 192.168.x), localhost, and cloud metadata endpoints (169.254.169.254, fd00::, etc.).
- **[MUST] Rate limit abuse-prone endpoints:** all authentication, password reset, token generation, and API endpoints that access sensitive data must have rate limiting before merging.
- **[MUST] New API endpoints must ship secure:** every new API endpoint must have authentication, authorization, input validation, and rate limiting before merging. Do **not** accept patterns like "auth can be added later" or "we'll add validation in a follow-up" — security is not a follow-up.
- **[MUST] No `.env` files with real secrets in version control.** `.env`, `.env.local`, `.env.production`, and similar files must be in `.gitignore`. `.env.example` / `.env.sample` files must contain only placeholder values (e.g., `API_KEY=changeme`, `DB_PASSWORD=<replace-me>`), never real credentials — even "dev" or "staging" ones. **Block any commit that adds or modifies a `.env` file containing values that appear to be real secrets.**
- **[MUST] All code must reside in approved GitHub organizations** (e.g., AtlanHQ). Code in personal GitHub accounts lacks SCA scanning, secret detection, branch protection, and audit visibility. **Flag any reference to personal repos, personal GitHub accounts, or code imports from non-organizational sources.**

#### Secret Discovery Protocol

**If you discover a secret in code, config, logs, or CI output — treat it as a CRITICAL incident:**

1. **Do NOT commit, push, or log the secret further.**
2. **Flag immediately** using the 🔒 SECURITY REVIEW format with severity `CRITICAL`.
3. **Recommend:** Revoke/rotate the exposed credential immediately.
4. **Check history:** If the secret was in a previous commit, it's already in git history — rotation is mandatory even if removed from HEAD.
5. **Notify Security team** — exposed secrets require incident tracking.

**Common secret patterns to watch for:**
- API keys: strings matching `AKIA[0-9A-Z]{16}` (AWS), `sk-[a-zA-Z0-9]{48}` (OpenAI), `ghp_[a-zA-Z0-9]{36}` (GitHub PAT), `xoxb-` / `xoxp-` (Slack)
- Private keys: `-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----`
- Connection strings: `postgres://user:password@`, `mongodb+srv://user:password@`, `redis://:password@`
- JWT secrets / signing keys, base64-encoded credentials, bearer tokens in config files
- Any value in a variable named `*_SECRET`, `*_KEY`, `*_TOKEN`, `*_PASSWORD`, `*_CREDENTIAL` that isn't a placeholder

#### Data Classification

**[MUST]** When an agent encounters new data fields (in models, schemas, APIs, logs), it must assess and flag fields that appear to contain sensitive information:

| Classification | Examples | Agent Action |
|---------------|----------|-------------|
| **PII** | name, email, phone, address, SSN, date of birth | Flag for review; ensure encryption at rest, masked in logs, tenant-scoped |
| **Financial** | credit card, bank account, billing info | Flag as HIGH; PCI-DSS requirements may apply |
| **Authentication** | passwords, tokens, API keys, session IDs | Flag as CRITICAL if stored/logged improperly |
| **Health** | PHI, medical records, diagnoses | Flag as CRITICAL; HIPAA requirements apply |
| **Tenant metadata** | tenant_id, org config, feature flags | Ensure tenant-scoped access; never expose cross-tenant |

**[MUST]** If a new field appears to contain sensitive data, recommend adding it to the data classification registry and applying appropriate controls (encryption, masking, access restrictions).

---

### Team Profiles (Fast Routing)

- **Backend/API & Workers:** read §Backend, §Multi-Tenant, §Dependencies (if adding libs)  
- **Frontend:** read §Frontend + cookies/CSRF note + §Dependencies  
- **DevOps / Platform (K8s/Helm/Docker):** read §Helm/K8s, §Docker, §IaC  
- **CI/CD:** read §CI/CD + §Dependencies (supply chain)  
- **AI/LLM:** read §AI/LLM + §Multi-Tenant + "Logging Redlines"  
- **Config-only changes:** read §Configuration + relevant platform section (K8s/IaC/CI)

---

### Core Principles

1. **[MUST] Security by default** — the secure path should be the easy path.  
2. **[MUST] Review before implement** — analyze implications before writing code (use STRIDE).  
3. **Explain, don't block** — flag issues with severity and impact.  
   - **Exception:** **[MUST] CRITICAL issues must be fixed before proceeding** (block).  
4. **[MUST] Think multi-tenant** — every data access path enforces tenant isolation.  
5. **[SHOULD] Assume compromise** — design for defense in depth.  
6. **[MUST] Least privilege everywhere** — minimal permissions for identities and network paths.  
7. **[MUST] No secrets in code** — zero tolerance for hardcoded credentials.  
8. **[MUST] Compliance is not optional** — SOC2, GDPR, HIPAA requirements apply.

#### Logging Redlines (Universal)
- **[REDLINE]** Never log: access tokens, refresh tokens, session cookies, API keys, Authorization headers, private keys.  
- **[MUST]** Avoid logging full request/response bodies by default (especially for auth and PII paths).  
- **[MUST]** Always include `tenant_id` (from auth context) in structured logs for audit correlation.  
- **[SHOULD]** Use automatic masking for known secret patterns and headers.

---

### Code Type Security Matrix

Use this to jump only to relevant subsections.

| Code Type | Priority Subsections | Key Risks |
|----------|---------------------|-----------|
| **Backend/API** (Python, Java, Go, Node.js) | §Backend, §Multi-Tenant, §Dependencies | SQLi, auth bypass, SSRF, tenant isolation, mass assignment |
| **Helm Charts / K8s YAML** | §Helm/K8s, §Docker | Privileged containers, secret exposure, RBAC escalation, DoS |
| **GitHub Actions / CI/CD** | §CI/CD, §Dependencies | Workflow injection, secret leakage, unpinned actions, excessive perms |
| **Shell Scripts** | §Shell | Command injection, unsafe temp files, creds in args, unquoted vars |
| **Config Files** (YAML/JSON/TOML/.env) | §Configuration | Hardcoded secrets, insecure defaults, exposed ports, debug enabled |
| **Frontend** (React/Vue/JS/TS) | §Frontend, §Dependencies | XSS, token exposure, open redirects, CSP bypass |
| **Terraform / CloudFormation** | §IaC | Public buckets, overpermissive IAM, unencrypted resources, open SGs |
| **Dependency Updates** | §Dependencies | Typosquatting, known CVEs, lockfile manipulation, supply chain |
| **AI/LLM integrations** | §AI/LLM, §Multi-Tenant | Prompt injection, data leakage, PII exposure, unsafe output usage (never pass LLM output directly to `eval`, SQL, shell commands, or `innerHTML` — leads to code injection, command injection, SQLi, and XSS) |
| **Dockerfiles** | §Docker, §IaC | Running as root, secrets in layers, large attack surface, unpinned base |

---

### Security Review Process

> **For risky or critical changes**, request a **manual security review** from the Security team before merging. See [Owners & Contact](#owners--contact).

#### When to Review
**Always review** when changes touch:
- user input handling, auth/authz, any API route, DB queries  
- external calls (HTTP/gRPC/queues), filesystem operations  
- secrets, logging/observability pipelines, caching  
- K8s RBAC/service accounts/security contexts  
- CI/CD workflows, container builds, IaC  
- CORS/security headers, redirects  
- LLM prompts, retrieval, tool execution, output rendering

**Skip review** for:
- comment-only changes  
- pure documentation (non-config, non-code)  
- renames with no behavior change  
- test-only changes with no prod impact  
- changes already explicitly security-reviewed

#### Review Output Format

**Standard format (for MEDIUM+):**
```txt
🔒 SECURITY REVIEW

Issue: [Brief description]
Severity: [CRITICAL | HIGH | MEDIUM | LOW]
Location: [File:Line or function name]
Category: [STRIDE category]
Risk: [Specific to Atlan context]
Impact: [What attacker gains]

Example Attack Scenario:
[How exploited]

Recommended Fix:
[Concrete fix]

Would you like me to:
1) Implement the secure version (recommended)
2) Proceed with the current approach (you'll fix it later)
3) Skip this for now
```

**Quick format (LOW / rapid dev):**
```txt
⚡ Security note: [one-line risk] → [one-line fix]
```

#### Severity Calibration (includes CVE handling)

| Severity | Criteria | Agent Response |
|----------|----------|----------------|
| **CRITICAL** | RCE, full data breach, credential exposure, cross-tenant access, complete auth bypass, CRITICAL CVEs in dependencies | **Block** — must fix before proceeding |
| **HIGH** | Auth bypass on specific endpoints, privilege escalation, significant leak, tenant isolation gap, HIGH CVEs in dependencies | **Block** — must fix before merging |
| **MEDIUM** | Info disclosure via errors, missing controls, weak configs, CORS issues | **Flag** — can be follow-up |
| **LOW** | Best practice gaps, defense-in-depth improvements | **Note** — mention briefly |

---

### Multi-Tenant Security

> **This is the most critical subsection for Atlan. Every data access path must enforce tenant isolation.**

#### The Non-Negotiable Rule
**[MUST] `tenant_id` must come from authenticated session/context, NEVER from request parameters, headers, or user input.**

```txt
tenantId = extractTenantId(authenticatedSession)

resource = query(filters={ id: resourceId, tenant_id: tenantId })

if not resource:
  return 404  // do not reveal existence with 403
return resource
```

#### Tenant Isolation Checklist (Apply everywhere)
- **[MUST] DB queries:** include `tenant_id` filter from auth context  
- **[MUST] Caches:** include tenant in cache keys (`tenant:{id}:…`)  
- **[MUST] Files/storage:** tenant-scoped prefixes/paths  
- **[MUST] Search:** mandatory tenant filter  
- **[MUST] Queues/workers:** messages tagged; consumers validate tenant  
- **[MUST] Webhooks:** verify destination belongs to sending tenant  
- **[SHOULD] Logs:** always tag with tenant_id from auth context

#### Common Anti-Patterns to Flag
```txt
❌ tenant_id = request.params["tenant_id"]     // attacker can swap tenant
❌ SELECT * FROM resources WHERE id = ?        // missing tenant filter
❌ /api/tenants/{tenant_id}/resources          // who verifies tenant ownership?
✅ tenant_id from auth + enforced in every query
```

---

### Backend & Server Code

<details>
<summary><strong>Expand Backend & Server Code</strong></summary>

#### Input Handling
- **[MUST]** validate input against schema/allowlists  
- **[MUST]** apply length limits to reduce DoS risk  
- **[MUST]** reject invalid input early (don't "sanitize" dangerous patterns into acceptability)

#### Database Security
- **[MUST]** parameterize queries / use ORM safely  
- **[MUST]** include tenant filter in every access path  
- **[REDLINE]** string concatenation for queries with user input

#### Authentication
- **[MUST]** validate JWT signatures and claims (`exp`, `nbf`, `iss`)  
- **[MUST]** tenant context extracted from authenticated session only  
- **[SHOULD]** prefer httpOnly + secure + sameSite cookies for sessions

#### Authorization
- **[MUST]** verify resource ownership (tenant + user permissions)  
- **[SHOULD]** return 404 for unauthorized resources (avoid existence leak)  
- **[SHOULD]** audit-log authorization failures

#### Error Handling
- **[MUST]** server logs can include stack traces; client responses must not  
- **[REDLINE]** returning SQL queries, stack traces, file paths, internal IPs, service names to clients

#### SSRF & Outbound HTTP Calls (Common in microservices)
- **[MUST]** allowlist outbound hosts/domains when user-controlled URLs are involved  
- **[MUST]** block link-local / metadata ranges (e.g., 169.254.169.254) and internal admin planes  
- **[MUST]** enforce timeouts + max response size  
- **[SHOULD]** restrict redirects; validate scheme (https only)

#### File Uploads (if applicable)
- **[MUST]** enforce size limits and content-type checks (don't trust client mime alone)  
- **[SHOULD]** store outside web root; randomize names; scan if required by policy  
- **[SHOULD]** avoid parsing complex formats server-side without sandboxing

#### Unsafe Deserialization
- **[REDLINE]** unsafe YAML/JSON deserialization modes, Java native serialization, or eval-like parsing of untrusted data.

#### Rate Limiting
- **[MUST]** rate limit login/reset/token and all new API endpoints (tenant + user + IP keys)  
- **[MUST]** return 429 with Retry-After  
- **[REDLINE]** shipping new API endpoints without rate limiting — "we'll add it later" is not acceptable

#### New API Endpoint Checklist
Every new API endpoint **[MUST]** have all of the following before merging:
- [ ] Authentication (JWT validation, session check, or equivalent)
- [ ] Authorization (tenant ownership + user permissions verified)
- [ ] Input validation (schema validation, allowlists, length limits)
- [ ] Rate limiting (tenant + user + IP keyed)
- [ ] Tenant isolation (tenant_id from auth context in every query)
- **[REDLINE]** Do not accept "auth/validation/rate-limiting can be added later" — these are shipping requirements, not follow-ups.

</details>

---

### Helm Charts & Kubernetes Manifests

<details>
<summary><strong>Expand Helm Charts & Kubernetes Manifests</strong></summary>

#### Container Security Context
- **[MUST]** set:
```yaml
securityContext:
  runAsNonRoot: true
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
```

#### Service Accounts & RBAC
- **[MUST]** prefer Role over ClusterRole unless justified  
- **[MUST]** scope secret access using `resourceNames` (no wildcards)  
- **[MUST]** minimum verbs (`get` over `list/watch/delete`)  
- **[MUST]** do not use `default` service account

#### Secrets in Helm
- **[REDLINE]** secrets in `values.yaml` / templates / ConfigMaps  
- **[SHOULD]** use ExternalSecret/SealedSecret/Vault/Secrets Manager patterns

#### Network & Exposure
- **[MUST]** enforce TLS on ingress where applicable  
- **[SHOULD]** avoid NodePort/LoadBalancer unless intentional and reviewed  
- **[SHOULD]** set resource requests/limits to reduce DoS risk  
- **[NICE]** NetworkPolicies for sensitive workloads (default deny + explicit allow)

#### Image Security
- **[MUST]** no `latest` tags; pin versions/digests  
- **[SHOULD]** trusted registries; minimal base images  
- **[REDLINE]** `privileged: true` or `hostNetwork: true` unless Security-approved

</details>

---

### CI/CD & GitHub Actions

<details>
<summary><strong>Expand CI/CD & GitHub Actions</strong></summary>

#### Workflow Injection (CRITICAL)
- **[MUST]** treat PR metadata as attacker-controlled  
- **[REDLINE]** untrusted input directly inside `run:` scripts

✅ Safer pattern:
```yaml
- env:
    PR_TITLE: ${{ github.event.pull_request.title }}
  run: echo "Processing PR: $PR_TITLE"
```

#### pull_request_target (High Risk)
- **[REDLINE]** Checking out PR head code in `pull_request_target` when secrets are available.
  - If you must use it: avoid secrets, sandbox, and never run untrusted code.

#### Action Pinning
- **[MUST]** pin third-party actions to full commit SHA (tags can move)

#### Secret Handling
- **[MUST]** never echo secrets  
- **[SHOULD]** use OIDC over long-lived tokens  
- **[SHOULD]** restrict secret access to only required workflows/environments

#### Workflow Permissions
- **[MUST]** minimum permissions block  
- **[REDLINE]** `permissions: write-all` unless explicitly justified

</details>

---

### Shell Scripts & Automation

<details>
<summary><strong>Expand Shell Scripts & Automation</strong></summary>

#### Script Hardening
- **[MUST]**
```bash
#!/usr/bin/env bash
set -euo pipefail
```

#### Command Injection Prevention
- **[MUST]** quote variables, use `${VAR:?}` for required vars  
- **[REDLINE]** `eval` with any user-controlled content

#### Credentials
- **[MUST]** avoid secrets in CLI args (visible in process list)  
- **[SHOULD]** pass via env vars or secure files with correct permissions

#### Temp Files
- **[MUST]** use `mktemp` + cleanup trap  
- **[REDLINE]** predictable temp paths for sensitive data

#### Network Operations
- **[MUST]** no `curl -k` in production  
- **[REDLINE]** `curl | bash` (download → verify checksum/signature → execute)

</details>

---

### Configuration Files

<details>
<summary><strong>Expand Configuration Files</strong></summary>

#### Common Risks
- **[MUST]** no secrets in committed config  
- **[MUST]** no debug mode in production  
- **[SHOULD]** avoid binding services to `0.0.0.0` unless needed  
- **[SHOULD]** ensure least-privileged defaults

#### CORS
- **[REDLINE]** `allowed_origins: ["*"]` in production  
- **[MUST]** use explicit origins from configuration + allow_credentials rules when needed

#### Kong / Keycloak
- **[MUST]** verify auth plugins/flows are actually applied and ordered correctly  
- **[SHOULD]** avoid wildcard redirect URIs; protect admin APIs; ensure brute-force protection

</details>

---

### Frontend Code

<details>
<summary><strong>Expand Frontend Code</strong></summary>

#### XSS Prevention
- **[REDLINE]** `dangerouslySetInnerHTML` / `v-html` with user-controlled data  
- **[MUST]** sanitize rich text (e.g., DOMPurify or equivalent) if rendering HTML

#### Token & Session Handling
- **[MUST]** do not store tokens in localStorage/sessionStorage  
- **[SHOULD]** prefer BFF + httpOnly cookies

#### CSRF Note (When using cookies)
- **[MUST]** if auth is cookie-based, ensure CSRF protections on state-changing requests
  - SameSite + CSRF token / double-submit / framework CSRF middleware as appropriate.

#### Open Redirects
- **[MUST]** validate redirect targets against allowlist or use relative paths only

#### Source Maps / Data Exposure
- **[SHOULD]** do not ship source maps publicly unless explicitly intended and reviewed  
- **[SHOULD]** avoid storing PII in client state unnecessarily

</details>

---

### Infrastructure-as-Code

<details>
<summary><strong>Expand Infrastructure-as-Code</strong></summary>

- **[MUST]** no public buckets by default; enforce BlockPublicAccess  
- **[MUST]** encrypt at rest (RDS/S3/EBS/Dynamo/etc.)  
- **[MUST]** least-privilege IAM; avoid `Action:"*"` / `Resource:"*"`  
- **[SHOULD]** minimize `0.0.0.0/0` ingress; prefer private subnets for data stores  
- **[SHOULD]** audit logging (CloudTrail / equivalent) enabled

</details>

---

### Dependency & Supply Chain Security

<details>
<summary><strong>Expand Dependency & Supply Chain Security</strong></summary>

#### Dependency Reviews
- **[MUST]** verify the package/action actually exists (avoid hallucinated deps)  
- **[MUST]** check for known CVEs — **block CRITICAL and HIGH CVEs**; flag MEDIUM for review  
- **[MUST]** ensure lockfile changes match manifest intent  
- **[SHOULD]** prefer approved registries / internal proxies if available

#### Typosquatting Signals
- new package, low downloads, newly created publisher, similar name to popular lib  
- **[SHOULD]** prefer well-known maintained packages

#### GitHub Actions Supply Chain
- **[MUST]** pin actions to SHAs  
- **[SHOULD]** prefer official actions (`actions/*`, `github/*`) and an org allowlist

</details>

---

### AI/LLM Integration Code

<details>
<summary><strong>Expand AI/LLM Integration Code</strong></summary>

#### Prompt Injection Prevention
- **[MUST]** never place raw user input into privileged/system instructions  
- **[MUST]** use clear delimiters between instructions and user content  
- **[SHOULD]** validate/filter model outputs before using downstream

#### Data Leakage to Models
- **[MUST]** do not send customer data to external LLMs without explicit approval/DPAs  
- **[MUST]** mask PII/secrets/tenant data before LLM processing  
- **[SHOULD]** log categories of data sent (not raw sensitive content)

#### LLM Output Handling
- **[REDLINE]** executing model output as code or shell commands without human review — never pass LLM output directly to `eval()`, SQL queries, shell commands (`exec`, `subprocess`, `child_process`), or `innerHTML`/`dangerouslySetInnerHTML`. This leads to code injection, command injection, SQL injection, and cross-site scripting.  
- **[MUST]** sanitize model output before rendering in UI (XSS risk)  
- **[MUST]** do not use model output for authz/security decisions

</details>

---

### Atlan Technology Context

<details>
<summary><strong>Expand Atlan Technology Context</strong></summary>

> **Note:** This reflects common Atlan components and patterns; always validate against the current repo/service ownership and architecture.

| Component | Technology | Key Security Risks |
|-----------|-----------|-------------------|
| Workflow Engine | Temporal (migrating from Argo) | Workflow RCE patterns, over-privileged SAs |
| Identity | Keycloak (OAuth2/OIDC) | JWT validation gaps, refresh token exposure |
| API Gateway | Kong | CORS misconfig, admin API exposure, plugin ordering |
| Log Storage | ClickHouse | Cross-tenant log access, credential exposure in logs |
| AI/ML | Azure OpenAI | Prompt injection, data leakage, PII exposure |
| Secrets | AWS Secrets Manager, Vault, K8s Secrets | over-privileged access, rotation gaps |
| Architecture | Multi-tenant SaaS | tenant isolation failures at any layer |

#### Compliance Requirements
- SOC2 Type II: audit logging, access controls, change management  
- GDPR: data residency, deletion, 72-hour notification requirements  
- HIPAA: PHI protection for healthcare customers  

</details>

---

### Security Review Checklist

#### Universal (All Code Types)
- [ ] **No secrets** in code/config/logs/CI output  
- [ ] Client errors don't expose stack traces/SQL/paths/internal IPs  
- [ ] Audit logging for security-sensitive actions (auth, role changes, exports, admin ops)  
- [ ] Existing security patterns are used (don't invent new auth flows casually)

#### Data Access (Backend/APIs/Workers)
- [ ] `tenant_id` enforced from auth context in every query  
- [ ] Input validated; allowlists preferred  
- [ ] Parameterized queries only  
- [ ] Auth enforced; authz verifies ownership  
- [ ] Rate limiting on abuse-prone endpoints

#### Infrastructure (Helm/K8s/Docker/Terraform)
- [ ] Containers run as non-root; minimal capabilities  
- [ ] RBAC least privilege; avoid wildcards  
- [ ] Network exposure minimized; TLS enforced where needed  
- [ ] Secrets managed externally (not values.yaml)  
- [ ] Resource limits set; images pinned

#### CI/CD
- [ ] Actions pinned to SHAs  
- [ ] Minimum permissions  
- [ ] No PR-metadata injection in run scripts  
- [ ] Secrets not logged; avoid risky `pull_request_target` patterns

#### Dependencies
- [ ] No typosquatting signals  
- [ ] Lockfile consistent with changes  
- [ ] CRITICAL and HIGH CVEs blocked; MEDIUM CVEs flagged for review

#### Frontend
- [ ] No unsafe HTML rendering  
- [ ] Tokens not in localStorage  
- [ ] Redirects validated  
- [ ] CSRF considered if using cookies

---

### SCA Coverage Requirements

- **[MUST]** All repositories in approved GitHub organizations must be enrolled in **SCA scanning (Snyk)**. No exceptions for internal tools, side projects, or "non-critical" apps.
- **[MUST]** When onboarding or migrating a repository, verify Snyk integration is configured as part of the migration checklist — not as a follow-up.
- **[SHOULD]** Maintain a dashboard showing SCA coverage percentage across all repos. Target: **100% of repos with deployable code.**
- **[MUST]** For critical/zero-day CVEs, have a process to identify **ALL affected applications within 24 hours** — not just the ones already scanned.

**Agent behavior:** When creating new projects, adding new repositories, or migrating code, include SCA tool configuration (Snyk) in the setup checklist. Flag if missing.

---

### Internal Application Exposure

- **[MUST]** Internal applications must **not be exposed to the public internet** without explicit security review and approval. Default to **VPN-only access**.
- **[MUST]** All internet-facing subdomains must have **CloudFlare WAF enabled** with virtual patching rules active for known critical CVEs.
- **[SHOULD]** Maintain a complete inventory of all internet-facing applications with documented ownership, tech stack, and security controls.
- **[MUST]** Security recommendations must be **formally tracked** with assigned owner, due date, and verified closure — not just communicated verbally or via chat.

**Agent behavior:** If you see code deploying a new service, subdomain, or endpoint, flag whether it should be public or internal. If internal, verify VPN/network restriction is configured. If public, verify WAF and authentication are in place.

---

### Code Repository Governance

- **[MUST]** All production/internal application code must reside in **approved GitHub organizations** (e.g., AtlanHQ). Code in personal GitHub accounts lacks SCA scanning, secret detection, branch protection, and audit visibility.
- **[MUST]** Agents must **flag any reference to personal repos**, personal GitHub accounts, or code imports/dependencies from non-organizational sources.
- **[SHOULD]** New repositories must be created within the approved org with branch protection, required reviews, and SCA integration from day one.

**Agent behavior:** When you encounter imports, git submodules, or references to repositories outside the approved org (e.g., `github.com/<personal-username>/...`), flag it as a security concern and recommend migrating to the org.

---

### Appendix: References

- OWASP Top 10 — https://owasp.org/www-project-top-ten/  
- OWASP API Security Top 10 — https://owasp.org/www-project-api-security/  
- OWASP Prompt Injection — https://owasp.org/www-community/prompt-injection  
- STRIDE — https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats  
- Temporal Security — https://docs.temporal.io/security  
- Kubernetes Security — https://kubernetes.io/docs/concepts/security/  
- Keycloak Docs — https://www.keycloak.org/docs/latest/server_admin/  
- GitHub Actions Hardening — https://docs.github.com/en/actions/security-for-github-actions/security-hardening-for-github-actions  
- SLSA — https://slsa.dev/  
- OpenSSF Scorecard — https://securityscorecards.dev/

---

## Version History

- **v4.2 (2026-02-11):** Added SCA Coverage Requirements (Snyk enrollment for all repos); Internal Application Exposure rules (VPN-only default, CloudFlare WAF for public); Code Repository Governance (all code in approved GitHub orgs, flag personal repos); approved org invariant added to Security Invariants.
- **v4.1 (2026-02-11):** Added Owners & Contact section; SSRF invariant; rate limiting as [MUST] for new endpoints; new API endpoint security checklist (auth, authz, input validation, rate limiting required before merge); .env file rules with secret pattern recognition; Secret Discovery Protocol; Data Classification guidelines; CVE blocking policy (CRITICAL/HIGH blocked, not just flagged); clarified unsafe LLM output usage with specific injection examples; escalation guidance for manual security reviews.
- **v4.0 (2026-02-11):** Restructured — all security content moved under `## Security` section; added `## Project Overview`, `## Project Commands`, `## Coding Conventions`, and `## Architecture Notes` placeholder sections for team customization; both `AGENTS.md` and `CLAUDE.md` now follow the same structure; heading levels adjusted (sections → subsections under Security).
- **v3.1 (2026-02-06):** Added Agent Quickstart + Security Invariants; introduced [MUST]/[REDLINE]/[SHOULD]/[NICE] tags; moved deep sections into collapsible blocks; added SSRF guidance (backend), CSRF note (frontend cookie sessions), explicit `pull_request_target` redline (CI/CD), and "Logging Redlines"; clarified "Explain, don't block" exception for CRITICAL issues; minor routing improvements via Team Profiles.
- **v3.0 (2026-02-06):** Major restructure — Added Code Type Security Matrix, Vibe Coding section, Helm/K8s, CI/CD, Shell Scripts, Configuration Files, Frontend, IaC, Supply Chain, AI/LLM sections. Reorganized by code type for faster agent lookup. Added quick review format.
- **v2.1 (2024-12-31):** Made code examples language-agnostic, removed unimplemented technologies, added Template Security and Error Handling sections.
- **v2.0 (2024-12-19):** Added Atlan-specific context, STRIDE framework, multi-tenant isolation, Temporal security, real vulnerability examples.
- **v1.0 (2024-12-19):** Initial version with basic security guidelines.
