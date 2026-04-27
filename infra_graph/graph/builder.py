"""
Graph builder: scans infrastructure files, builds a NetworkX DiGraph,
persists to JSON with SHA-256 caching.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path
from typing import Any

import networkx as nx

from ..parsers.tf_parser import TerraformParser
from ..parsers.yaml_parser import YAMLParser
from .community import assign_communities

# Output directory name
_OUT_DIR = "infra-graph-out"
_GRAPH_FILE = "graph.toon"
_GRAPH_FILE_JSON = "graph.json"
_CACHE_FILE = "cache/file_hashes.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


class GraphBuilder:
    """Build and persist an infrastructure knowledge graph."""

    def __init__(self, project_root: Path, out_dir: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.out_dir = (out_dir or project_root / _OUT_DIR).resolve()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "cache").mkdir(exist_ok=True)

        self.graph: nx.DiGraph = nx.DiGraph()
        self._cache: dict[str, str] = {}  # filepath → sha256
        self._tf_parser = TerraformParser()
        self._yaml_parser = YAMLParser()

    # ── Persistence ──────────────────────────────────────────────────────────

    def load_cache(self) -> None:
        cache_path = self.out_dir / _CACHE_FILE
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text())
            except Exception:
                self._cache = {}

    def save_cache(self) -> None:
        cache_path = self.out_dir / _CACHE_FILE
        cache_path.write_text(json.dumps(self._cache, indent=2))

    def load_graph(self) -> bool:
        """Load persisted graph. Try graph.toon first, then graph.json. Returns True if successful."""
        from . import toon

        toon_path = self.out_dir / _GRAPH_FILE
        json_path = self.out_dir / _GRAPH_FILE_JSON

        if toon_path.exists():
            try:
                self.graph, _ = toon.load_graph(toon_path)
                return True
            except Exception as exc:
                warnings.warn(f"[builder] Failed to load {toon_path}: {exc}")

        if json_path.exists():
            try:
                data = json.loads(json_path.read_text())
                self.graph = nx.DiGraph()
                for node in data.get("nodes", []):
                    nid = node.pop("id")
                    self.graph.add_node(nid, **node)
                for edge in data.get("edges", []):
                    frm = edge.pop("from")
                    to = edge.pop("to")
                    self.graph.add_edge(frm, to, **edge)
                return True
            except Exception as exc:
                warnings.warn(f"[builder] Failed to load graph: {exc}")

        return False

    def save_graph(self, output_format: str = "toon") -> None:
        """Persist graph (default: TOON format; pass output_format='json' for JSON)."""
        from . import toon as _toon

        meta = {
            "project_root": str(self.project_root),
            "node_count": self.graph.number_of_nodes(),
            "edge_count": self.graph.number_of_edges(),
        }

        if output_format == "toon":
            graph_path = self.out_dir / _GRAPH_FILE
            _toon.dump_graph(self.graph, graph_path, meta)
        else:
            # JSON format
            graph_path = self.out_dir / _GRAPH_FILE_JSON
            nodes = []
            for nid, attrs in self.graph.nodes(data=True):
                node_data = {"id": nid}
                node_data.update(attrs)
                nodes.append(node_data)

            edges = []
            for frm, to, attrs in self.graph.edges(data=True):
                edge_data = {"from": frm, "to": to}
                edge_data.update(attrs)
                edges.append(edge_data)

            data = {
                "meta": meta,
                "nodes": nodes,
                "edges": edges,
            }
            graph_path.write_text(json.dumps(data, indent=2, default=str))

    # ── File discovery ────────────────────────────────────────────────────────

    def _collect_files(self, ignore_spec: Any = None) -> list[Path]:
        """Walk project root and return parseable infrastructure files."""
        extensions = {".tf", ".yml", ".yaml"}
        files: list[Path] = []
        for path in self.project_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in extensions:
                continue
            # Skip hidden dirs (except .github for Actions)
            rel = path.relative_to(self.project_root)
            parts = rel.parts
            skip = False
            for part in parts[:-1]:
                if part.startswith(".") and part != ".github":
                    skip = True
                    break
            if skip:
                continue
            if ignore_spec is not None:
                try:
                    if ignore_spec.match_file(str(rel)):
                        continue
                except Exception:
                    pass
            files.append(path)
        return files

    def _load_ignore_spec(self) -> Any:
        """Load .infraignore from project root if present."""
        ignore_file = self.project_root / ".infraignore"
        if not ignore_file.exists():
            return None
        try:
            import pathspec
            spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore_file.read_text().splitlines())
            return spec
        except Exception:
            return None

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self, update_only: bool = False, output_format: str = "toon") -> dict[str, Any]:
        """
        Scan all infrastructure files and build/update the graph.

        Args:
            update_only: If True, skip files whose SHA-256 matches the cache.
            output_format: 'toon' (default) or 'json'.

        Returns:
            Stats dict with counts of nodes, edges, files parsed.
        """
        self.load_cache()
        if update_only:
            self.load_graph()

        ignore_spec = self._load_ignore_spec()
        files = sorted(self._collect_files(ignore_spec))

        parsed_files = 0
        skipped_files = 0
        all_nodes: list[dict] = []
        all_edges: list[dict] = []

        for fpath in files:
            fkey = str(fpath)
            sha = _sha256(fpath)
            if update_only and self._cache.get(fkey) == sha:
                skipped_files += 1
                continue

            result = self._parse_single(fpath)
            if result:
                all_nodes.extend(result.get("nodes", []))
                all_edges.extend(result.get("edges", []))
                self._cache[fkey] = sha
                parsed_files += 1

        # Finalize YAML (k8s selector resolution)
        extra_edges = self._yaml_parser.finalize()
        all_edges.extend(extra_edges)

        # Deduplicate nodes (keep last seen, same id)
        node_map: dict[str, dict] = {}
        for n in all_nodes:
            nid = n.get("id")
            if nid:
                node_map[nid] = n

        # Deduplicate edges
        edge_set: set[tuple] = set()
        unique_edges: list[dict] = []
        for e in all_edges:
            key = (e.get("from"), e.get("to"), e.get("type"))
            if key not in edge_set and all(k is not None for k in key):
                edge_set.add(key)
                unique_edges.append(e)

        # If updating, merge with existing graph
        if update_only:
            for nid, attrs in self.graph.nodes(data=True):
                if nid not in node_map:
                    node_map[nid] = {"id": nid, **attrs}
            for frm, to, attrs in self.graph.edges(data=True):
                key = (frm, to, attrs.get("type"))
                if key not in edge_set:
                    edge_set.add(key)
                    unique_edges.append({"from": frm, "to": to, **attrs})

        # Build graph
        self.graph = nx.DiGraph()
        for nid, attrs in node_map.items():
            node_attrs = {k: v for k, v in attrs.items() if k != "id"}
            self.graph.add_node(nid, **node_attrs)

        for edge in unique_edges:
            frm = edge.get("from")
            to = edge.get("to")
            if frm and to:
                edge_attrs = {k: v for k, v in edge.items() if k not in ("from", "to")}
                # Ensure both nodes exist
                if frm not in self.graph:
                    self.graph.add_node(frm, type="unknown", kind="unknown", name=frm, file=None, line=None, labels={}, community_id=None)
                if to not in self.graph:
                    self.graph.add_node(to, type="unknown", kind="unknown", name=to, file=None, line=None, labels={}, community_id=None)
                self.graph.add_edge(frm, to, **edge_attrs)

        # Assign communities
        assign_communities(self.graph)

        self.save_cache()
        self.save_graph(output_format=output_format)

        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "files_parsed": parsed_files,
            "files_skipped": skipped_files,
        }

    def _parse_single(self, path: Path) -> dict[str, Any] | None:
        """Route a single file to the correct parser."""
        try:
            if path.suffix == ".tf":
                return self._tf_parser.parse_file(path)
            elif path.suffix in (".yml", ".yaml"):
                return self._yaml_parser.parse_file(path)
        except Exception as exc:
            warnings.warn(f"[builder] Error parsing {path}: {exc}")
        return None

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> dict | None:
        if node_id not in self.graph:
            return None
        attrs = dict(self.graph.nodes[node_id])
        attrs["id"] = node_id
        attrs["in_degree"] = self.graph.in_degree(node_id)
        attrs["out_degree"] = self.graph.out_degree(node_id)
        return attrs

    def get_neighbors(self, node_id: str, direction: str = "both") -> list[dict]:
        """Return neighbors with edge data."""
        result = []
        if direction in ("out", "both"):
            for _, target, data in self.graph.out_edges(node_id, data=True):
                result.append({"node_id": target, "edge": data, "direction": "out"})
        if direction in ("in", "both"):
            for source, _, data in self.graph.in_edges(node_id, data=True):
                result.append({"node_id": source, "edge": data, "direction": "in"})
        return result

    def search(self, query: str) -> list[dict]:
        """Keyword search across node ids, names, types, labels."""
        query_lower = query.lower()
        results = []
        for nid, attrs in self.graph.nodes(data=True):
            score = 0
            if query_lower in nid.lower():
                score += 3
            if query_lower in str(attrs.get("name", "")).lower():
                score += 2
            if query_lower in str(attrs.get("type", "")).lower():
                score += 1
            if query_lower in str(attrs.get("kind", "")).lower():
                score += 1
            labels_str = " ".join(f"{k}={v}" for k, v in (attrs.get("labels") or {}).items())
            if query_lower in labels_str.lower():
                score += 1
            if score > 0:
                node_data = {"id": nid, "score": score}
                node_data.update(attrs)
                results.append(node_data)
        results.sort(key=lambda x: x["score"], reverse=True)
        return results
