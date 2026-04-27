"""
MCP stdio server for infra-graph.

Exposes 10 tools:
  get_minimal_context, get_blast_radius, query_graph,
  get_resource_context, get_architecture_overview, detect_changes,
  find_hub_nodes, get_knowledge_gaps, build_or_update_graph, search_resources
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import networkx as nx

try:
    from mcp import types as mcp_types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

from . import tools as T

_OUT_DIR = "infra-graph-out"
_GRAPH_FILE = "graph.toon"
_GRAPH_FILE_JSON = "graph.json"


def _load_graph(project_root: Path, graph_file: Path | None = None) -> nx.DiGraph:
    """
    Load the persisted graph.

    Search order:
      1. ``graph_file`` if explicitly provided
      2. ``project_root/infra-graph-out/graph.toon``
      3. ``project_root/infra-graph-out/graph.json``
    """
    import warnings

    from ..graph import toon

    candidates: list[Path] = []
    if graph_file is not None:
        candidates.append(graph_file)
    candidates.append(project_root / _OUT_DIR / _GRAPH_FILE)
    candidates.append(project_root / _OUT_DIR / _GRAPH_FILE_JSON)

    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            if candidate.suffix == ".toon":
                g, _ = toon.load_graph(candidate)
                return g
            # JSON fallback
            data = json.loads(candidate.read_text())
            g = nx.DiGraph()
            for node in data.get("nodes", []):
                node = dict(node)
                nid = node.pop("id")
                g.add_node(nid, **node)
            for edge in data.get("edges", []):
                edge = dict(edge)
                frm = edge.pop("from")
                to = edge.pop("to")
                g.add_edge(frm, to, **edge)
            return g
        except Exception as exc:
            warnings.warn(f"[server] Failed to load {candidate}: {exc}")

    return nx.DiGraph()


def _reload_graph(
    project_root: Path, graph: nx.DiGraph, graph_file: Path | None = None
) -> nx.DiGraph:
    """Reload graph in-place."""
    new_graph = _load_graph(project_root, graph_file=graph_file)
    graph.clear()
    graph.update(new_graph)
    return graph


def run_server(project_root: Path | None = None, graph_file: Path | None = None) -> None:
    """Start the MCP stdio server."""
    if not _MCP_AVAILABLE:
        print(
            "ERROR: 'mcp' package is not installed. Run: pip install mcp",
            file=sys.stderr,
        )
        sys.exit(1)

    if project_root is None:
        project_root = Path.cwd()

    # Load graph at startup
    graph = _load_graph(project_root, graph_file=graph_file)

    server = Server("infra-graph")

    @server.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name="get_minimal_context",
                description=(
                    "~100-token summary of the infrastructure graph: "
                    "god nodes, community count, and a quick orientation."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            mcp_types.Tool(
                name="get_blast_radius",
                description=(
                    "BFS traversal from a node; returns all affected resources "
                    "with depth and edge chain."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "The node ID to start from (e.g. resource.aws_vpc.main)",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximum BFS depth (default 5)",
                            "default": 5,
                        },
                    },
                    "required": ["node_id"],
                },
            ),
            mcp_types.Tool(
                name="query_graph",
                description=(
                    "BFS/DFS from any node. Supports direction (downstream/upstream/both), "
                    "edge type filtering, and a token budget."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "from_node": {"type": "string"},
                        "direction": {
                            "type": "string",
                            "enum": ["downstream", "upstream", "both"],
                            "default": "downstream",
                        },
                        "edge_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by edge types (empty = all)",
                        },
                        "max_depth": {"type": "integer", "default": 3},
                        "token_budget": {"type": "integer", "default": 2000},
                    },
                    "required": ["from_node"],
                },
            ),
            mcp_types.Tool(
                name="get_resource_context",
                description="Full context for one resource: edges, community, source file, line.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                    },
                    "required": ["node_id"],
                },
            ),
            mcp_types.Tool(
                name="get_architecture_overview",
                description="Community-level map with coupling warnings.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            mcp_types.Tool(
                name="detect_changes",
                description=(
                    "Risk-scored impact analysis for a git diff. "
                    "Pass a unified diff string; returns affected resources ranked by risk."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "diff_text": {
                            "type": "string",
                            "description": "Unified diff string (output of git diff)",
                        },
                    },
                    "required": ["diff_text"],
                },
            ),
            mcp_types.Tool(
                name="find_hub_nodes",
                description="Return the highest-degree (most connected) resources.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "top_n": {"type": "integer", "default": 10},
                    },
                    "required": [],
                },
            ),
            mcp_types.Tool(
                name="get_knowledge_gaps",
                description="Find orphaned resources, AMBIGUOUS edges, and dangling references.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            mcp_types.Tool(
                name="build_or_update_graph",
                description=(
                    "Trigger a graph rebuild from within the assistant. "
                    "Pass the project path and whether to do an incremental update."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to the infrastructure project root",
                        },
                        "update_only": {
                            "type": "boolean",
                            "default": False,
                            "description": "If true, only re-parse changed files",
                        },
                    },
                    "required": ["path"],
                },
            ),
            mcp_types.Tool(
                name="search_resources",
                description="Keyword search across node names, IDs, types, and labels.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> list[mcp_types.TextContent]:
        # Reload graph before each call to pick up any changes
        _reload_graph(project_root, graph, graph_file=graph_file)

        try:
            result = _dispatch(graph, name, arguments, project_root, graph_file=graph_file)
        except Exception as exc:
            result = {"error": str(exc), "tool": name}

        return [mcp_types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    import asyncio

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())


def _dispatch(
    graph: nx.DiGraph,
    name: str,
    args: dict[str, Any],
    project_root: Path,
    graph_file: Path | None = None,
) -> Any:
    if name == "get_minimal_context":
        return T.get_minimal_context(graph)
    elif name == "get_blast_radius":
        return T.get_blast_radius(
            graph,
            node_id=args["node_id"],
            max_depth=int(args.get("max_depth", 5)),
        )
    elif name == "query_graph":
        return T.query_graph(
            graph,
            from_node=args["from_node"],
            direction=args.get("direction", "downstream"),
            edge_types=args.get("edge_types") or None,
            max_depth=int(args.get("max_depth", 3)),
            token_budget=int(args.get("token_budget", 2000)),
        )
    elif name == "get_resource_context":
        return T.get_resource_context(graph, node_id=args["node_id"])
    elif name == "get_architecture_overview":
        return T.get_architecture_overview(graph)
    elif name == "detect_changes":
        return T.detect_changes(graph, diff_text=args["diff_text"])
    elif name == "find_hub_nodes":
        return T.find_hub_nodes(graph, top_n=int(args.get("top_n", 10)))
    elif name == "get_knowledge_gaps":
        return T.get_knowledge_gaps(graph)
    elif name == "build_or_update_graph":
        result = T.build_or_update_graph(
            path=args["path"],
            update_only=bool(args.get("update_only", False)),
        )
        # Reload the graph after build
        if result.get("success"):
            _reload_graph(project_root, graph, graph_file=graph_file)
        return result
    elif name == "search_resources":
        return T.search_resources(graph, query=args["query"])
    else:
        return {"error": f"Unknown tool: {name}"}
