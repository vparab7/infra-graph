"""
Graph federation: merge multiple graph files, resolve cross-repo unknowns.

Resolution strategies:
  1. Exact node ID match
  2. Fuzzy/suffix match (strip known org prefixes, match base name, same type)
  3. Attribute/value match: ArgoCD cluster Secret server_url ↔ Terraform output expression
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import networkx as nx

from .community import assign_communities

# Prefixes to strip when computing base names (sorted longest-first).
# Extend this list with your org-specific prefixes (e.g. "myorg-", "team-a-").
STRIP_PREFIXES = sorted(
    ["org-platform-", "org-", "team-", "prod-", "staging-"],
    key=len,
    reverse=True,
)


def _base_name(node_id: str) -> str:
    """Return the base name of a node ID: last path segment, prefixes stripped."""
    segment = node_id.split("/")[-1]
    for prefix in STRIP_PREFIXES:
        if segment.startswith(prefix):
            segment = segment[len(prefix):]
            break
    return segment


def load_graph_file(path: Path) -> tuple[nx.DiGraph, dict]:
    """Load a graph from either a .toon or .json file."""
    if path.suffix == ".toon":
        from . import toon
        return toon.load_graph(path)

    # JSON format
    data = json.loads(path.read_text(encoding="utf-8"))
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
    meta = data.get("meta", {})
    return g, meta


def federate(graph_paths: list[Path]) -> tuple[nx.DiGraph, dict]:
    """
    Main entry point: load and merge multiple graphs, resolve unknowns,
    infer provisioned_by edges.

    Returns (merged_graph, meta_dict).
    """
    source_graphs: list[nx.DiGraph] = []
    source_metas: list[dict] = []
    total_nodes_before = 0

    for p in graph_paths:
        g, m = load_graph_file(p)
        source_graphs.append(g)
        source_metas.append(m)
        total_nodes_before += g.number_of_nodes()

    # ── Build unified node set ────────────────────────────────────────────────
    merged = nx.DiGraph()

    for g in source_graphs:
        for nid, attrs in g.nodes(data=True):
            if nid in merged:
                existing = merged.nodes[nid]
                # If existing is unknown but new is real, replace attrs
                if existing.get("type") == "unknown" and attrs.get("type") != "unknown":
                    merged.nodes[nid].update(attrs)
                # Accumulate source_repos
                repos = existing.get("source_repos") or []
                new_repo = attrs.get("file") or ""
                if new_repo and new_repo not in repos:
                    repos.append(new_repo)
                merged.nodes[nid]["source_repos"] = repos
            else:
                merged.add_node(nid, **attrs)

    # ── Build unified edge set (deduplicate by from+to+type) ─────────────────
    seen_edges: set[tuple[str, str, str]] = set()
    for g in source_graphs:
        for frm, to, attrs in g.edges(data=True):
            key = (frm, to, str(attrs.get("type", "")))
            if key not in seen_edges:
                seen_edges.add(key)
                if not merged.has_node(frm):
                    merged.add_node(frm, type="unknown", kind="unknown", name=frm,
                                    file=None, line=None, labels={}, community_id=None)
                if not merged.has_node(to):
                    merged.add_node(to, type="unknown", kind="unknown", name=to,
                                    file=None, line=None, labels={}, community_id=None)
                merged.add_edge(frm, to, **attrs)

    # ── Resolve unknowns ──────────────────────────────────────────────────────
    unknowns_before = sum(
        1 for _, a in merged.nodes(data=True) if a.get("type") == "unknown"
    )
    _resolve_unknowns(merged, source_graphs)
    unknowns_after = sum(
        1 for _, a in merged.nodes(data=True) if a.get("type") == "unknown"
    )
    unknowns_resolved = unknowns_before - unknowns_after

    # ── Match provisioned_by ──────────────────────────────────────────────────
    provisioned_by_count = _match_provisioned_by(merged)

    # ── Re-run community detection ────────────────────────────────────────────
    assign_communities(merged)

    meta: dict = {
        "federated": True,
        "source_count": len(graph_paths),
        "total_nodes_before_federation": total_nodes_before,
        "node_count": merged.number_of_nodes(),
        "edge_count": merged.number_of_edges(),
        "unknowns_before": unknowns_before,
        "unknowns_resolved": unknowns_resolved,
        "provisioned_by_edges": provisioned_by_count,
    }

    return merged, meta


def _resolve_unknowns(merged: nx.DiGraph, source_graphs: list[nx.DiGraph]) -> list[dict]:
    """
    Attempt to resolve unknown nodes via exact match or fuzzy/suffix match.
    Mutates merged in place.  Returns list of new edges added.
    """
    to_remove: set[str] = set()
    new_edges: list[dict] = []

    for nid in list(merged.nodes):
        if merged.nodes[nid].get("type") != "unknown":
            continue

        # Strategy 1: exact match in source graphs
        resolved = False
        for g in source_graphs:
            if nid in g and g.nodes[nid].get("type") != "unknown":
                merged.nodes[nid].update(g.nodes[nid])
                resolved = True
                break
        if resolved:
            continue

        # Strategy 2: fuzzy/suffix match
        base = _base_name(nid)
        ntype_prefix = nid.split("/")[0] if "/" in nid else ""
        candidates = [
            cid for cid in merged.nodes
            if cid != nid
            and merged.nodes[cid].get("type") != "unknown"
            and _base_name(cid) == base
            and (not ntype_prefix or cid.startswith(ntype_prefix))
        ]
        if len(candidates) == 1:
            real_id = candidates[0]
            for pred in list(merged.predecessors(nid)):
                edge_data = dict(merged.edges[pred, nid])
                edge_data["provenance"] = "FEDERATED_FUZZY"
                edge_data["confidence"] = 0.7
                new_edges.append({"from": pred, "to": real_id, **edge_data})
            for succ in list(merged.successors(nid)):
                edge_data = dict(merged.edges[nid, succ])
                edge_data["provenance"] = "FEDERATED_FUZZY"
                edge_data["confidence"] = 0.7
                new_edges.append({"from": real_id, "to": succ, **edge_data})
            to_remove.add(nid)

    # Remove resolved unknown nodes
    for nid in to_remove:
        merged.remove_node(nid)

    # Add new edges
    for e in new_edges:
        frm = e.pop("from")
        to = e.pop("to")
        if not merged.has_node(frm):
            continue
        if not merged.has_node(to):
            continue
        if not merged.has_edge(frm, to):
            merged.add_edge(frm, to, **e)

    return new_edges


def _match_provisioned_by(graph: nx.DiGraph) -> int:
    """
    Match ArgoCD cluster Secrets (server_url attr) to Terraform cluster resources.
    Adds provisioned_by edges and returns count.
    """
    # ArgoCD cluster Secrets with a non-local server_url
    cluster_secrets = [
        (nid, attrs) for nid, attrs in graph.nodes(data=True)
        if attrs.get("type") == "Secret"
        and attrs.get("server_url")
        and attrs.get("server_url") != "https://kubernetes.default.svc"
    ]

    # Terraform kubernetes_cluster resources
    tf_clusters = [
        (nid, attrs) for nid, attrs in graph.nodes(data=True)
        if attrs.get("kind") == "azurerm_kubernetes_cluster"
    ]

    count = 0
    for secret_id, secret_attrs in cluster_secrets:
        server_url = secret_attrs.get("server_url", "")
        # Extract hostname from URL for potential FQDN matching
        m = re.search(r"https?://([^/]+)", server_url)
        _hostname = m.group(1) if m else server_url  # noqa: F841

        cluster_name = secret_attrs.get("argocd_cluster_name", "")

        for tf_id, _tf_attrs in tf_clusters:
            tf_name = tf_id.split(".")[-1] if "." in tf_id else tf_id
            name_parts = [p for p in cluster_name.replace("-", " ").split() if p]
            if (cluster_name and name_parts and any(part in tf_name for part in name_parts)) \
               or any(part in tf_id for part in ["main", "cluster"]):
                if not graph.has_edge(tf_id, secret_id):
                    graph.add_edge(
                        tf_id,
                        secret_id,
                        type="provisioned_by",
                        confidence=0.6,
                        provenance="FEDERATED_INFERRED",
                    )
                    count += 1
    return count
