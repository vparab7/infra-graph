# infra-graph

**Stop asking your AI to read 70 files. Give it a graph.**

infra-graph is a knowledge graph engine for infrastructure files. It parses your Terraform, Kubernetes, ArgoCD, GitHub Actions, Docker Compose, Helm, and Kustomize files, builds a structural dependency graph, and exposes it as an MCP server — so your AI assistant reads compact graph context instead of raw files on every question.

[![PyPI](https://img.shields.io/pypi/v/infra-graph7?style=flat-square&color=blue)](https://pypi.org/project/infra-graph7/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=flat-square)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square)](https://www.python.org/)
[![MCP compatible](https://img.shields.io/badge/MCP-compatible-green.svg?style=flat-square)](https://modelcontextprotocol.io/)
[![CI](https://github.com/vparab7/infra-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/vparab7/infra-graph/actions/workflows/ci.yml)

---

## Why infra-graph?

Every time you ask your AI assistant an infrastructure question, it reads your entire repo from scratch.

- "What does this EC2 instance depend on?" → AI reads all 80 `.tf` files
- "Which ConfigMap does this Deployment use?" → AI reads every manifest
- "What breaks if I change this ArgoCD AppProject?" → AI scans everything again

The cross-file relationships that matter — a Security Group referenced by 12 resources, a ConfigMap mounted by 5 Deployments, an ArgoCD ApplicationSet deploying 9 services to 3 clusters — are invisible without a graph.

**infra-graph pre-indexes those relationships once. Every subsequent question reads the compact graph.**

| Approach | Tokens per query |
|----------|-----------------|
| AI reads all files (naive) | **~29,600–71,000** |
| `get_minimal_context` | **~300** |
| `get_blast_radius` (targeted) | **~500–800** |
| Full graph (worst case) | **~1,100** |

**Up to 65× token reduction on targeted queries.**

---

## Quick Start

> **Prerequisites:** Python 3.10+ · pip · An AI assistant with MCP support (Claude Code, Cursor, Codex, or OpenCode)

**Step 1 — Install**

```bash
pip install infra-graph7
```

> The PyPI package is `infra-graph7`. Once installed, the CLI command is `infra-graph` (no `7`).

**Step 2 — Go to your infrastructure repo**

```bash
cd /path/to/your/infra-repo
```

**Step 3 — Wire it into your AI assistant**

```bash
infra-graph install
```

This auto-detects your AI assistant (Claude Code, Cursor, Codex, OpenCode) and writes the MCP config. Done — restart your AI assistant and it will use infra-graph automatically.

**Step 4 — Build the graph**

```bash
infra-graph build .
```

You'll see a `GRAPH_REPORT.md` appear in the current directory with a summary of your infrastructure: god nodes, communities, surprising connections, and token savings.

**Step 5 — Ask questions**

Open your AI assistant and ask:

```
What is the blast radius if I delete the production VPC?
Which Deployments use the app-config ConfigMap?
Show me the full architecture overview.
What secrets does the external-secrets operator manage?
Which services depend on this database?
```

The AI now reads compact graph context (~500 tokens) instead of all your files (~30,000 tokens).

---

## Installation Details

### Claude Code

```bash
infra-graph install --platform claude-code
```

This writes:
- `.mcp.json` — MCP server config (Claude Code picks this up automatically on next launch)
- `CLAUDE.md` — instructs Claude to use infra-graph tools before reading files

Then restart Claude Code. You'll see infra-graph listed under available MCP servers.

You can also use the `/infra-graph` slash command:

```
/infra-graph .           # build + get orientation summary
/infra-graph . --update  # incremental update after file changes
```

### Cursor

```bash
infra-graph install --platform cursor
```

Writes `.cursor/rules/infra-graph.mdc`. Restart Cursor to pick it up.

### Codex

```bash
infra-graph install --platform codex
```

Writes `AGENTS.md` with tool usage instructions.

### OpenCode

```bash
infra-graph install --platform opencode
```

### Manual / other assistants

```bash
infra-graph serve   # starts the MCP stdio server
```

Point your assistant's MCP config at this command. The server speaks the standard MCP stdio protocol.

---

## Building the graph

```bash
infra-graph build .                   # parse everything in current directory
infra-graph build ./terraform         # only Terraform files
infra-graph build ./k8s               # only Kubernetes manifests
infra-graph build . --update          # re-parse only files that changed (fast)
infra-graph build . --watch           # auto-rebuild on every file save
infra-graph build . --mode deep       # add optional LLM semantic annotations
```

After each build, `GRAPH_REPORT.md` is written with:
- **God nodes** — highest-degree resources everything connects through
- **Communities** — automatically detected resource clusters (networking, compute, secrets, CI/CD)
- **Surprising edges** — cross-community connections worth reviewing
- **Token savings** — naive token cost vs. graph query cost for your repo

### Ignoring files

Create `.infraignore` in your repo root (same syntax as `.gitignore`):

```
.terraform/
*.tfstate
*.tfstate.backup
dist/
node_modules/
```

---

## What gets parsed

| Format | Extensions | What gets extracted |
|--------|-----------|---------------------|
| **Terraform / HCL** | `.tf` `.hcl` | Resources, modules, variables, outputs, locals, data sources, providers, `${}` interpolations, `depends_on` |
| **Kubernetes** | `.yaml` `.yml` with `apiVersion` | Deployments, Services, ConfigMaps, Secrets, Ingresses, StatefulSets, DaemonSets, HPAs, PVCs, ServiceAccounts + label→selector edges |
| **ArgoCD** | `.yaml` with `argoproj.io` | AppProjects, Applications, ApplicationSets, cluster generators, `member_of` + `deploys_to` edges |
| **cert-manager** | `.yaml` with `cert-manager.io` | ClusterIssuers, Issuers, Certificates + `uses_issuer`, `creates_secret` edges |
| **External Secrets** | `.yaml` with `external-secrets.io` | ExternalSecrets, ClusterSecretStores + `uses_store` edges |
| **Istio** | `.yaml` with `networking.istio.io` | VirtualServices → Service (`routes_to`), DestinationRules → Service (`configures`) |
| **Flux CD** | `.yaml` with `*.fluxcd.io` | HelmRelease → HelmRepository/GitRepository (`from_repo`), Kustomization → GitRepository, Alert → Provider |
| **Argo Rollouts** | `.yaml` with `argoproj.io/v1alpha1 Rollout` | Rollout → Service (`routes_to` canary/stable), Rollout → AnalysisTemplate (`uses_analysis`) |
| **KEDA** | `.yaml` with `keda.sh` | ScaledObject → Deployment/StatefulSet (`scales`) |
| **Gateway API** | `.yaml` with `gateway.networking.k8s.io` | HTTPRoute → Gateway (`attached_to`), HTTPRoute → Service (`routes_to`) |
| **Unknown CRDs** | any `.yaml` with `apiVersion + kind + metadata` | Node created with the custom `kind`; no edges (works with Velero, Crossplane, custom operators, etc.) |
| **Ansible** | `.yaml` playbooks and task files | Play nodes, role nodes, task file nodes + `uses_role`, `includes_tasks` edges |
| **GitHub Actions** | `.yml` in `.github/workflows/` | Jobs, steps, `uses:` action refs, `needs:` deps, secret usage |
| **Docker Compose** | `docker-compose.yml` / `compose.yaml` | Services, volumes, networks, `depends_on` |
| **Helm** | `Chart.yaml` + `values*.yaml` | Chart metadata, value file override edges |
| **Helm templates** | `templates/*.yaml` | Go `{{}}` directives auto-stripped; static structure extracted cleanly |
| **Kustomize** | `kustomization.yaml` | Base/overlay `extends` and `patches` edges |
| **Generic YAML** | any other `.yml` / `.yaml` | Produces a `config/<filename>` node — nothing is silently dropped |

---

## MCP Tools

Once installed, your AI assistant calls these tools automatically. You can also ask it to call them explicitly.

| Tool | What it does |
|------|-------------|
| `get_minimal_context` | ~300-token orientation: god nodes, community count, totals. **Start here.** |
| `get_blast_radius` | Every resource affected by a change, with depth and edge type |
| `query_graph` | BFS/DFS traversal from any node in any direction |
| `get_resource_context` | Full detail on one resource: all edges, community, file, line number |
| `get_architecture_overview` | Community map with dominant types and coupling warnings |
| `detect_changes` | Risk-scored impact analysis for a git diff |
| `find_hub_nodes` | Top N highest-degree (most connected) resources |
| `get_knowledge_gaps` | Orphaned resources, ambiguous edges, unresolved references |
| `build_or_update_graph` | Trigger a rebuild or incremental update from within the AI |
| `search_resources` | Keyword search across node IDs, names, types, and labels |

### Node ID format

Use these IDs when calling tools directly:

| Resource type | Node ID format | Example |
|--------------|----------------|---------|
| Terraform resource | `resource.<type>.<name>` | `resource.aws_vpc.main` |
| Terraform variable | `variable.<name>` | `variable.region` |
| Terraform module | `module.<name>` | `module.vpc` |
| Kubernetes workload | `<Kind>/<namespace>/<name>` | `Deployment/default/api` |
| ArgoCD AppProject | `AppProject/<namespace>/<name>` | `AppProject/argocd/my-project` |
| ArgoCD Application | `Application/<namespace>/<name>` | `Application/argocd/frontend` |
| Compose service | `service/<project>/<name>` | `service/myapp/postgres` |
| GitHub Actions job | `job/<workflow>/<job_key>` | `job/ci/build` |

---

## CLI Reference

```bash
# Build the graph
infra-graph build .                     # full build
infra-graph build . --update            # incremental (only changed files)
infra-graph build . --mode deep         # with optional LLM annotation
infra-graph build . --watch             # auto-rebuild on file saves

# Query from the terminal
infra-graph query "what does aws_instance.web depend on?"
infra-graph blast-radius resource.aws_vpc.main
infra-graph path "Deployment/default/api" "ConfigMap/default/app-config"

# Inspect
infra-graph status                      # node / edge / community counts
infra-graph visualize                   # open interactive vis.js graph in browser

# Server
infra-graph serve                       # start MCP stdio server manually

# Install
infra-graph install                     # auto-detect AI assistant
infra-graph install --platform claude-code
infra-graph install --platform cursor
infra-graph install --platform codex
infra-graph install --platform opencode
```

---

## Benchmarks

| Corpus | Files | Naive tokens/query | Graph tokens/query | Reduction |
|--------|-------|--------------------|-------------------|-----------|
| AWS three-tier Terraform | 38 `.tf` | ~31,000 | ~520 | **~60×** |
| Kubernetes GitOps repo | 120 manifests | ~48,000 | ~980 | **~49×** |
| Mixed monorepo (TF + k8s + Actions) | 160 | ~71,000 | ~1,100 | **~65×** |
| ArgoCD GitOps repo | 70 YAML | ~29,600 | ~650 | **~46×** |
| Small single-service Compose | 4 files | ~1,200 | ~950 | ~1.3× |

> **Small repo note:** For repos under ~20 files, graph overhead can exceed raw file size. infra-graph pays off at scale — when questions span multiple files and change frequently.

*Reproduce with `infra-graph eval --all`.*

---

## How it works

**Pass 1 — Structural parse (no LLM)**
Terraform files are parsed with `python-hcl2`. YAML files are parsed with `ruamel.yaml`. Helm templates have Go `{{}}` directives stripped before parsing. Every resource, module, variable, workload, ArgoCD app, cert, and workflow job becomes a typed node. Every interpolation, dependency, and selector reference becomes a typed edge.

**Pass 2 — Schema-aware inference (no LLM)**
Kubernetes label-selector matching runs as a cross-file sweep: a label inverted index is built, then Service selectors are matched against Deployment labels to create `routes_to` edges. ArgoCD cluster generator `matchLabels` are matched against cluster Secrets. Helm and Kustomize overlay relationships are detected as `extends`/`patches` edges.

**Pass 3 — Optional LLM annotation (`--mode deep`)**
Claude annotates communities with human-readable names, extracts design rationale from comments, and enriches report summaries. Not required for token savings — the structural graph alone delivers the reduction numbers above.

---

## Architecture

```
infra_graph/
├── parsers/
│   ├── tf_parser.py          # python-hcl2 + ${} interpolation extractor
│   ├── yaml_parser.py        # ruamel.yaml + Helm template pre-processor + generic fallback
│   ├── k8s_schema.py         # K8s + ArgoCD + Istio + Flux + KEDA + Gateway API + any CRD
│   ├── ansible_schema.py     # Ansible playbook + task file parser
│   ├── actions_schema.py     # GitHub Actions job/step/uses graph
│   ├── compose_schema.py     # Docker Compose service graph
│   └── helm_schema.py        # Helm Chart.yaml + Kustomize overlay detection
├── graph/
│   ├── builder.py            # NetworkX DiGraph + SHA-256 file cache
│   ├── blast_radius.py       # BFS impact traversal
│   ├── community.py          # Leiden clustering (graspologic) + fallback
│   └── report.py             # GRAPH_REPORT.md generator
├── mcp/
│   ├── server.py             # MCP stdio server
│   └── tools.py              # 10 MCP tool implementations
├── install/
│   ├── claude.py             # .mcp.json + CLAUDE.md writer
│   ├── cursor.py             # .cursor/rules/infra-graph.mdc
│   └── codex.py              # AGENTS.md writer
├── viz/
│   └── html_report.py        # vis.js interactive HTML graph
└── cli.py                    # click CLI
```

**Privacy:** All parsing happens locally. No file contents leave your machine except during the optional `--mode deep` LLM pass, which uses your own API key. No telemetry. No cloud.

---

## Contributing

```bash
git clone https://github.com/vparab7/infra-graph
cd infra-graph
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on adding new parsers and schemas.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

*Built by [Vedang Parab](mailto:parabvedang007@gmail.com)*
