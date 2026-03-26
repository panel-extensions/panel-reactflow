"""Tests for the public API helpers."""

try:
    import networkx as nx
except ImportError:
    nx = None
import panel as pn
import param
import pytest

from panel_reactflow import Edge, EdgeSpec, EdgeType, Node, NodeSpec, NodeType, ReactFlow, SchemaSource
from panel_reactflow.base import (
    Editor,
    JsonEditor,
    SchemaEditor,
    _normalize_schema,
    _param_to_jsonschema,
)

nx_available = pytest.mark.skipif(nx is None, reason="networkx is not installed")


def test_node_spec_roundtrip() -> None:
    node = NodeSpec(
        id="n1",
        position={"x": 1, "y": 2},
        type="panel",
        label="Node 1",
        data={},
        selected=True,
    )
    payload = node.to_dict()
    assert payload["id"] == "n1"
    assert payload["label"] == "Node 1"
    assert NodeSpec.from_dict(payload).to_dict() == payload


def test_node_spec_with_view() -> None:
    view = pn.pane.Markdown("Hello World")
    node = NodeSpec(
        id="n1",
        position={"x": 1, "y": 2},
        label="Node 1",
        view=view,
    )
    payload = node.to_dict()
    assert payload["id"] == "n1"
    assert payload["label"] == "Node 1"
    assert payload["view"] is view
    # Test roundtrip with view
    node_from_dict = NodeSpec.from_dict(payload)
    assert node_from_dict.view is view
    assert node_from_dict.to_dict() == payload


def test_node_spec_without_view() -> None:
    """Test that view is not included in dict when None."""
    node = NodeSpec(
        id="n1",
        position={"x": 1, "y": 2},
        label="Node 1",
    )
    payload = node.to_dict()
    assert "view" not in payload


def test_reactflow_add_node_with_nodespec_view() -> None:
    """Test that NodeSpec view is preserved when passed to add_node."""
    flow = ReactFlow()
    view = pn.pane.Markdown("Hello")
    node = NodeSpec(id="n1", position={"x": 0, "y": 0}, label="Node", view=view)
    flow.add_node(node)
    assert len(flow.nodes) == 1
    assert flow.nodes[0]["view"] is view


class _CountingNode(Node):
    def __init__(self, **params):
        super().__init__(**params)
        self.events = []

    def on_event(self, payload, flow):
        self.events.append(("event", payload["type"]))

    def on_move(self, payload, flow):
        self.events.append(("move", payload["position"]))

    def on_delete(self, payload, flow):
        self.events.append(("delete", payload["node_id"]))


class _ParameterizedNode(Node):
    threshold = param.Number(default=0.5, precedence=0)
    hidden = param.String(default="secret", precedence=-1)


def test_reactflow_accepts_node_instance() -> None:
    flow = ReactFlow()
    node = Node(id="n1", position={"x": 0, "y": 0}, label="Node object", data={"status": "idle"})
    flow.add_node(node)
    assert flow.nodes[0] is node
    assert node.flow is flow
    assert flow.nodes[0].data["status"] == "idle"


def test_patch_node_data_updates_node_instance() -> None:
    node = Node(id="n1", position={"x": 0, "y": 0}, data={"value": 1})
    flow = ReactFlow(nodes=[node])
    flow.patch_node_data("n1", {"value": 42, "name": "patched"})
    assert node.data["value"] == 42
    assert node.data["name"] == "patched"


def test_sync_updates_node_instance_fields() -> None:
    node = Node(id="n1", position={"x": 0, "y": 0}, data={"value": 1}, selected=False)
    flow = ReactFlow(nodes=[node])
    flow._handle_msg(
        {
            "type": "sync",
            "nodes": [
                {
                    "id": "n1",
                    "type": "panel",
                    "position": {"x": 10, "y": 20},
                    "data": {"value": 3},
                    "selected": True,
                    "draggable": False,
                    "connectable": False,
                    "deletable": False,
                }
            ],
        }
    )
    assert flow.nodes[0] is node
    assert node.position == {"x": 10, "y": 20}
    assert node.data == {"value": 3}
    assert node.selected is True
    assert node.draggable is False
    assert node.connectable is False
    assert node.deletable is False


def test_node_hooks_receive_events() -> None:
    node = _CountingNode(id="n1", position={"x": 0, "y": 0}, data={})
    flow = ReactFlow(nodes=[node])
    flow._handle_msg({"type": "node_moved", "node_id": "n1", "position": {"x": 5, "y": 9}})
    flow.remove_node("n1")
    assert ("move", {"x": 5, "y": 9}) in node.events
    assert ("event", "node_moved") in node.events
    assert ("delete", "n1") in node.events
    assert ("event", "node_deleted") in node.events
    assert node.flow is None


def test_node_can_provide_custom_editor() -> None:
    class _NodeWithEditor(Node):
        def editor(self, data, schema, *, id, type, on_patch):
            return pn.pane.Markdown(f"Editor for {id}")

    node = _NodeWithEditor(id="n1", position={"x": 0, "y": 0}, data={})
    flow = ReactFlow(nodes=[node])
    editor = flow._node_editors["n1"]
    assert hasattr(editor, "object")
    assert "n1" in editor.object


def test_node_subclass_params_with_non_negative_precedence_in_data_and_schema() -> None:
    node = _ParameterizedNode(id="n1", type="custom", position={"x": 0, "y": 0}, data={})
    flow = ReactFlow(nodes=[node])
    payload = node.to_dict()
    assert payload["data"]["threshold"] == 0.5
    assert "hidden" not in payload["data"]
    schema = flow._get_node_schema("custom")
    assert schema is not None
    assert "threshold" in schema["properties"]
    assert "hidden" not in schema["properties"]


def test_patch_node_data_updates_parameterized_node_params() -> None:
    node = _ParameterizedNode(id="n1", type="custom", position={"x": 0, "y": 0}, data={})
    flow = ReactFlow(nodes=[node])
    flow.patch_node_data("n1", {"threshold": 0.9, "hidden": "still-hidden"})
    assert node.threshold == 0.9
    assert node.hidden == "secret"
    assert node.data["threshold"] == 0.9
    assert node.data["hidden"] == "still-hidden"


def test_parameterized_node_param_change_auto_patches_data() -> None:
    node = _ParameterizedNode(id="n1", type="custom", position={"x": 0, "y": 0}, data={})
    flow = ReactFlow(nodes=[node])
    events = []
    flow.on("node_data_changed", events.append)
    node.threshold = 0.77
    assert node.data["threshold"] == 0.77
    assert events[-1]["patch"] == {"threshold": 0.77}


def test_parameterized_node_watchers_clean_up_on_delete() -> None:
    node = _ParameterizedNode(id="n1", type="custom", position={"x": 0, "y": 0}, data={})
    flow = ReactFlow(nodes=[node])
    assert "n1" in flow._node_data_param_watchers
    flow.remove_node("n1")
    assert "n1" not in flow._node_data_param_watchers
    assert node.flow is None
    events = []
    flow.on("node_data_changed", events.append)
    node.threshold = 0.31
    assert events == []


def test_node_flow_ref_updates_on_nodes_assignment() -> None:
    flow = ReactFlow()
    node = Node(id="n1", position={"x": 0, "y": 0}, data={})
    flow.nodes = [node]
    assert node.flow is flow
    flow.nodes = []
    assert node.flow is None


def test_edge_spec_roundtrip() -> None:
    edge = EdgeSpec(id="e1", source="n1", target="n2", data={"weight": 0.5})
    payload = edge.to_dict()
    assert payload["source"] == "n1"
    assert payload["data"]["weight"] == 0.5
    assert EdgeSpec.from_dict(payload).to_dict() == payload


class _CountingEdge(Edge):
    def __init__(self, **params):
        super().__init__(**params)
        self.events = []

    def on_event(self, payload, flow):
        self.events.append(("event", payload["type"]))

    def on_delete(self, payload, flow):
        self.events.append(("delete", payload["edge_id"]))


class _ParameterizedEdge(Edge):
    confidence = param.Number(default=0.8, precedence=0)
    internal = param.String(default="ignore", precedence=-1)


def test_reactflow_accepts_edge_instance() -> None:
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}})
    edge = Edge(id="e1", source="n1", target="n2", data={"weight": 1})
    flow.add_edge(edge)
    assert flow.edges[0] is edge
    assert edge.flow is flow
    assert flow.edges[0].data["weight"] == 1


def test_patch_edge_data_updates_edge_instance() -> None:
    edge = Edge(id="e1", source="n1", target="n2", data={"weight": 1})
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[edge],
    )
    flow.patch_edge_data("e1", {"weight": 3, "label": "hi"})
    assert edge.data["weight"] == 3
    assert edge.data["label"] == "hi"


def test_sync_updates_edge_instance_fields() -> None:
    edge = Edge(id="e1", source="n1", target="n2", data={"weight": 1}, selected=False)
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[edge],
    )
    flow._handle_msg(
        {
            "type": "sync",
            "edges": [
                {
                    "id": "e1",
                    "source": "n1",
                    "target": "n2",
                    "label": "patched",
                    "type": "flow",
                    "selected": True,
                    "data": {"weight": 7},
                    "sourceHandle": "out",
                    "targetHandle": "in",
                }
            ],
        }
    )
    assert flow.edges[0] is edge
    assert edge.label == "patched"
    assert edge.type == "flow"
    assert edge.selected is True
    assert edge.data == {"weight": 7}
    assert edge.sourceHandle == "out"
    assert edge.targetHandle == "in"


def test_edge_hooks_receive_events() -> None:
    edge = _CountingEdge(id="e1", source="n1", target="n2", data={})
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[edge],
    )
    flow.patch_edge_data("e1", {"weight": 2})
    flow.remove_edge("e1")
    assert ("event", "edge_data_changed") in edge.events
    assert ("delete", "e1") in edge.events
    assert ("event", "edge_deleted") in edge.events
    assert edge.flow is None


def test_edge_can_provide_custom_editor() -> None:
    class _EdgeWithEditor(Edge):
        def editor(self, data, schema, *, id, type, on_patch):
            return pn.pane.Markdown(f"Edge editor for {id}")

    edge = _EdgeWithEditor(id="e1", source="n1", target="n2", data={})
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[edge],
    )
    editor = flow._edge_editors["e1"]
    assert hasattr(editor, "object")
    assert "e1" in editor.object


def test_edge_subclass_params_with_non_negative_precedence_in_data_and_schema() -> None:
    edge = _ParameterizedEdge(id="e1", source="n1", target="n2", type="weighted", data={})
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[edge],
    )
    payload = edge.to_dict()
    assert payload["data"]["confidence"] == 0.8
    assert "internal" not in payload["data"]
    schema = flow._get_edge_schema("weighted")
    assert schema is not None
    assert "confidence" in schema["properties"]
    assert "internal" not in schema["properties"]


def test_patch_edge_data_updates_parameterized_edge_params() -> None:
    edge = _ParameterizedEdge(id="e1", source="n1", target="n2", type="weighted", data={})
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[edge],
    )
    flow.patch_edge_data("e1", {"confidence": 0.25, "internal": "keep-data-only"})
    assert edge.confidence == 0.25
    assert edge.internal == "ignore"
    assert edge.data["confidence"] == 0.25
    assert edge.data["internal"] == "keep-data-only"


def test_parameterized_edge_param_change_auto_patches_data() -> None:
    edge = _ParameterizedEdge(id="e1", source="n1", target="n2", type="weighted", data={})
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[edge],
    )
    events = []
    flow.on("edge_data_changed", events.append)
    edge.confidence = 0.41
    assert edge.data["confidence"] == 0.41
    assert events[-1]["patch"] == {"confidence": 0.41}


def test_parameterized_edge_watchers_clean_up_on_delete() -> None:
    edge = _ParameterizedEdge(id="e1", source="n1", target="n2", type="weighted", data={})
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[edge],
    )
    assert "e1" in flow._edge_data_param_watchers
    flow.remove_edge("e1")
    assert "e1" not in flow._edge_data_param_watchers
    assert edge.flow is None
    events = []
    flow.on("edge_data_changed", events.append)
    edge.confidence = 0.2
    assert events == []


def test_edge_flow_ref_updates_on_edges_assignment() -> None:
    edge = Edge(id="e1", source="n1", target="n2", data={})
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ]
    )
    flow.edges = [edge]
    assert edge.flow is flow
    flow.edges = []
    assert edge.flow is None


def test_edge_spec_with_handles() -> None:
    """Test that EdgeSpec correctly handles sourceHandle and targetHandle."""
    edge = EdgeSpec(id="e1", source="producer", target="consumer", sourceHandle="result", targetHandle="mode")
    payload = edge.to_dict()
    assert payload["source"] == "producer"
    assert payload["target"] == "consumer"
    assert payload["sourceHandle"] == "result"
    assert payload["targetHandle"] == "mode"
    # Test roundtrip
    assert EdgeSpec.from_dict(payload).to_dict() == payload


def test_edge_spec_without_handles() -> None:
    """Test that EdgeSpec works without handles (backward compatibility)."""
    edge = EdgeSpec(id="e1", source="n1", target="n2")
    payload = edge.to_dict()
    # When handles are None, they should not be in the payload
    assert "sourceHandle" not in payload
    assert "targetHandle" not in payload
    # Test roundtrip
    edge2 = EdgeSpec.from_dict(payload)
    assert edge2.sourceHandle is None
    assert edge2.targetHandle is None


def test_edge_spec_with_handles_via_add_edge() -> None:
    """Test that sourceHandle/targetHandle survive add_edge via ReactFlow."""
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}})
    flow.add_edge(
        EdgeSpec(
            id="e1",
            source="n1",
            target="n2",
            sourceHandle="out1",
            targetHandle="in1",
        )
    )
    edge = flow.edges[0]
    assert edge["sourceHandle"] == "out1"
    assert edge["targetHandle"] == "in1"


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
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "label": "Pane", "data": {}, "view": view})
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


def test_handle_msg_edge_added_emits_once() -> None:
    """Frontend connect sends edge_added; Python must not double-emit (add_edge already emits)."""
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[],
    )
    events: list[dict] = []
    flow.on("edge_added", events.append)
    flow._handle_msg(
        {
            "type": "edge_added",
            "edge": {"id": "n1->n2", "source": "n1", "target": "n2"},
        },
    )
    assert len(events) == 1
    assert events[0]["type"] == "edge_added"
    assert events[0]["edge"]["id"] == "n1->n2"
    assert len(flow.edges) == 1


def test_handle_msg_node_moved_emits_once() -> None:
    flow = ReactFlow(nodes=[{"id": "n1", "position": {"x": 0, "y": 0}, "data": {}}])
    events: list[dict] = []
    flow.on("node_moved", events.append)
    flow._handle_msg({"type": "node_moved", "node_id": "n1", "position": {"x": 10, "y": 20}})
    assert len(events) == 1
    assert events[0]["type"] == "node_moved"
    assert events[0]["node_id"] == "n1"
    assert events[0]["position"] == {"x": 10, "y": 20}
    assert flow.nodes[0]["position"] == {"x": 10, "y": 20}


def test_handle_msg_selection_changed_emits_once() -> None:
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[{"id": "e1", "source": "n1", "target": "n2", "data": {}}],
    )
    events: list[dict] = []
    flow.on("selection_changed", events.append)
    flow._handle_msg({"type": "selection_changed", "nodes": ["n1"], "edges": ["e1"]})
    assert len(events) == 1
    assert events[0]["type"] == "selection_changed"
    assert flow.selection == {"nodes": ["n1"], "edges": ["e1"]}


def test_handle_msg_node_clicked_emits_once() -> None:
    flow = ReactFlow(nodes=[{"id": "n1", "position": {"x": 0, "y": 0}, "data": {}}])
    events: list[dict] = []
    flow.on("node_clicked", events.append)
    flow._handle_msg({"type": "node_clicked", "node_id": "n1", "button": 0})
    assert len(events) == 1
    assert events[0]["type"] == "node_clicked"
    assert events[0]["node_id"] == "n1"


def test_handle_msg_sync_emits_once() -> None:
    flow = ReactFlow()
    events: list[dict] = []
    flow.on("sync", events.append)
    msg = {
        "type": "sync",
        "nodes": [
            {
                "id": "n1",
                "position": {"x": 0, "y": 0},
                "type": "panel",
                "data": {},
                "selected": False,
            }
        ],
        "edges": [],
    }
    flow._handle_msg(msg)
    assert len(events) == 1
    assert events[0]["type"] == "sync"
    assert [n["id"] for n in flow.nodes] == ["n1"]


def test_handle_msg_node_deleted_emits_once_for_single_node() -> None:
    """remove_node already emits; _handle_msg must not emit a duplicate batch message."""
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[{"id": "e1", "source": "n1", "target": "n2", "data": {}}],
    )
    events: list[dict] = []
    flow.on("node_deleted", events.append)
    flow._handle_msg(
        {
            "type": "node_deleted",
            "node_id": "n1",
            "node_ids": ["n1"],
            "deleted_edges": ["e1"],
        },
    )
    assert len(events) == 1
    assert events[0]["type"] == "node_deleted"
    assert events[0]["node_id"] == "n1"
    assert events[0]["deleted_edges"] == ["e1"]
    assert [n["id"] for n in flow.nodes] == ["n2"]
    assert flow.edges == []


def test_handle_msg_node_deleted_one_event_per_node_when_batch() -> None:
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[],
    )
    events: list[dict] = []
    flow.on("node_deleted", events.append)
    flow._handle_msg(
        {
            "type": "node_deleted",
            "node_id": None,
            "node_ids": ["n1", "n2"],
            "deleted_edges": [],
        },
    )
    assert len(events) == 2
    assert {e["node_id"] for e in events} == {"n1", "n2"}
    assert flow.nodes == []


def test_handle_msg_edge_deleted_emits_once() -> None:
    """remove_edge already emits; _handle_msg must not emit a duplicate batch message."""
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[{"id": "e1", "source": "n1", "target": "n2", "data": {}}],
    )
    events: list[dict] = []
    flow.on("edge_deleted", events.append)
    flow._handle_msg({"type": "edge_deleted", "edge_id": "e1", "edge_ids": ["e1"]})
    assert len(events) == 1
    assert events[0]["type"] == "edge_deleted"
    assert events[0]["edge_id"] == "e1"
    assert flow.edges == []


def test_handle_msg_edge_deleted_one_event_per_edge_when_batch() -> None:
    flow = ReactFlow(
        nodes=[
            {"id": "n1", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "n2", "position": {"x": 1, "y": 1}, "data": {}},
        ],
        edges=[
            {"id": "e1", "source": "n1", "target": "n2", "data": {}},
            {"id": "e2", "source": "n2", "target": "n1", "data": {}},
        ],
    )
    events: list[dict] = []
    flow.on("edge_deleted", events.append)
    flow._handle_msg({"type": "edge_deleted", "edge_id": None, "edge_ids": ["e1", "e2"]})
    assert len(events) == 2
    assert {e["edge_id"] for e in events} == {"e1", "e2"}
    assert flow.edges == []


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


@nx_available
def test_networkx_roundtrip_with_handles() -> None:
    """Test that sourceHandle/targetHandle survive a NetworkX roundtrip."""
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}})
    flow.add_edge(
        EdgeSpec(
            id="e1",
            source="n1",
            target="n2",
            sourceHandle="out",
            targetHandle="in",
        )
    )
    graph = flow.to_networkx()
    # Handles stored as edge attributes
    assert graph["n1"]["n2"]["sourceHandle"] == "out"
    assert graph["n1"]["n2"]["targetHandle"] == "in"
    # Roundtrip back
    flow2 = ReactFlow.from_networkx(graph)
    edge = flow2.edges[0]
    assert edge["sourceHandle"] == "out"
    assert edge["targetHandle"] == "in"
    # Handles should not leak into data
    assert "sourceHandle" not in edge["data"]
    assert "targetHandle" not in edge["data"]


# --- NodeType / EdgeType / SchemaSource tests ---


def test_node_type_to_dict_no_schema() -> None:
    nt = NodeType(type="task", label="Task Node", inputs=["in"], outputs=["out"])
    d = nt.to_dict()
    assert d["type"] == "task"
    assert d["label"] == "Task Node"
    assert d["schema"] is None
    assert d["inputs"] == ["in"]
    assert d["outputs"] == ["out"]
    assert d["pane_policy"] == "single"


def test_node_type_to_dict_json_schema() -> None:
    js = {"type": "object", "properties": {"name": {"type": "string"}}}
    nt = NodeType(type="task", schema=js)
    d = nt.to_dict()
    assert d["schema"] == js


def test_node_type_to_dict_param_schema() -> None:
    class MyParam(param.Parameterized):
        label = param.String(default="hello")
        count = param.Integer(default=0)

    nt = NodeType(type="task", schema=MyParam)
    d = nt.to_dict()
    assert d["schema"]["type"] == "object"
    assert "label" in d["schema"]["properties"]
    assert d["schema"]["properties"]["label"]["type"] == "string"
    assert d["schema"]["properties"]["count"]["type"] == "integer"


def test_node_type_to_dict_schema_source() -> None:
    js = {"type": "object", "properties": {"x": {"type": "number"}}}
    nt = NodeType(type="task", schema=SchemaSource(kind="jsonschema", value=js))
    d = nt.to_dict()
    assert d["schema"] == js


def test_edge_type_to_dict() -> None:
    et = EdgeType(type="flow", label="Flow Edge")
    d = et.to_dict()
    assert d["type"] == "flow"
    assert d["label"] == "Flow Edge"
    assert d["schema"] is None


def test_param_to_jsonschema_skips_base_and_private() -> None:
    class MyParam(param.Parameterized):
        label = param.String()
        _internal = param.Integer()

    schema = _param_to_jsonschema(MyParam)
    assert "label" in schema["properties"]
    assert "_internal" not in schema["properties"]
    assert "name" not in schema["properties"]


def test_param_to_jsonschema_selector() -> None:
    class MyParam(param.Parameterized):
        status = param.Selector(objects=["a", "b", "c"])

    schema = _param_to_jsonschema(MyParam)
    prop = schema["properties"]["status"]
    assert prop["enum"] == ["a", "b", "c"]


def test_normalize_schema_none() -> None:
    assert _normalize_schema(None) is None


def test_normalize_schema_dict_passthrough() -> None:
    js = {"type": "object"}
    assert _normalize_schema(js) is js


def test_normalize_schema_invalid_raises() -> None:
    with pytest.raises(ValueError, match="Cannot normalize schema"):
        _normalize_schema(42)


# --- Editor resolution tests ---


def test_editor_resolution_default_schema_editor() -> None:
    """Without node_editors, the fallback is SchemaEditor."""
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {"x": 1}})
    assert isinstance(flow._node_editors["n1"], SchemaEditor)


def test_editor_resolution_explicit_json_editor() -> None:
    flow = ReactFlow(node_editors={"panel": JsonEditor})
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {"x": 1}})
    assert isinstance(flow._node_editors["n1"], JsonEditor)


def test_editor_resolution_custom_editor_class() -> None:
    class MyEditor(Editor):
        def __panel__(self):
            return pn.pane.Markdown(f"Custom editor for {self._node_id}")

    flow = ReactFlow(node_editors={"panel": MyEditor})
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {"value": "hi"}})
    assert isinstance(flow._node_editors["n1"], MyEditor)


def test_editor_resolution_callable_editor() -> None:
    def my_editor(data, schema, *, id, type, on_patch):
        return pn.pane.Markdown(f"Editor for {id}")

    flow = ReactFlow(node_editors={"panel": my_editor})
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}})
    editor = flow._node_editors["n1"]
    assert hasattr(editor, "object")
    assert "n1" in editor.object


def test_editor_resolution_default_node_editor() -> None:
    flow = ReactFlow(default_node_editor=JsonEditor)
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {"value": "hi"}})
    assert isinstance(flow._node_editors["n1"], JsonEditor)


# --- Edge editor resolution tests ---


def test_edge_editor_resolution_default() -> None:
    """Without edge_editors, the fallback is SchemaEditor."""
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}})
    flow.add_edge({"source": "n1", "target": "n2", "data": {"w": 1}})
    edge_id = flow.edges[0]["id"]
    assert isinstance(flow._edge_editors[edge_id], SchemaEditor)


def test_edge_editor_resolution_custom() -> None:
    def my_edge_editor(data, schema, *, id, type, on_patch):
        return pn.pane.Markdown(f"Edge {id}")

    flow = ReactFlow(edge_editors={"flow": my_edge_editor})
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}})
    flow.add_edge({"source": "n1", "target": "n2", "type": "flow", "data": {}})
    edge_id = flow.edges[0]["id"]
    editor = flow._edge_editors[edge_id]
    assert hasattr(editor, "object")
    assert edge_id in editor.object


def test_edge_editor_resolution_default_edge_editor() -> None:
    flow = ReactFlow(default_edge_editor=JsonEditor)
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}})
    flow.add_edge({"source": "n1", "target": "n2", "data": {}})
    edge_id = flow.edges[0]["id"]
    assert isinstance(flow._edge_editors[edge_id], JsonEditor)


def test_edge_editor_on_patch_calls_patch_edge_data() -> None:
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}})
    flow.add_edge({"source": "n1", "target": "n2", "data": {"w": 0}})
    edge_id = flow.edges[0]["id"]
    editor = flow._edge_editors[edge_id]
    # Simulate an on_patch call from the editor
    editor._on_patch({"w": 42})
    assert flow.edges[0]["data"]["w"] == 42


# --- node_types normalization tests ---


def test_node_types_normalized_on_init() -> None:
    nt = NodeType(type="task", label="Task", inputs=["in"], outputs=["out"])
    flow = ReactFlow(node_types={"task": nt})
    assert flow.node_types["task"]["type"] == "task"
    assert flow.node_types["task"]["label"] == "Task"
    assert flow.node_types["task"]["inputs"] == ["in"]
    assert flow.node_types["task"]["schema"] is None


def test_node_types_param_class_shorthand() -> None:
    """Passing a Param class directly as a node_types value
    should auto-wrap it into a NodeType with a normalized schema."""

    class MyParam(param.Parameterized):
        name_field = param.String(default="test")

    flow = ReactFlow(node_types={"task": MyParam})
    assert flow.node_types["task"]["type"] == "task"
    assert flow.node_types["task"]["schema"] is not None
    assert "name_field" in flow.node_types["task"]["schema"]["properties"]


def test_node_types_dict_passthrough() -> None:
    spec = {"type": "task", "label": "Task", "schema": None, "inputs": None, "outputs": None}
    flow = ReactFlow(node_types={"task": spec})
    assert flow.node_types["task"] == spec


def test_nodespec_autoserialize_on_init() -> None:
    """NodeSpec objects should be automatically converted to dicts on init."""
    node1 = NodeSpec(id="n1", position={"x": 0, "y": 0}, label="Node 1")
    node2 = NodeSpec(id="n2", position={"x": 100, "y": 50}, label="Node 2")
    flow = ReactFlow(nodes=[node1, node2])

    # Verify nodes are now dictionaries
    assert isinstance(flow.nodes[0], dict)
    assert isinstance(flow.nodes[1], dict)
    assert flow.nodes[0]["id"] == "n1"
    assert flow.nodes[0]["label"] == "Node 1"
    assert flow.nodes[1]["id"] == "n2"
    assert flow.nodes[1]["label"] == "Node 2"


def test_edgespec_autoserialize_on_init() -> None:
    """EdgeSpec objects should be automatically converted to dicts on init."""
    edge1 = EdgeSpec(id="e1", source="n1", target="n2", label="Edge 1")
    edge2 = EdgeSpec(id="e2", source="n2", target="n3")
    flow = ReactFlow(edges=[edge1, edge2])

    # Verify edges are now dictionaries
    assert isinstance(flow.edges[0], dict)
    assert isinstance(flow.edges[1], dict)
    assert flow.edges[0]["id"] == "e1"
    assert flow.edges[0]["source"] == "n1"
    assert flow.edges[0]["target"] == "n2"
    assert flow.edges[0]["label"] == "Edge 1"
    assert flow.edges[1]["id"] == "e2"


def test_nodespec_autoserialize_on_assignment() -> None:
    """NodeSpec objects should be automatically converted when assigned to nodes param."""
    flow = ReactFlow()
    node1 = NodeSpec(id="n1", position={"x": 0, "y": 0}, label="Node 1")
    node2 = NodeSpec(id="n2", position={"x": 100, "y": 50}, label="Node 2")

    # Assign NodeSpec objects directly
    flow.nodes = [node1, node2]

    # Verify they were converted to dicts
    assert isinstance(flow.nodes[0], dict)
    assert isinstance(flow.nodes[1], dict)
    assert flow.nodes[0]["id"] == "n1"
    assert flow.nodes[1]["id"] == "n2"


def test_edgespec_autoserialize_on_assignment() -> None:
    """EdgeSpec objects should be automatically converted when assigned to edges param."""
    flow = ReactFlow()
    edge1 = EdgeSpec(id="e1", source="n1", target="n2", label="Edge 1")
    edge2 = EdgeSpec(id="e2", source="n2", target="n3")

    # Assign EdgeSpec objects directly
    flow.edges = [edge1, edge2]

    # Verify they were converted to dicts
    assert isinstance(flow.edges[0], dict)
    assert isinstance(flow.edges[1], dict)
    assert flow.edges[0]["id"] == "e1"
    assert flow.edges[1]["id"] == "e2"


def test_mixed_nodespec_and_dict() -> None:
    """Should handle a mix of NodeSpec and dict objects."""
    node1 = NodeSpec(id="n1", position={"x": 0, "y": 0})
    node2 = {"id": "n2", "position": {"x": 100, "y": 50}, "data": {}}
    flow = ReactFlow(nodes=[node1, node2])

    # Both should be dicts
    assert isinstance(flow.nodes[0], dict)
    assert isinstance(flow.nodes[1], dict)
    assert flow.nodes[0]["id"] == "n1"
    assert flow.nodes[1]["id"] == "n2"


def test_mixed_edgespec_and_dict() -> None:
    """Should handle a mix of EdgeSpec and dict objects."""
    edge1 = EdgeSpec(id="e1", source="n1", target="n2")
    edge2 = {"id": "e2", "source": "n2", "target": "n3", "data": {}}
    flow = ReactFlow(edges=[edge1, edge2])

    # Both should be dicts
    assert isinstance(flow.edges[0], dict)
    assert isinstance(flow.edges[1], dict)
    assert flow.edges[0]["id"] == "e1"
    assert flow.edges[1]["id"] == "e2"
