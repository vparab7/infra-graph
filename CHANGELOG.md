# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-04-27

### Changed

- **Docs:** Replaced all example node IDs in documentation and tests with generic names; fuzzy-match prefix examples now use common patterns (`org-`, `team-`, `prod-`, `staging-`) with a comment instructing users to extend the list for their own org.

## [0.3.0] - 2026-04-27

### Added

- **TOON output format (default):** `graph.toon` replaces `graph.json` as the default build output. TOON (Token-Oriented Object Notation) uses tabular encoding for uniform arrays, reducing graph size by ~40% compared to JSON. Use `infra-graph build . --format json` to opt in to the legacy JSON format. `load_graph` falls back to `.json` automatically if `.toon` is not found, preserving backward compatibility.
- **Graph federation (`infra-graph federate`):** Merges graphs from multiple repositories into a single cross-repo `federated-graph.toon`. Three resolution strategies are applied in order:
  - *Exact ID match* — an unknown node in repo A is resolved by a real node in repo B sharing the same node ID.
  - *Fuzzy/suffix match* — strips known org prefixes and matches on base name + type (e.g. `helm_chart/myapp` resolved to `helm_chart/org-myapp`); resolved edges are tagged `provenance=FEDERATED_FUZZY, confidence=0.7`.
  - *Attribute/value match* — ArgoCD cluster Secrets (`server_url`) are matched to Terraform `azurerm_kubernetes_cluster` resources and linked via `provisioned_by` edges (`provenance=FEDERATED_INFERRED, confidence=0.6`).
  - Federation output includes `meta` fields: `unknowns_resolved` and `provisioned_by_edges`.
- **ArgoCD cluster Secret `server_url` extraction:** When a Kubernetes Secret carries the label `argocd.argoproj.io/secret-type: cluster`, the parser now extracts `server_url` (resolved from `stringData.server` → `spec.server` → base64-decoded `data.server`) and `argocd_cluster_name` as node attributes. These attributes drive the federation attribute-match strategy.
- **MCP server `--graph` flag:** `infra-graph serve --graph /path/to/any-graph.toon` loads any graph file (local single-repo or federated) directly. The server resolves `.toon` or `.json` format from the file extension.
- **Dual-graph MCP install:** `infra-graph install --federated /path/to/federated-graph.toon` writes a second MCP server entry (`infra-graph-federated`) alongside the standard `infra-graph` entry. Claude Code discovers both servers and can query either scope.
- **Terraform output `expression` attribute:** `output` nodes now store the raw HCL `value` expression string as an `expression` attribute, allowing federation to trace cluster FQDN references across repositories.

## [0.2.0] - 2026-04-25

### Added

- **Universal K8s CRD support:** Removed the 29-kind allowlist gate in `k8s_schema.py`. Any YAML document with `apiVersion + kind + metadata` now produces a node — unknown CRDs (Velero, Crossplane, custom operators, etc.) are no longer silently dropped.
- **Istio:** `VirtualService` → `Service` (`routes_to`), `DestinationRule` → `Service` (`configures`) edge extraction.
- **Flux CD:** `HelmRelease` → `HelmRepository`/`GitRepository` (`from_repo`), `HelmRelease` → helm chart (`uses_chart`), `Kustomization` → `GitRepository` (`from_repo`), `Alert` → `Provider` (`uses_provider`) edge extraction.
- **Argo Rollouts:** `Rollout` → `Service` (`routes_to` for canary/stable services), `Rollout` → `AnalysisTemplate` (`uses_analysis`) edge extraction.
- **KEDA:** `ScaledObject` → `Deployment`/`StatefulSet` (`scales`) edge extraction, mirroring the existing HPA pattern.
- **Gateway API:** `HTTPRoute` → `Gateway` (`attached_to`), `HTTPRoute` → `Service` (`routes_to`) edge extraction.
- **Ansible parser** (`ansible_schema.py`): Detects and parses playbooks (play nodes, `uses_role` edges, `includes_tasks` edges) and task files.
- **Generic YAML fallback:** Any `.yml`/`.yaml` file that doesn't match any known schema now produces a `config/<stem>` node instead of being silently skipped.
- **27 new tests** in `tests/test_extensions.py` covering all new parsers and the unknown-CRD fallback.

## [0.1.2] - 2026-04-22

### Changed

- **License:** Switched from MIT to Apache 2.0.
- **Security:** Added CodeQL scanning workflow and Dependabot auto-update config.
- **Docs:** Clarified PyPI package name (`infra-graph7`) vs CLI command (`infra-graph`); fixed all repo URLs to `vparab7/infra-graph`.

## [0.1.1] - 2026-04-22

### Fixed

- **Parse-order bug for `selects_clusters` edges:** ApplicationSet cluster generator selectors are now resolved in a post-parse sweep (`resolve_cluster_selectors()`), the same way K8s Service label selectors work. Previously, zero `selects_clusters` edges were created if the ApplicationSet file was parsed before the cluster Secret file. Now 12 edges are correctly created on the benchmark repo.
- **ArgoCD multi-source Applications:** Both `Application` and `ApplicationSet` now extract `spec.sources` (list, ArgoCD 2.6+) in addition to `spec.source`. Helm chart sources create `uses_chart` edges.
- **Helm `__helm__` placeholder leaking into node IDs:** All field extractions in ArgoCD edge methods now route through `_safe_str()`, preventing phantom nodes named `AppProject/argocd/__helm__`.
- **Non-deterministic parse order:** Files are now sorted before parsing, making builds reproducible across runs.
- **Line numbers:** Nodes now include a 1-indexed `line` attribute extracted from ruamel.yaml's `.lc` metadata, enabling `get_resource_context` to report exact source locations.
- **`spec.destination.server` context:** Applications that use a server URL instead of a cluster name now store `dest_server` as a node attribute for AI context, without creating phantom edges.

### Added

- 8 new tests covering ArgoCD schemas (AppProject, Application, ApplicationSet, ExternalSecret, Certificate), Helm template stripping, `selects_clusters` post-parse resolution, and line number extraction.

## [0.1.0] - 2026-04-22

### Added

- Terraform / HCL parser: resources, modules, variables, outputs, locals, data sources, providers with 7 edge types (`references`, `depends_on`, `uses_var`, `uses_data`, `passes_input`, `uses_local`, `dynamic_ref`).
- Kubernetes manifest parser: 11 node types (Deployment, Service, ConfigMap, Secret, Ingress, Namespace, StatefulSet, DaemonSet, HPA, PVC, ServiceAccount) with cross-file label-selector sweep for `selects`/`routes_to` edges.
- GitHub Actions parser: jobs, steps, `needs:` dependencies, `uses:` action references, secret usage.
- Docker Compose parser: services, volumes, networks, `depends_on`, `shares_volume`, `shares_network`.
- Helm/Kustomize parser: chart metadata, `values*.yaml` overrides, `kustomization.yaml` bases/overlays/patches.
- SHA-256 file cache for incremental rebuilds (`--update`).
- `.infraignore` support (`.gitignore` syntax via `pathspec`).
- `--watch` mode for auto-rebuild on file saves.
- Community detection with Leiden algorithm (graspologic) and greedy modularity fallback.
- `GRAPH_REPORT.md` with god nodes, community map, surprising cross-community edges, and token benchmark.
- Interactive vis.js HTML visualization with type filter, community coloring, and click-to-inspect panel.
- 10 MCP tools: `get_minimal_context`, `get_blast_radius`, `query_graph`, `get_resource_context`, `get_architecture_overview`, `detect_changes`, `find_hub_nodes`, `get_knowledge_gaps`, `build_or_update_graph`, `search_resources`.
- `infra-graph install` for Claude Code, Cursor, Codex, and OpenCode.
- `/infra-graph` Claude Code skill.

[Unreleased]: https://github.com/vparab7/infra-graph/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/vparab7/infra-graph/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/vparab7/infra-graph/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/vparab7/infra-graph/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/vparab7/infra-graph/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/vparab7/infra-graph/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/vparab7/infra-graph/releases/tag/v0.1.0
