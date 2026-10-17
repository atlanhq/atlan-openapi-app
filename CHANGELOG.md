# Changelog

## v0.7.4 (October 17, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.7.3...v0.7.4

### Chores

- lock file maintenance (#767) (by @atlan-app-fleet[bot] in [8db0e1f](https://github.com/atlanhq/atlan-openapi-app/commit/8db0e1f))
- lock file maintenance (#769) (by @atlan-app-fleet[bot] in [f184046](https://github.com/atlanhq/atlan-openapi-app/commit/f184046))
- lock file maintenance (#770) (by @atlan-app-fleet[bot] in [7eca468](https://github.com/atlanhq/atlan-openapi-app/commit/7eca468))


## v0.7.3 (October 16, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.7.2...v0.7.3

### Chores

- lock file maintenance (#760) (by @atlan-app-fleet[bot] in [f56a903](https://github.com/atlanhq/atlan-openapi-app/commit/f56a903))
- update atlan framework dependencies (#759) (by @atlan-app-fleet[bot] in [127b8a7](https://github.com/atlanhq/atlan-openapi-app/commit/127b8a7))
- lock file maintenance (#762) (by @atlan-app-fleet[bot] in [7b8107e](https://github.com/atlanhq/atlan-openapi-app/commit/7b8107e))
- lock file maintenance (#764) (by @atlan-app-fleet[bot] in [de44e56](https://github.com/atlanhq/atlan-openapi-app/commit/de44e56))
- lock file maintenance (#765) (by @atlan-app-fleet[bot] in [0801267](https://github.com/atlanhq/atlan-openapi-app/commit/0801267))
- update atlan framework dependencies (#766) (by @atlan-app-fleet[bot] in [ae87edd](https://github.com/atlanhq/atlan-openapi-app/commit/ae87edd))


## v0.7.2 (October 15, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.7.1...v0.7.2

### Bug Fixes

- raise transients as retryable SOURCE_UNAVAILABLE leaves; F005 DEBUG log (conformance findings) (#741) (by @vaibhavatlan in [a10fe75](https://github.com/atlanhq/atlan-openapi-app/commit/a10fe75))

### Chores

- lock file maintenance (#738) (by @atlan-app-fleet[bot] in [924bb17](https://github.com/atlanhq/atlan-openapi-app/commit/924bb17))
- lock file maintenance (#740) (by @atlan-app-fleet[bot] in [d9ef85d](https://github.com/atlanhq/atlan-openapi-app/commit/d9ef85d))
- lock file maintenance (#742) (by @atlan-app-fleet[bot] in [9d309f3](https://github.com/atlanhq/atlan-openapi-app/commit/9d309f3))
- lock file maintenance (#743) (by @atlan-app-fleet[bot] in [fb007f7](https://github.com/atlanhq/atlan-openapi-app/commit/fb007f7))
- update dependency atlan-application-sdk to v3.46.0 (#744) (by @atlan-app-fleet[bot] in [1cabe1e](https://github.com/atlanhq/atlan-openapi-app/commit/1cabe1e))
- update dependency atlan-application-sdk-conformance to v0.47.0 (#745) (by @atlan-app-fleet[bot] in [8b7bb8c](https://github.com/atlanhq/atlan-openapi-app/commit/8b7bb8c))
- lock file maintenance (#748) (by @atlan-app-fleet[bot] in [cf9bbdf](https://github.com/atlanhq/atlan-openapi-app/commit/cf9bbdf))
- update atlan framework dependencies (#749) (by @atlan-app-fleet[bot] in [4a85b8f](https://github.com/atlanhq/atlan-openapi-app/commit/4a85b8f))
- lock file maintenance (#750) (by @atlan-app-fleet[bot] in [93bf9d1](https://github.com/atlanhq/atlan-openapi-app/commit/93bf9d1))
- lock file maintenance (#752) (by @atlan-app-fleet[bot] in [c11dcae](https://github.com/atlanhq/atlan-openapi-app/commit/c11dcae))
- update atlan framework dependencies (#753) (by @atlan-app-fleet[bot] in [064b41b](https://github.com/atlanhq/atlan-openapi-app/commit/064b41b))
- update atlan framework dependencies (#755) (by @atlan-app-fleet[bot] in [93c00d0](https://github.com/atlanhq/atlan-openapi-app/commit/93c00d0))
- lock file maintenance (#756) (by @atlan-app-fleet[bot] in [3ea7a1b](https://github.com/atlanhq/atlan-openapi-app/commit/3ea7a1b))
- lock file maintenance (#758) (by @atlan-app-fleet[bot] in [6413524](https://github.com/atlanhq/atlan-openapi-app/commit/6413524))


## v0.7.1 (October 08, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.7.0...v0.7.1

### Features

- update output_file comment for entity_bytes serialization (#611) (by @cmgrote in [f62e154](https://github.com/atlanhq/atlan-openapi-app/commit/f62e154))

### Bug Fixes

- close spec-URL redaction gap; clear all conformance suppressions (#574) (by @cmgrote in [023828c](https://github.com/atlanhq/atlan-openapi-app/commit/023828c))
- serialize through SDK entity_bytes, not to_nested_bytes (FND-2724) (#607) (by @cmgrote in [3df6c75](https://github.com/atlanhq/atlan-openapi-app/commit/3df6c75))
- ignore ruff G201, which contradicts conformance L017 (#704) (by @vaibhavatlan in [4a5cd4c](https://github.com/atlanhq/atlan-openapi-app/commit/4a5cd4c))
- probe the object-store credential on the CLOUD path (#384) (by @fyzanshaik-atlan in [6cd8934](https://github.com/atlanhq/atlan-openapi-app/commit/6cd8934))


## v0.7.0 (September 10, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.6.2...v0.7.0

### Features

- onboard ai-connector-review + local review kit (#401) (by @chetanchoudhary-atlan in [3939f04](https://github.com/atlanhq/atlan-openapi-app/commit/3939f04))

### Bug Fixes

- report Release Gate on merge-queue branches (FND-851) (#394) (by @cmgrote in [2c97880](https://github.com/atlanhq/atlan-openapi-app/commit/2c97880))
- resync bootstrap scaffolding and clear all conformance findings (#449) (by @cmgrote in [555e7e2](https://github.com/atlanhq/atlan-openapi-app/commit/555e7e2))
- key the hidden extraction-method field like the rest of the fleet (#458) (by @vaibhavatlan in [4eb217a](https://github.com/atlanhq/atlan-openapi-app/commit/4eb217a))
- add suggested_action to unguided openapi preflight checks (#492) (by @fyzanshaik-atlan in [29a0c2f](https://github.com/atlanhq/atlan-openapi-app/commit/29a0c2f))


## v0.6.2 (August 20, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.6.1...v0.6.2

### Features

- correct cloud_source GUID backward-compatibility claims (#353) (by @fyzanshaik-atlan in [cae3d22](https://github.com/atlanhq/atlan-openapi-app/commit/cae3d22))

### Bug Fixes

- unpin the yanked pytest-timeout 2.5.0 (FND-379) (#378) (by @cmgrote in [46154e0](https://github.com/atlanhq/atlan-openapi-app/commit/46154e0))
- allowlist emulator fixtures so the credential-leak gate can pass (#381) (by @sachi-atlan in [be27335](https://github.com/atlanhq/atlan-openapi-app/commit/be27335))


## v0.6.1 (August 19, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.6.0...v0.6.1

### Bug Fixes

- route cloud-spec credential through agent-aware resolver (P037) (#271) (by @cmgrote in [073f2c3](https://github.com/atlanhq/atlan-openapi-app/commit/073f2c3))
- derive per-test timeout from each suite's declared poll budgets (#284) (by @fyzanshaik-atlan in [78693e0](https://github.com/atlanhq/atlan-openapi-app/commit/78693e0))
- emit deploy.execution_mode so canary stops falling back to argo (#324) (by @vaibhavatlan in [a95f744](https://github.com/atlanhq/atlan-openapi-app/commit/a95f744))
- close CONNECT-812 pattern classes found in this app (#368) (by @praveenkmr in [d3eb389](https://github.com/atlanhq/atlan-openapi-app/commit/d3eb389))


## v0.6.0 (July 21, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.5.3...v0.6.0

### Features

- generate spec-source credential from app.pkl (retire csa-connectors-objectstore.pkl) (#261) (by @vaibhavatlan in [782e292](https://github.com/atlanhq/atlan-openapi-app/commit/782e292))


## v0.5.3 (July 20, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.5.2...v0.5.3


## v0.5.2 (July 17, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.5.1...v0.5.2


## v0.5.1 (July 16, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.5.0...v0.5.1

### Bug Fixes

- restore app_id in contract and add poe generate task (#241) (by @cmgrote in [3d1bc2e](https://github.com/atlanhq/atlan-openapi-app/commit/3d1bc2e))


## v0.5.0 (July 16, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.4.0...v0.5.0

### Features

- migrate tests.yaml to tests-reusable + services-script hook (#134) (by @cmgrote in [ddc79ef](https://github.com/atlanhq/atlan-openapi-app/commit/ddc79ef))
- assertion-only publish on connection_usage=REUSE (CONNECT-55) (#219) (by @cmgrote in [9ad1117](https://github.com/atlanhq/atlan-openapi-app/commit/9ad1117))

### Bug Fixes

- resolve E018 conformance warnings with domain-specific error subclasses (#133) (by @cmgrote in [c80dad5](https://github.com/atlanhq/atlan-openapi-app/commit/c80dad5))
- regenerate manifest for app-contract-toolkit 0.14.2 (#170) (by @vaibhavatlan in [133e4e7](https://github.com/atlanhq/atlan-openapi-app/commit/133e4e7))
- remediate conformance findings across CI, contracts, and asset mapping (#203) (by @cmgrote in [d170c85](https://github.com/atlanhq/atlan-openapi-app/commit/d170c85))
- inherit per-leg ATLAN_DEPLOYMENT_NAME (overlay + agent_spec) (#226) (by @cmgrote in [46b880e](https://github.com/atlanhq/atlan-openapi-app/commit/46b880e))


## v0.4.0 (June 03, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.3.0...v0.4.0

### Features

- opt in to versioned release model (by @cmgrote in [4eda21c](https://github.com/atlanhq/atlan-openapi-app/commit/4eda21c))
- wire unit-tests gate (DISTR-456) (#89) (by @anurag-atlan in [7b0b118](https://github.com/atlanhq/atlan-openapi-app/commit/7b0b118))
- add full-DAG e2e test tier (#96) (by @cmgrote in [d3d9e3e](https://github.com/atlanhq/atlan-openapi-app/commit/d3d9e3e))

### Bug Fixes

- wait for Temporal default namespace instead of fixed sleep 5 (by @cmgrote in [d25cc6d](https://github.com/atlanhq/atlan-openapi-app/commit/d25cc6d))
- restore release_model: semver via pkl metadata field (#102) (by @cmgrote in [44f8d43](https://github.com/atlanhq/atlan-openapi-app/commit/44f8d43))
- bundle Petstore spec to avoid CI rate-limiting (#105) (by @cmgrote in [fa8c1d5](https://github.com/atlanhq/atlan-openapi-app/commit/fa8c1d5))
- restore APIPath assets in OpenAPI spec loader (BLDX-1363) (#104) (by @mothership-ai[bot] in [2f58854](https://github.com/atlanhq/atlan-openapi-app/commit/2f58854))


## v0.3.0 (May 08, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.2.1...v0.3.0

### Features

- adopt SDK e2e harness from application-sdk#1669 (by @cmgrote in [9759ac2](https://github.com/atlanhq/atlan-openapi-app/commit/9759ac2))

### Bug Fixes

- strip https:// prefix from SDR_TEST_TENANT before envsubst (by @cmgrote in [9c31527](https://github.com/atlanhq/atlan-openapi-app/commit/9c31527))
- construct auth URL from tenant domain; strip protocol once globally (by @cmgrote in [6edab76](https://github.com/atlanhq/atlan-openapi-app/commit/6edab76))
- force IPv4 in container; fix volume mount absolute path (by @cmgrote in [042dff4](https://github.com/atlanhq/atlan-openapi-app/commit/042dff4))
- pin Temporal hostname to IPv4 via extra_hosts (by @cmgrote in [19910c4](https://github.com/atlanhq/atlan-openapi-app/commit/19910c4))
- read Temporal host from configurator output, not derived pattern (by @cmgrote in [86da797](https://github.com/atlanhq/atlan-openapi-app/commit/86da797))
- override ATLAN_TEMPORAL_HOST to actual tenant domain (by @cmgrote in [8b49e0c](https://github.com/atlanhq/atlan-openapi-app/commit/8b49e0c))
- force IPv4 for Temporal gRPC via extra_hosts (by @cmgrote in [5b7298c](https://github.com/atlanhq/atlan-openapi-app/commit/5b7298c))
- remove ATLAN_TEMPORAL_HOST override; let configurator set it (by @cmgrote in [5138eac](https://github.com/atlanhq/atlan-openapi-app/commit/5138eac))


## v0.2.1 (April 29, 2026)

Full Changelog: https://github.com/atlanhq/atlan-openapi-app/compare/v0.2.0...v0.2.1


## v0.2.0

- Initial baseline aligned with v0.2.0 git tag.
