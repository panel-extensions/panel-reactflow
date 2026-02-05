"""Tests for the public API helpers."""

try:
    import networkx as nx
except ImportError:
    nx = None
import panel as pn
import pytest

from panel_reactflow import EdgeSpec, NodeSpec, ReactFlow

nx_available = pytest.mark.skipif(nx is None, reason="networkx is not installed")


def test_node_spec_roundtrip() -> None:
    node = NodeSpec(
        id="n1",
        position={"x": 1, "y": 2},
        type="panel",
        data={"label": "Node 1"},
        selected=True,
    )
    payload = node.to_dict()
    assert payload["id"] == "n1"
    assert payload["data"]["label"] == "Node 1"
    assert NodeSpec.from_dict(payload).to_dict() == payload


def test_edge_spec_roundtrip() -> None:
    edge = EdgeSpec(id="e1", source="n1", target="n2", data={"weight": 0.5})
    payload = edge.to_dict()
    assert payload["source"] == "n1"
    assert payload["data"]["weight"] == 0.5
    assert EdgeSpec.from_dict(payload).to_dict() == payload


def test_reactflow_add_remove() -> None:
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "type": "panel", "data": {}})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "type": "panel", "data": {}})
    flow.add_edge({"id": "e1", "source": "n1", "target": "n2", "data": {}})
    assert len(flow.nodes) == 2
    assert len(flow.edges) == 1
    flow.remove_node("n1")
    assert [node["id"] for node in flow.nodes] == ["n2"]
    assert flow.edges == []


def test_reactflow_add_node_with_view() -> None:
    flow = ReactFlow()
    events = []
    flow.on("node_added", events.append)
    view = pn.pane.Markdown("Hello")
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {"label": "Pane"}}, view=view)
    assert events[-1]["type"] == "node_added"


def test_view_idx_updates_on_remove_node() -> None:
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}}, view=pn.pane.Markdown("A"))
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}}, view=pn.pane.Markdown("B"))
    flow.add_node({"id": "n3", "position": {"x": 2, "y": 2}, "data": {}}, view=None)

    flow.remove_node("n1")

    remaining = {node["id"]: node for node in flow.nodes}
    assert remaining["n2"]["data"]["view_idx"] == 0
    assert remaining["n3"]["data"].get("view_idx") is None


def test_reactflow_events_and_selection() -> None:
    flow = ReactFlow()
    events = []
    flow.on("edge_added", events.append)
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}, "selected": True})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}, "selected": False})
    flow.add_edge({"source": "n1", "target": "n2", "data": {}})
    assert flow.selection["nodes"] == ["n1"]
    assert events[-1]["type"] == "edge_added"
    edge_id = flow.edges[0]["id"]
    flow.patch_edge_data(edge_id, {"weight": 0.25})
    assert flow.edges[0]["data"]["weight"] == 0.25


@nx_available
def test_reactflow_to_networkx() -> None:
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}, "selected": True})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}, "selected": False})
    flow.add_edge({"source": "n1", "target": "n2", "data": {}})
    graph = flow.to_networkx()
    assert list(graph.nodes) == ["n1", "n2"]
    assert list(graph.edges) == [("n1", "n2")]


@nx_available
def test_reactflow_from_networkx() -> None:
    graph = nx.DiGraph()
    graph.add_node("n1", position={"x": 0, "y": 0}, data={})
    graph.add_node("n2", position={"x": 1, "y": 1}, data={})
    graph.add_edge("n1", "n2", data={})
    flow = ReactFlow.from_networkx(graph)
    assert flow.nodes == [
        {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}, "type": "panel"},
        {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}, "type": "panel"},
    ]
    assert flow.edges == [{"id": "n1->n2", "source": "n1", "target": "n2", "data": {}}]
