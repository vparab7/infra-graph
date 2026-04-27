"""
Install infra-graph for Claude Code:
  - Write .mcp.json
  - Append section to CLAUDE.md
"""

from __future__ import annotations

import json
from pathlib import Path

_MCP_JSON = {
    "mcpServers": {
        "infra-graph": {
            "command": "infra-graph",
            "args": ["serve"],
        }
    }
}

_CLAUDE_MD_SECTION = """
## MCP Tools: infra-graph

**IMPORTANT: This project has an infrastructure knowledge graph. ALWAYS use the
infra-graph MCP tools BEFORE using Grep/Glob/Read to explore infrastructure files.**
The graph is faster, cheaper (fewer tokens), and gives you structural context
(blast radius, dependencies, community clusters) that file scanning cannot.

### When to use graph tools FIRST

- **Exploring infra**: `search_resources` or `get_architecture_overview` instead of Grep
- **Understanding impact**: `get_blast_radius` instead of manually tracing dependencies
- **Code review**: `detect_changes` + `get_resource_context` instead of reading files
- **Finding relationships**: `query_graph` with direction upstream/downstream
- **Architecture questions**: `get_architecture_overview` + `find_hub_nodes`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `get_minimal_context` | Start here — quick orientation (~100 tokens) |
| `get_blast_radius` | Understanding impact of changing a resource |
| `query_graph` | Tracing dependencies from any node |
| `get_resource_context` | Deep-dive on one resource |
| `get_architecture_overview` | Community map with coupling warnings |
| `detect_changes` | Risk analysis from a git diff |
| `find_hub_nodes` | Finding the most critical resources |
| `get_knowledge_gaps` | Orphaned resources and ambiguous refs |
| `build_or_update_graph` | Rebuild graph after changes |
| `search_resources` | Find resources by keyword |
"""


def install(project_root: Path, federated_graph: Path | None = None) -> dict[str, str]:
    """
    Write .mcp.json and update CLAUDE.md in the given project root.

    Args:
        project_root: The project directory to install into.
        federated_graph: Optional path to a federated graph file.  When
            provided, a second ``"infra-graph-federated"`` entry is written
            to ``.mcp.json`` pointing at the given file.

    Returns a dict of {filename: action} for reporting.
    """
    project_root = project_root.resolve()
    results: dict[str, str] = {}

    # ── .mcp.json ──────────────────────────────────────────────────────────
    mcp_json_path = project_root / ".mcp.json"
    if mcp_json_path.exists():
        try:
            existing = json.loads(mcp_json_path.read_text())
        except Exception:
            existing = {}
        servers = existing.setdefault("mcpServers", {})
        servers["infra-graph"] = _MCP_JSON["mcpServers"]["infra-graph"]
        if federated_graph is not None:
            servers["infra-graph-federated"] = {
                "command": "infra-graph",
                "args": ["serve", "--graph", str(federated_graph.resolve())],
            }
        mcp_json_path.write_text(json.dumps(existing, indent=2) + "\n")
        results[".mcp.json"] = "updated"
    else:
        config = dict(_MCP_JSON)
        if federated_graph is not None:
            config["mcpServers"]["infra-graph-federated"] = {
                "command": "infra-graph",
                "args": ["serve", "--graph", str(federated_graph.resolve())],
            }
        mcp_json_path.write_text(json.dumps(config, indent=2) + "\n")
        results[".mcp.json"] = "created"

    # ── CLAUDE.md ──────────────────────────────────────────────────────────
    claude_md_path = project_root / "CLAUDE.md"
    marker = "## MCP Tools: infra-graph"

    if claude_md_path.exists():
        existing_content = claude_md_path.read_text()
        if marker in existing_content:
            results["CLAUDE.md"] = "already configured (skipped)"
        else:
            with claude_md_path.open("a") as f:
                f.write(_CLAUDE_MD_SECTION)
            results["CLAUDE.md"] = "updated"
    else:
        claude_md_path.write_text(
            "# Project Instructions\n" + _CLAUDE_MD_SECTION
        )
        results["CLAUDE.md"] = "created"

    return results
