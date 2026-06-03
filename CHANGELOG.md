# Changelog

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
