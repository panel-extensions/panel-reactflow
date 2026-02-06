"""Tests for the public API helpers."""

try:
    import networkx as nx
except ImportError:
    nx = None
import panel as pn
import param
import pytest

from panel_reactflow import EdgeSpec, EdgeType, NodeSpec, NodeType, ReactFlow, SchemaSource
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
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "label": "Pane", "data": {}}, view=view)
    assert events[-1]["type"] == "node_added"


def test_view_idx_updates_on_remove_node(document, comm) -> None:
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}}, view=pn.pane.Markdown("A"))
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}}, view=pn.pane.Markdown("B"))
    flow.add_node({"id": "n3", "position": {"x": 2, "y": 2}, "data": {}}, view=None)

    model = flow.get_root(document, comm=comm)

    flow.remove_node("n1")

    remaining = {node["id"]: node for node in model.data.nodes}
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
