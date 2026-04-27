"""
infra-graph CLI entry point.

Commands:
  build <path> [--update] [--mode deep] [--watch]
  query "<question>"
  blast-radius <node_id_or_file>
  path <from> <to>
  status
  visualize
  serve
  install [--platform claude-code|cursor|codex|opencode]
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .graph.blast_radius import find_path, get_blast_radius
from .graph.builder import GraphBuilder
from .graph.report import generate_report


def _get_builder(project_root: Path) -> GraphBuilder:
    return GraphBuilder(project_root)


def _load_graph_or_exit(builder: GraphBuilder) -> None:
    ok = builder.load_graph()
    if not ok or builder.graph.number_of_nodes() == 0:
        click.echo(
            "No graph found. Run `infra-graph build <path>` first.",
            err=True,
        )
        sys.exit(1)


@click.group()
@click.version_option(package_name="infra-graph")
def cli() -> None:
    """infra-graph: Infrastructure knowledge graph for Claude Code."""


# ── build ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--update", is_flag=True, help="Incremental update (skip unchanged files)")
@click.option("--mode", default="standard", type=click.Choice(["standard", "deep"]),
              help="deep uses graspologic Leiden for community detection")
@click.option("--watch", is_flag=True, help="Watch for file changes and auto-rebuild")
@click.option("--format", "fmt", default="toon", type=click.Choice(["toon", "json"]),
              help="Output graph format (default: toon)")
def build(path: str, update: bool, mode: str, watch: bool, fmt: str) -> None:
    """Build or update the infrastructure knowledge graph."""
    project_root = Path(path).resolve()
    builder = GraphBuilder(project_root)

    click.echo(f"Building graph for: {project_root}")
    if update:
        click.echo("Mode: incremental update")

    stats = builder.build(update_only=update, output_format=fmt)

    click.echo(
        f"Done. nodes={stats['nodes']}, edges={stats['edges']}, "
        f"files_parsed={stats['files_parsed']}, files_skipped={stats.get('files_skipped', 0)}"
    )

    # Generate reports
    report_path = generate_report(builder.graph, builder.out_dir, stats)
    click.echo(f"Report: {report_path}")

    graph_file_name = "graph.toon" if fmt == "toon" else "graph.json"
    graph_path = builder.out_dir / graph_file_name
    click.echo(f"Graph:  {graph_path}")

    if watch:
        _watch_mode(project_root, builder)


def _watch_mode(project_root: Path, builder: GraphBuilder) -> None:
    """Watch for file changes and rebuild incrementally."""
    try:
        import time

        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        click.echo("watchdog not installed. Run: pip install watchdog", err=True)
        sys.exit(1)

    click.echo(f"Watching {project_root} for changes... (Ctrl+C to stop)")

    class RebuildHandler(FileSystemEventHandler):
        def on_modified(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            p = Path(event.src_path)
            if p.suffix in (".tf", ".yml", ".yaml"):
                click.echo(f"  Changed: {p.name} — rebuilding...")
                builder.build(update_only=True)
                click.echo("  Done.")

        def on_created(self, event):  # type: ignore[override]
            self.on_modified(event)

    observer = Observer()
    observer.schedule(RebuildHandler(), str(project_root), recursive=True)
    observer.start()
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# ── query ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("question")
@click.option("--path", default=".", type=click.Path(exists=True, file_okay=False))
def query(question: str, path: str) -> None:
    """Search the graph and print relevant context."""
    project_root = Path(path).resolve()
    builder = _get_builder(project_root)
    _load_graph_or_exit(builder)

    results = builder.search(question)
    if not results:
        click.echo("No matching resources found.")
        return

    click.echo(f"Top results for '{question}':\n")
    for r in results[:10]:
        click.echo(f"  {r['id']}  [{r['type']}/{r['kind']}]  score={r['score']}")
        if r.get("file"):
            click.echo(f"    File: {r['file']}")
        click.echo()


# ── blast-radius ──────────────────────────────────────────────────────────────

@cli.command("blast-radius")
@click.argument("node_id")
@click.option("--path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--max-depth", default=5, show_default=True)
def blast_radius_cmd(node_id: str, path: str, max_depth: int) -> None:
    """Show all resources affected by changing a node."""
    project_root = Path(path).resolve()
    builder = _get_builder(project_root)
    _load_graph_or_exit(builder)

    # Try exact match first, then search
    if node_id not in builder.graph:
        matches = builder.search(node_id)
        if not matches:
            click.echo(f"Node '{node_id}' not found.", err=True)
            sys.exit(1)
        node_id = matches[0]["id"]
        click.echo(f"Using closest match: {node_id}")

    result = get_blast_radius(builder.graph, node_id, max_depth=max_depth)
    click.echo(f"\nBlast radius for: {result['root']}")
    click.echo(f"Total affected: {result['total_affected']}\n")

    for item in result["affected"]:
        indent = "  " * item["depth"]
        click.echo(f"{indent}depth={item['depth']}  {item['node_id']}  [{item['type']}]")


# ── path ──────────────────────────────────────────────────────────────────────

@cli.command("path")
@click.argument("from_node")
@click.argument("to_node")
@click.option("--path", "project_path", default=".", type=click.Path(exists=True, file_okay=False))
def path_cmd(from_node: str, to_node: str, project_path: str) -> None:
    """Find the shortest dependency path between two nodes."""
    project_root = Path(project_path).resolve()
    builder = _get_builder(project_root)
    _load_graph_or_exit(builder)

    result = find_path(builder.graph, from_node, to_node)
    if result.get("error"):
        click.echo(f"Error: {result['error']}", err=True)
        sys.exit(1)

    if not result["path"]:
        click.echo("No path found.")
        return

    click.echo(f"\nPath (length {result['length']}):")
    for i, node in enumerate(result["path"]):
        prefix = "  " if i > 0 else "  "
        if i < len(result["edges"]):
            edge = result["edges"][i]
            click.echo(f"{prefix}{node}")
            click.echo(f"    --[{edge['type']}]-->")
        else:
            click.echo(f"{prefix}{node}")


# ── status ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, file_okay=False))
def status(path: str) -> None:
    """Show graph statistics."""
    project_root = Path(path).resolve()
    builder = _get_builder(project_root)

    # Check for graph.toon first, fall back to graph.json
    graph_file = builder.out_dir / "graph.toon"
    if not graph_file.exists():
        graph_file = builder.out_dir / "graph.json"
    if not graph_file.exists():
        click.echo("No graph built yet. Run `infra-graph build <path>`.")
        return

    ok = builder.load_graph()
    if not ok:
        click.echo("Graph file exists but could not be loaded.", err=True)
        return

    g = builder.graph
    from .graph.community import get_community_summary
    communities = get_community_summary(g)

    click.echo(f"\ninfra-graph status for: {project_root}")
    click.echo(f"  Nodes:       {g.number_of_nodes()}")
    click.echo(f"  Edges:       {g.number_of_edges()}")
    click.echo(f"  Communities: {len(communities)}")
    click.echo(f"  Graph file:  {graph_file}")

    # Type breakdown
    from collections import Counter
    types = Counter(attrs.get("type", "unknown") for _, attrs in g.nodes(data=True))
    click.echo("\n  Node types:")
    for t, count in types.most_common():
        click.echo(f"    {t:30s} {count}")


# ── visualize ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--open", "open_browser", is_flag=True, help="Open in browser after generating")
def visualize(path: str, open_browser: bool) -> None:
    """Generate an interactive HTML visualization."""
    project_root = Path(path).resolve()
    builder = _get_builder(project_root)
    _load_graph_or_exit(builder)

    from .viz.html_report import generate_html
    html_path = generate_html(builder.graph, builder.out_dir, title=project_root.name)
    click.echo(f"Visualization: {html_path}")

    if open_browser:
        import webbrowser
        webbrowser.open(html_path.as_uri())


# ── federate ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--output", "-o", default=None,
              help="Output path (default: ./federated-graph.toon)")
@click.option("--format", "fmt", default="toon",
              type=click.Choice(["toon", "json"]))
def federate(paths: tuple[str, ...], output: str | None, fmt: str) -> None:
    """Merge multiple repo graph files into a federated cross-repo graph."""
    import json as _json

    from .graph.federation import federate as _federate
    from .graph.toon import dump_graph

    # Accept both repo root dirs and direct graph file paths
    graph_files = []
    for p in paths:
        pp = Path(p).resolve()
        if pp.is_dir():
            toon_file = pp / "infra-graph-out" / "graph.toon"
            json_file = pp / "infra-graph-out" / "graph.json"
            if toon_file.exists():
                graph_files.append(toon_file)
            elif json_file.exists():
                graph_files.append(json_file)
            else:
                click.echo(
                    f"No graph found in {pp}. Run `infra-graph build` first.", err=True
                )
                sys.exit(1)
        else:
            graph_files.append(pp)

    click.echo(f"Federating {len(graph_files)} graphs...")
    for f in graph_files:
        click.echo(f"  {f}")

    graph, meta = _federate(graph_files)

    out_path = Path(output).resolve() if output else Path("./federated-graph.toon").resolve()
    if fmt == "json":
        if not str(out_path).endswith(".json"):
            out_path = out_path.with_suffix(".json")
        nodes = [{"id": nid, **attrs} for nid, attrs in graph.nodes(data=True)]
        edges = [{"from": f, "to": t, **d} for f, t, d in graph.edges(data=True)]
        out_path.write_text(
            _json.dumps({"meta": meta, "nodes": nodes, "edges": edges}, indent=2, default=str)
        )
    else:
        if not str(out_path).endswith(".toon"):
            out_path = out_path.with_suffix(".toon")
        dump_graph(graph, out_path, meta)

    click.echo(f"\nFederated graph: {out_path}")
    click.echo(f"  nodes={graph.number_of_nodes()}, edges={graph.number_of_edges()}")
    click.echo(f"  node_count_before={meta.get('total_nodes_before_federation', '?')}")
    click.echo(f"  unknowns_resolved={meta.get('unknowns_resolved', '?')}")
    click.echo(f"  provisioned_by_edges={meta.get('provisioned_by_edges', '?')}")


# ── serve ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--graph", "graph_path", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Explicit graph file to serve (overrides default search)")
def serve(path: str, graph_path: str | None) -> None:
    """Start the MCP stdio server."""
    project_root = Path(path).resolve()
    from .mcp.server import run_server
    gp = Path(graph_path).resolve() if graph_path else None
    run_server(project_root=project_root, graph_file=gp)


# ── install ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--platform",
    default="claude-code",
    type=click.Choice(["claude-code", "cursor", "codex", "opencode"]),
    show_default=True,
    help="Target AI assistant platform",
)
@click.option("--path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--federated",
    default=None,
    type=click.Path(dir_okay=False),
    help="Path to a federated graph file; adds infra-graph-federated MCP entry",
)
def install(platform: str, path: str, federated: str | None) -> None:
    """Install infra-graph integration files into a project."""
    project_root = Path(path).resolve()
    click.echo(f"Installing infra-graph for {platform} in: {project_root}")

    federated_path = Path(federated).resolve() if federated else None

    if platform == "claude-code":
        from .install.claude import install as _install
        results = _install(project_root, federated_graph=federated_path)
    elif platform == "cursor":
        from .install.cursor import install as _install
        results = _install(project_root)
    elif platform in ("codex", "opencode"):
        from .install.codex import install as _install
        results = _install(project_root)
    else:
        click.echo(f"Unknown platform: {platform}", err=True)
        sys.exit(1)

    for filename, action in results.items():
        click.echo(f"  {action:10s}  {filename}")

    click.echo("\nDone. Run `infra-graph build .` to build the graph.")
