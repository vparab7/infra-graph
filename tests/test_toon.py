"""Tests for the TOON (Token-Oriented Object Notation) serializer/deserializer."""

from __future__ import annotations

import networkx as nx
import pytest

from infra_graph.graph.toon import dumps_graph, loads_graph


class TestRoundTripEmpty:
    def test_round_trip_empty_graph(self):
        """Empty graph serializes and deserializes correctly."""
        g = nx.DiGraph()
        text = dumps_graph(g)
        g2, meta = loads_graph(text)
        assert g2.number_of_nodes() == 0
        assert g2.number_of_edges() == 0
        assert meta == {}


class TestRoundTripNodesAndEdges:
    def _make_graph(self):
        g = nx.DiGraph()
        g.add_node(
            "resource.aws_vpc.main",
            type="resource",
            kind="aws_vpc",
            name="main",
            file="/repo/main.tf",
            line=10,
            namespace=None,
            community_id=0,
            server_url=None,
            labels={"env": "prod"},
            expression=None,
        )
        g.add_node(
            "resource.aws_subnet.public",
            type="resource",
            kind="aws_subnet",
            name="public",
            file="/repo/main.tf",
            line=20,
            namespace=None,
            community_id=0,
            server_url=None,
            labels={},
            expression=None,
        )
        g.add_edge(
            "resource.aws_vpc.main",
            "resource.aws_subnet.public",
            type="references",
            confidence=1.0,
            provenance="EXTRACTED",
        )
        return g

    def test_round_trip_nodes_and_edges(self):
        """Graph with nodes and edges: all fields preserved after round-trip."""
        g = self._make_graph()
        text = dumps_graph(g)
        g2, _ = loads_graph(text)

        assert set(g2.nodes()) == set(g.nodes())
        assert set(g2.edges()) == set(g.edges())

    def test_node_type_preserved(self):
        g = self._make_graph()
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        attrs = g2.nodes["resource.aws_vpc.main"]
        assert attrs["type"] == "resource"
        assert attrs["kind"] == "aws_vpc"
        assert attrs["name"] == "main"

    def test_edge_type_preserved(self):
        g = self._make_graph()
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        eattrs = g2.edges["resource.aws_vpc.main", "resource.aws_subnet.public"]
        assert eattrs["type"] == "references"
        assert eattrs["provenance"] == "EXTRACTED"


class TestLabels:
    def test_labels_dict_preserved(self):
        """Labels dict with multiple keys survives round-trip."""
        g = nx.DiGraph()
        g.add_node(
            "Service/default/myapp",
            type="Service",
            kind="Service",
            name="myapp",
            file="/k8s/svc.yaml",
            line=1,
            namespace="default",
            community_id=None,
            server_url=None,
            labels={"app": "myapp", "env": "staging", "team": "platform"},
            expression=None,
        )
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        labels = g2.nodes["Service/default/myapp"]["labels"]
        assert labels == {"app": "myapp", "env": "staging", "team": "platform"}

    def test_empty_labels_preserved(self):
        """Empty labels come back as empty dict."""
        g = nx.DiGraph()
        g.add_node("n1", type="x", kind="x", name="n1", file=None, line=None,
                   namespace=None, community_id=None, server_url=None, labels={}, expression=None)
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        assert g2.nodes["n1"]["labels"] == {}


class TestNullValues:
    def test_null_values_preserved(self):
        """None fields come back as None after round-trip."""
        g = nx.DiGraph()
        g.add_node(
            "Deployment/default/app",
            type="Deployment",
            kind="Deployment",
            name="app",
            file=None,
            line=None,
            namespace=None,
            community_id=None,
            server_url=None,
            labels={},
            expression=None,
        )
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        attrs = g2.nodes["Deployment/default/app"]
        assert attrs["file"] is None
        assert attrs["line"] is None
        assert attrs["namespace"] is None
        assert attrs["server_url"] is None


class TestNumericLine:
    def test_numeric_line_preserved(self):
        """Integer line numbers are preserved (not converted to string)."""
        g = nx.DiGraph()
        g.add_node(
            "resource.aws_s3_bucket.my_bucket",
            type="resource",
            kind="aws_s3_bucket",
            name="my_bucket",
            file="/repo/s3.tf",
            line=42,
            namespace=None,
            community_id=None,
            server_url=None,
            labels={},
            expression=None,
        )
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        assert g2.nodes["resource.aws_s3_bucket.my_bucket"]["line"] == 42
        assert isinstance(g2.nodes["resource.aws_s3_bucket.my_bucket"]["line"], int)


class TestMeta:
    def test_meta_round_trip(self):
        """Meta dict is preserved through dumps/loads."""
        g = nx.DiGraph()
        meta = {"repo": "my-repo", "version": "1.2.3", "branch": "main"}
        text = dumps_graph(g, meta=meta)
        _, meta2 = loads_graph(text)
        assert meta2["repo"] == "my-repo"
        assert meta2["version"] == "1.2.3"
        assert meta2["branch"] == "main"

    def test_meta_empty_when_none(self):
        """No meta section → empty dict returned."""
        g = nx.DiGraph()
        text = dumps_graph(g, meta=None)
        _, meta = loads_graph(text)
        assert meta == {}


class TestSpecialValues:
    def test_commas_in_file_path(self):
        """File path containing a comma is quoted then unquoted correctly."""
        g = nx.DiGraph()
        path_with_comma = "/repo/dir,with,commas/main.tf"
        g.add_node(
            "resource.aws_vpc.main",
            type="resource",
            kind="aws_vpc",
            name="main",
            file=path_with_comma,
            line=1,
            namespace=None,
            community_id=None,
            server_url=None,
            labels={},
            expression=None,
        )
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        assert g2.nodes["resource.aws_vpc.main"]["file"] == path_with_comma

    def test_special_chars_in_name(self):
        """Node name with slashes and colons round-trips correctly."""
        nid = "helm_chart/org-platform-myapp"
        g = nx.DiGraph()
        g.add_node(
            nid,
            type="helm_chart",
            kind="helm_chart",
            name="org-platform-myapp",
            file=None,
            line=None,
            namespace=None,
            community_id=None,
            server_url=None,
            labels={},
            expression=None,
        )
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        assert nid in g2.nodes()

    def test_node_id_with_colons(self):
        """Node id containing colons round-trips correctly."""
        nid = "Secret/argocd/my:cluster:secret"
        g = nx.DiGraph()
        g.add_node(
            nid,
            type="Secret",
            kind="Secret",
            name="my:cluster:secret",
            file=None,
            line=None,
            namespace="argocd",
            community_id=None,
            server_url=None,
            labels={},
            expression=None,
        )
        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        assert nid in g2.nodes()


class TestEdgeConfidence:
    def test_edge_confidence_float(self):
        """confidence=0.7 is preserved as a float after round-trip."""
        g = nx.DiGraph()
        g.add_node("a", type="x", kind="x", name="a", file=None, line=None,
                   namespace=None, community_id=None, server_url=None, labels={}, expression=None)
        g.add_node("b", type="x", kind="x", name="b", file=None, line=None,
                   namespace=None, community_id=None, server_url=None, labels={}, expression=None)
        g.add_edge("a", "b", type="uses", confidence=0.7, provenance="INFERRED")

        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        eattrs = g2.edges["a", "b"]
        assert eattrs["confidence"] == pytest.approx(0.7)
        assert isinstance(eattrs["confidence"], float)

    def test_edge_confidence_one(self):
        """confidence=1.0 is preserved as a float."""
        g = nx.DiGraph()
        g.add_node("a", type="x", kind="x", name="a", file=None, line=None,
                   namespace=None, community_id=None, server_url=None, labels={}, expression=None)
        g.add_node("b", type="x", kind="x", name="b", file=None, line=None,
                   namespace=None, community_id=None, server_url=None, labels={}, expression=None)
        g.add_edge("a", "b", type="depends_on", confidence=1.0, provenance="EXTRACTED")

        text = dumps_graph(g)
        g2, _ = loads_graph(text)
        eattrs = g2.edges["a", "b"]
        assert eattrs["confidence"] == pytest.approx(1.0)
