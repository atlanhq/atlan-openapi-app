# Changelog

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
