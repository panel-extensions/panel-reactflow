"""Tests for the public API helpers."""

import panel as pn

from panel_reactflow import (
    EdgeSpec,
    EdgeTypeSpec,
    NodeSpec,
    NodeTypeSpec,
    PropertySpec,
    ReactFlow,
)


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


def test_schema_helpers_to_dict() -> None:
    prop = PropertySpec(name="weight", type="float", default=1.0, visible_in_node=True)
    node_spec = NodeTypeSpec(type="panel", label="Panel", properties=[prop], inputs=["in"], outputs=["out"])
    edge_spec = EdgeTypeSpec(type="default", properties=[prop])
    assert node_spec.to_dict()["properties"][0]["name"] == "weight"
    assert edge_spec.to_dict()["properties"][0]["type"] == "float"


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
    assert flow.nodes[0]["data"]["view_idx"] == 0
    assert len(flow._views) == 1
    assert events[-1]["type"] == "node_added"


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
