"""Tests for graph federation and ArgoCD cluster Secret server_url extraction."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import networkx as nx
import pytest

from infra_graph.graph.federation import _base_name, federate
from infra_graph.parsers.k8s_schema import KubernetesParser

FIXTURES = Path(__file__).parent / "fixtures"

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_graph(nodes: list[dict], edges: list[dict] | None = None) -> nx.DiGraph:
    """Build a DiGraph from plain dicts (mirrors the JSON graph format)."""
    g = nx.DiGraph()
    for node in nodes:
        n = dict(node)
        nid = n.pop("id")
        g.add_node(nid, **n)
    for edge in edges or []:
        e = dict(edge)
        frm = e.pop("from")
        to = e.pop("to")
        g.add_edge(frm, to, **e)
    return g


def _write_json_graph(path: Path, nodes: list[dict], edges: list[dict]) -> None:
    data = {"nodes": nodes, "edges": edges, "meta": {}}
    path.write_text(json.dumps(data), encoding="utf-8")


# ── Federation: exact match ───────────────────────────────────────────────────


class TestExactMatchResolvesUnknown:
    def test_exact_match_resolves_unknown(self, tmp_path):
        """Graph A has helm_chart/nginx as unknown; graph B has it as real.
        After federation it should be resolved (type != 'unknown')."""
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "Application/argocd/myapp", "type": "Application", "kind": "Application",
                 "name": "myapp", "file": "a/app.yaml", "line": 1, "labels": {}, "community_id": None},
                {"id": "helm_chart/nginx", "type": "unknown", "kind": "unknown",
                 "name": "helm_chart/nginx", "file": None, "line": None, "labels": {}, "community_id": None},
            ],
            edges=[
                {"from": "Application/argocd/myapp", "to": "helm_chart/nginx",
                 "type": "uses_chart", "confidence": 1.0, "provenance": "EXTRACTED"},
            ],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": "helm_chart/nginx", "type": "helm_chart", "kind": "helm_chart",
                 "name": "nginx", "file": "b/charts.yaml", "line": 5, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])
        attrs = merged.nodes.get("helm_chart/nginx")
        assert attrs is not None
        assert attrs.get("type") != "unknown"

    def test_exact_match_edge_preserved(self, tmp_path):
        """The uses_chart edge must still exist after exact-match resolution."""
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "Application/argocd/myapp", "type": "Application", "kind": "Application",
                 "name": "myapp", "file": "a/app.yaml", "line": 1, "labels": {}, "community_id": None},
                {"id": "helm_chart/nginx", "type": "unknown", "kind": "unknown",
                 "name": "nginx", "file": None, "line": None, "labels": {}, "community_id": None},
            ],
            edges=[
                {"from": "Application/argocd/myapp", "to": "helm_chart/nginx",
                 "type": "uses_chart", "confidence": 1.0, "provenance": "EXTRACTED"},
            ],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": "helm_chart/nginx", "type": "helm_chart", "kind": "helm_chart",
                 "name": "nginx", "file": "b/charts.yaml", "line": 5, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])
        assert merged.has_edge("Application/argocd/myapp", "helm_chart/nginx")


# ── Federation: fuzzy match ───────────────────────────────────────────────────


class TestFuzzyMatchResolvesUnknown:
    def test_fuzzy_match_resolves_unknown(self, tmp_path):
        """Graph A has helm_chart/myapp (unknown); graph B has helm_chart/org-myapp (real).
        Fuzzy match should resolve the unknown node by stripping the org- prefix."""
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "Application/argocd/myapp", "type": "Application", "kind": "Application",
                 "name": "myapp", "file": "a/app.yaml", "line": 1, "labels": {}, "community_id": None},
                {"id": "helm_chart/myapp", "type": "unknown", "kind": "unknown",
                 "name": "myapp", "file": None, "line": None, "labels": {}, "community_id": None},
            ],
            edges=[
                {"from": "Application/argocd/myapp", "to": "helm_chart/myapp",
                 "type": "uses_chart", "confidence": 1.0, "provenance": "EXTRACTED"},
            ],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": "helm_chart/org-myapp", "type": "helm_chart", "kind": "helm_chart",
                 "name": "org-myapp", "file": "b/charts.yaml", "line": 5, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])
        # The unknown node should have been removed
        assert "helm_chart/myapp" not in merged.nodes()
        # The real node should still exist
        assert "helm_chart/org-myapp" in merged.nodes()

    def test_fuzzy_match_creates_edge_to_real_node(self, tmp_path):
        """After fuzzy resolution the edge should point to the real node."""
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "Application/argocd/myapp", "type": "Application", "kind": "Application",
                 "name": "myapp", "file": "a/app.yaml", "line": 1, "labels": {}, "community_id": None},
                {"id": "helm_chart/myapp", "type": "unknown", "kind": "unknown",
                 "name": "myapp", "file": None, "line": None, "labels": {}, "community_id": None},
            ],
            edges=[
                {"from": "Application/argocd/myapp", "to": "helm_chart/myapp",
                 "type": "uses_chart", "confidence": 1.0, "provenance": "EXTRACTED"},
            ],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": "helm_chart/org-myapp", "type": "helm_chart", "kind": "helm_chart",
                 "name": "org-myapp", "file": "b/charts.yaml", "line": 5, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])
        assert merged.has_edge("Application/argocd/myapp", "helm_chart/org-myapp")


# ── Federation: unrelated repos stay isolated ─────────────────────────────────


class TestUnrelatedReposStayIsolated:
    def test_unrelated_repos_stay_isolated(self, tmp_path):
        """Two completely unrelated graphs federate without creating spurious edges."""
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "resource.aws_vpc.main", "type": "resource", "kind": "aws_vpc",
                 "name": "main", "file": "a/main.tf", "line": 1, "labels": {}, "community_id": None},
            ],
            edges=[],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": "Deployment/default/api", "type": "Deployment", "kind": "Deployment",
                 "name": "api", "file": "b/deploy.yaml", "line": 1, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])

        # Both nodes exist
        assert "resource.aws_vpc.main" in merged.nodes()
        assert "Deployment/default/api" in merged.nodes()

        # No spurious edges between them
        assert not merged.has_edge("resource.aws_vpc.main", "Deployment/default/api")
        assert not merged.has_edge("Deployment/default/api", "resource.aws_vpc.main")
        assert merged.number_of_edges() == 0


# ── Federation: fuzzy provenance ─────────────────────────────────────────────


class TestFederatedFuzzyProvenance:
    def test_federated_fuzzy_provenance(self, tmp_path):
        """Resolved fuzzy edges must have provenance='FEDERATED_FUZZY' and confidence=0.7.

        helm_chart/frontend → _base_name → 'frontend'
        helm_chart/org-frontend → _base_name → 'frontend'  (strips 'org-' prefix)
        These share the same base so a fuzzy edge is created.
        """
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "Application/argocd/myapp", "type": "Application", "kind": "Application",
                 "name": "myapp", "file": "a/app.yaml", "line": 1, "labels": {}, "community_id": None},
                # unknown node — base name will be 'frontend' after stripping nothing
                {"id": "helm_chart/frontend", "type": "unknown", "kind": "unknown",
                 "name": "frontend", "file": None, "line": None, "labels": {}, "community_id": None},
            ],
            edges=[
                {"from": "Application/argocd/myapp", "to": "helm_chart/frontend",
                 "type": "uses_chart", "confidence": 1.0, "provenance": "EXTRACTED"},
            ],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                # real node — base name will also be 'frontend' (strips 'org-' prefix)
                {"id": "helm_chart/org-frontend", "type": "helm_chart", "kind": "helm_chart",
                 "name": "org-frontend", "file": "b/charts.yaml", "line": 3, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])

        # Find any edge with FEDERATED_FUZZY provenance
        fuzzy_edges = [
            (f, t, d)
            for f, t, d in merged.edges(data=True)
            if d.get("provenance") == "FEDERATED_FUZZY"
        ]
        assert len(fuzzy_edges) >= 1
        for _, _, edata in fuzzy_edges:
            assert edata["confidence"] == pytest.approx(0.7)

    def test_federated_fuzzy_confidence_value(self, tmp_path):
        """Fuzzy resolved edges always have exactly confidence=0.7.

        helm_chart/svc → base 'svc'; helm_chart/org-svc → base 'svc' (strips 'org-').
        """
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "Application/argocd/svc", "type": "Application", "kind": "Application",
                 "name": "svc", "file": "a/app.yaml", "line": 1, "labels": {}, "community_id": None},
                {"id": "helm_chart/svc", "type": "unknown", "kind": "unknown",
                 "name": "svc", "file": None, "line": None, "labels": {}, "community_id": None},
            ],
            edges=[
                {"from": "Application/argocd/svc", "to": "helm_chart/svc",
                 "type": "uses_chart", "confidence": 1.0, "provenance": "EXTRACTED"},
            ],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": "helm_chart/org-svc", "type": "helm_chart", "kind": "helm_chart",
                 "name": "org-svc", "file": "b/charts.yaml", "line": 2, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])
        fuzzy_edges = [
            d for _, _, d in merged.edges(data=True)
            if d.get("provenance") == "FEDERATED_FUZZY"
        ]
        assert len(fuzzy_edges) >= 1
        assert all(e["confidence"] == pytest.approx(0.7) for e in fuzzy_edges)


# ── Federation: node count ────────────────────────────────────────────────────


class TestNodeCountAfterFederation:
    def test_node_count_after_federation(self, tmp_path):
        """Nodes shared between graphs (exact id) are merged, not duplicated."""
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        shared_id = "helm_chart/common-lib"
        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "Application/argocd/app-a", "type": "Application", "kind": "Application",
                 "name": "app-a", "file": "a/app.yaml", "line": 1, "labels": {}, "community_id": None},
                {"id": shared_id, "type": "helm_chart", "kind": "helm_chart",
                 "name": "common-lib", "file": "a/charts.yaml", "line": 2, "labels": {}, "community_id": None},
            ],
            edges=[],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": "Application/argocd/app-b", "type": "Application", "kind": "Application",
                 "name": "app-b", "file": "b/app.yaml", "line": 1, "labels": {}, "community_id": None},
                {"id": shared_id, "type": "helm_chart", "kind": "helm_chart",
                 "name": "common-lib", "file": "b/charts.yaml", "line": 2, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])
        # 3 unique nodes: app-a, app-b, common-lib (shared deduped)
        assert merged.number_of_nodes() == 3

    def test_distinct_nodes_all_present(self, tmp_path):
        """All distinct nodes across graphs appear in the merged graph."""
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": "resource.aws_vpc.a", "type": "resource", "kind": "aws_vpc",
                 "name": "a", "file": "a/main.tf", "line": 1, "labels": {}, "community_id": None},
            ],
            edges=[],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": "resource.aws_vpc.b", "type": "resource", "kind": "aws_vpc",
                 "name": "b", "file": "b/main.tf", "line": 1, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])
        assert "resource.aws_vpc.a" in merged.nodes()
        assert "resource.aws_vpc.b" in merged.nodes()


# ── Federation: source_repos attr ────────────────────────────────────────────


class TestSourceReposAttr:
    def test_source_repos_attr(self, tmp_path):
        """Shared nodes get a source_repos list populated from subsequent graphs.

        The federation logic adds source_repos when a node is encountered a
        second time (already in ``merged``).  The second graph's file path is
        recorded there; the first graph's file path stays in the ``file`` attr.
        """
        graph_a_path = tmp_path / "a.json"
        graph_b_path = tmp_path / "b.json"

        shared_id = "helm_chart/shared-lib"
        _write_json_graph(
            graph_a_path,
            nodes=[
                {"id": shared_id, "type": "helm_chart", "kind": "helm_chart",
                 "name": "shared-lib", "file": "repo-a/charts.yaml", "line": 1, "labels": {}, "community_id": None},
            ],
            edges=[],
        )
        _write_json_graph(
            graph_b_path,
            nodes=[
                {"id": shared_id, "type": "helm_chart", "kind": "helm_chart",
                 "name": "shared-lib", "file": "repo-b/charts.yaml", "line": 1, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_a_path, graph_b_path])
        node_attrs = merged.nodes[shared_id]
        source_repos = node_attrs.get("source_repos") or []
        # The second graph's file is accumulated into source_repos
        assert "repo-b/charts.yaml" in source_repos
        # The first graph's file is preserved in the ``file`` attribute
        assert node_attrs.get("file") == "repo-a/charts.yaml"

    def test_unique_node_has_empty_or_single_source_repos(self, tmp_path):
        """A node appearing in only one graph has a single-entry source_repos."""
        graph_path = tmp_path / "a.json"
        _write_json_graph(
            graph_path,
            nodes=[
                {"id": "resource.aws_vpc.main", "type": "resource", "kind": "aws_vpc",
                 "name": "main", "file": "my-repo/main.tf", "line": 1, "labels": {}, "community_id": None},
            ],
            edges=[],
        )

        merged, _ = federate([graph_path])
        attrs = merged.nodes["resource.aws_vpc.main"]
        # source_repos may be absent or have at most one entry
        source_repos = attrs.get("source_repos") or []
        assert len(source_repos) <= 1


# ── Base name helper unit tests ───────────────────────────────────────────────


class TestBaseName:
    @pytest.mark.parametrize("node_id,expected", [
        # Slash-separated: last segment, then prefix stripped
        ("helm_chart/nginx", "nginx"),
        ("helm_chart/org-platform-myapp", "myapp"),
        ("helm_chart/org-myapp", "myapp"),
        ("helm_chart/prod-myapp", "myapp"),
        ("Deployment/default/my-service", "my-service"),
        # No slash: whole string becomes the segment; no prefix matches
        ("resource.aws_vpc.main", "resource.aws_vpc.main"),
    ])
    def test_base_name(self, node_id, expected):
        assert _base_name(node_id) == expected


# ── ArgoCD cluster Secret: server_url extraction ──────────────────────────────


ARGOCD_CLUSTER_SECRET_YAML = textwrap.dedent("""\
    apiVersion: v1
    kind: Secret
    metadata:
      name: my-cluster-secret
      namespace: argocd
      labels:
        argocd.argoproj.io/secret-type: cluster
    type: Opaque
    stringData:
      name: my-aks-cluster
      server: https://my-cluster.eastus.azmk8s.io:443
      config: |
        {"tlsClientConfig": {"insecure": false}}
""")

REGULAR_SECRET_YAML = textwrap.dedent("""\
    apiVersion: v1
    kind: Secret
    metadata:
      name: plain-secret
      namespace: default
    type: Opaque
    stringData:
      password: hunter2
""")


class TestArgoCDClusterSecretServerUrl:
    @pytest.fixture()
    def cluster_result(self, tmp_path):
        yaml_path = tmp_path / "cluster_secret.yaml"
        yaml_path.write_text(ARGOCD_CLUSTER_SECRET_YAML, encoding="utf-8")
        parser = KubernetesParser()
        result = parser.parse_file(yaml_path)
        return result, parser

    def test_argocd_cluster_secret_server_url(self, cluster_result):
        """Parse the secret; verify node has server_url correctly set."""
        result, _ = cluster_result
        nodes_by_id = {n["id"]: n for n in result["nodes"]}
        secret_node = nodes_by_id.get("Secret/argocd/my-cluster-secret")
        assert secret_node is not None
        assert secret_node.get("server_url") == "https://my-cluster.eastus.azmk8s.io:443"

    def test_argocd_cluster_secret_name(self, cluster_result):
        """Verify argocd_cluster_name attr is extracted from stringData.name."""
        result, _ = cluster_result
        nodes_by_id = {n["id"]: n for n in result["nodes"]}
        secret_node = nodes_by_id.get("Secret/argocd/my-cluster-secret")
        assert secret_node is not None
        assert secret_node.get("argocd_cluster_name") == "my-aks-cluster"

    def test_argocd_cluster_secret_from_fixture_file(self):
        """Parse the fixture YAML file — verify server_url extracted."""
        parser = KubernetesParser()
        result = parser.parse_file(FIXTURES / "argocd_cluster_secret.yaml")
        nodes_by_id = {n["id"]: n for n in result["nodes"]}
        secret_node = nodes_by_id.get("Secret/argocd/my-cluster-secret")
        assert secret_node is not None
        assert secret_node.get("server_url") == "https://my-cluster.eastus.azmk8s.io:443"


class TestNonClusterSecretNoServerUrl:
    def test_non_cluster_secret_no_server_url(self, tmp_path):
        """A regular Secret (no cluster label) must not have server_url set."""
        yaml_path = tmp_path / "plain_secret.yaml"
        yaml_path.write_text(REGULAR_SECRET_YAML, encoding="utf-8")
        parser = KubernetesParser()
        result = parser.parse_file(yaml_path)
        nodes_by_id = {n["id"]: n for n in result["nodes"]}
        secret_node = nodes_by_id.get("Secret/default/plain-secret")
        assert secret_node is not None
        assert secret_node.get("server_url") is None

    def test_non_cluster_secret_no_argocd_cluster_name(self, tmp_path):
        """A regular Secret must not have argocd_cluster_name set."""
        yaml_path = tmp_path / "plain_secret.yaml"
        yaml_path.write_text(REGULAR_SECRET_YAML, encoding="utf-8")
        parser = KubernetesParser()
        result = parser.parse_file(yaml_path)
        nodes_by_id = {n["id"]: n for n in result["nodes"]}
        secret_node = nodes_by_id.get("Secret/default/plain-secret")
        assert secret_node is not None
        assert secret_node.get("argocd_cluster_name") is None

    def test_cluster_secret_from_existing_fixture(self):
        """Use the pre-existing cluster_secret fixture to verify server_url."""
        parser = KubernetesParser()
        result = parser.parse_file(FIXTURES / "cluster_secret.yaml")
        nodes_by_id = {n["id"]: n for n in result["nodes"]}
        secret_node = nodes_by_id.get("Secret/argocd/staging-cluster")
        assert secret_node is not None
        # cluster_secret.yaml has stringData.server: https://staging.example.com
        assert secret_node.get("server_url") == "https://staging.example.com"
