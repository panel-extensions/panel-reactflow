"""Tests for how structural graph changes are synced and what they rebuild."""

import panel as pn
import param
import pytest

from panel_reactflow import Edge, Node, NodeSpec, NodeType, ReactFlow
from panel_reactflow.base import Editor, JsonEditor


def _nodes(n, prefix="n"):
    return [{"id": f"{prefix}{i}", "position": {"x": i, "y": 0}, "data": {"v": i}} for i in range(n)]


def _edges(n, prefix="n"):
    return [{"id": f"e{i}", "source": f"{prefix}{i}", "target": f"{prefix}{i + 1}", "data": {}} for i in range(n - 1)]


def _count_events(flow, name):
    events = []
    flow.param.watch(events.append, [name], onlychanged=False)
    return events


class _RecordingEditor(Editor):
    built: list = []

    def __init__(self, data=None, schema=None, **kwargs):
        super().__init__(data, schema, **kwargs)
        type(self).built.append(self._node_id)

    def __panel__(self):
        return pn.pane.Markdown(str(self._data))


@pytest.fixture
def recording_editor():
    _RecordingEditor.built = []
    yield _RecordingEditor
    _RecordingEditor.built = []


# --- batch APIs ---


def test_add_nodes_assigns_nodes_once_and_emits_per_node() -> None:
    flow = ReactFlow()
    assignments = _count_events(flow, "nodes")
    added = []
    flow.on("node_added", added.append)

    flow.add_nodes(_nodes(5))

    assert len(assignments) == 1
    assert [node["id"] for node in flow.nodes] == [f"n{i}" for i in range(5)]
    assert [event["node"]["id"] for event in added] == [f"n{i}" for i in range(5)]


def test_add_nodes_payloads_match_add_node() -> None:
    sequential = ReactFlow()
    sequential_events = []
    sequential.on("node_added", sequential_events.append)
    for node in _nodes(3):
        sequential.add_node(node)

    batched = ReactFlow()
    batched_events = []
    batched.on("node_added", batched_events.append)
    batched.add_nodes(_nodes(3))

    assert batched.nodes == sequential.nodes
    assert batched_events == sequential_events


def test_add_nodes_is_atomic_on_validation_error() -> None:
    flow = ReactFlow()
    node = Node(id="ok", data={"x": 1})
    # A node without an id fails validation without needing jsonschema.
    with pytest.raises(ValueError):
        flow.add_nodes([node, {"position": {"x": 0, "y": 0}, "data": {}}])
    assert flow.nodes == []
    assert node.flow is None


def test_add_nodes_accepts_specs_and_instances() -> None:
    flow = ReactFlow()
    node = Node(id="inst")
    flow.add_nodes([NodeSpec(id="spec"), node])
    assert flow.nodes[0]["id"] == "spec"
    assert flow.nodes[1] is node
    assert node.flow is flow


def test_add_nodes_empty_is_noop() -> None:
    flow = ReactFlow()
    assignments = _count_events(flow, "nodes")
    flow.add_nodes([])
    assert assignments == []


def test_add_edges_assigns_edges_once_and_emits_per_edge() -> None:
    flow = ReactFlow(nodes=_nodes(4))
    assignments = _count_events(flow, "edges")
    added = []
    flow.on("edge_added", added.append)

    flow.add_edges([{"source": "n0", "target": "n1"}, Edge(source="n1", target="n2"), {"id": "e3", "source": "n2", "target": "n3"}])

    assert len(assignments) == 1
    assert len(flow.edges) == 3
    assert all(event["edge"]["id"] for event in added)
    assert added[2]["edge"]["id"] == "e3"
    assert flow.edges[1].flow is flow


def test_add_edges_is_atomic_on_validation_error() -> None:
    flow = ReactFlow(nodes=_nodes(2))
    with pytest.raises(ValueError):
        flow.add_edges(
            [
                {"id": "a", "source": "n0", "target": "n1"},
                {"id": "b", "source": "n0"},
            ]
        )
    assert flow.edges == []


# --- what a structural change rebuilds ---


def test_adding_node_without_view_does_not_rebuild_views() -> None:
    flow = ReactFlow(nodes=_nodes(3), edges=_edges(3))
    views = _count_events(flow, "_views")
    editor_views = _count_events(flow, "_node_editor_views")
    flow.add_node({"id": "x", "position": {"x": 0, "y": 0}, "data": {}})
    flow.remove_node("x")
    flow.add_edge({"source": "n0", "target": "n2"})
    assert views == []
    assert editor_views == []


def test_adding_node_with_view_rebuilds_views_once() -> None:
    flow = ReactFlow(nodes=_nodes(3))
    views = _count_events(flow, "_views")
    flow.add_node({"id": "x", "position": {"x": 0, "y": 0}, "data": {}, "view": pn.pane.Markdown("x")})
    assert len(views) == 1


def test_reassigning_identical_nodes_is_noop() -> None:
    flow = ReactFlow(nodes=_nodes(3))
    counts = _count_events(flow, "_node_update_count")
    flow.nodes = list(flow.nodes)
    assert counts == []


def test_structural_change_increments_node_update_count_once() -> None:
    flow = ReactFlow(nodes=_nodes(3))
    counts = _count_events(flow, "_node_update_count")
    flow.add_nodes(_nodes(3, prefix="m"))
    assert len(counts) == 1


def test_nodespec_assignment_is_normalized() -> None:
    flow = ReactFlow()
    flow.nodes = [NodeSpec(id="a")]
    assert flow.nodes == [NodeSpec(id="a").to_dict()]
    assert flow._get_node("a") is flow.nodes[0]


def test_nodespec_assignment_alongside_edges_updates_both() -> None:
    flow = ReactFlow()
    flow.param.update(nodes=[NodeSpec(id="a"), NodeSpec(id="b")], edges=[{"id": "e", "source": "a", "target": "b"}])
    assert isinstance(flow.nodes[0], dict)
    assert flow._get_edge("e") is flow.edges[0]


def test_node_schema_lookup_does_not_scan_nodes_per_node() -> None:
    calls = []

    class Counted(Node):
        value = param.Number(default=0, precedence=0)

        @classmethod
        def _data_schema(cls):
            calls.append(cls)
            return super()._data_schema()

    flow = ReactFlow(nodes=[Counted(id=f"n{i}", type="counted") for i in range(200)])
    calls.clear()
    flow.add_nodes([Counted(id=f"m{i}", type="counted") for i in range(50)])
    # One lookup per added node at most, not one per node in the graph.
    assert len(calls) <= 50


# --- lazy editors ---


def test_toolbar_editors_are_built_on_request(recording_editor) -> None:
    flow = ReactFlow(nodes=_nodes(50), default_node_editor=recording_editor)
    assert recording_editor.built == []
    assert flow._node_editor_ids == []

    flow._handle_msg({"type": "editor_requested", "node_id": "n7"})

    assert recording_editor.built == ["n7"]
    assert flow._node_editor_ids == ["n7"]

    # Requesting again does not rebuild.
    flow._handle_msg({"type": "editor_requested", "node_id": "n7"})
    assert recording_editor.built == ["n7"]


def test_editor_request_for_unknown_node_is_ignored(recording_editor) -> None:
    flow = ReactFlow(nodes=_nodes(2), default_node_editor=recording_editor)
    flow._handle_msg({"type": "editor_requested", "node_id": "missing"})
    flow._handle_msg({"type": "editor_requested"})
    assert recording_editor.built == []
    assert flow._node_editor_ids == []


def test_removed_node_drops_requested_editor(recording_editor) -> None:
    flow = ReactFlow(nodes=_nodes(3), default_node_editor=recording_editor)
    flow._handle_msg({"type": "editor_requested", "node_id": "n1"})
    flow.remove_node("n1")
    assert flow._node_editor_ids == []
    assert "n1" not in flow._node_editors


def test_node_mode_builds_every_editor(recording_editor) -> None:
    flow = ReactFlow(nodes=_nodes(3), default_node_editor=recording_editor, editor_mode="node")
    assert recording_editor.built == ["n0", "n1", "n2"]
    assert flow._node_editor_ids == ["n0", "n1", "n2"]
    flow.add_node({"id": "n3", "position": {"x": 0, "y": 0}, "data": {}})
    assert recording_editor.built == ["n0", "n1", "n2", "n3"]
    assert flow._node_editor_ids == ["n0", "n1", "n2", "n3"]


def test_side_mode_builds_editor_on_selection(recording_editor) -> None:
    flow = ReactFlow(nodes=_nodes(20), default_node_editor=recording_editor, editor_mode="side")
    assert recording_editor.built == []
    flow._handle_msg({"type": "selection_changed", "nodes": ["n3"], "edges": []})
    assert recording_editor.built == ["n3"]
    assert flow._selected_editor is not None
    assert flow._node_editor_ids == []


def test_edge_editors_are_built_on_selection() -> None:
    built = []

    class MyEdge(Edge):
        def editor(self, data, schema, *, id, type, on_patch):
            built.append(id)
            return pn.pane.Markdown(id)

    flow = ReactFlow(nodes=_nodes(3), edges=[MyEdge(id=f"e{i}", source="n0", target="n1") for i in range(10)])
    assert built == []
    flow._handle_msg({"type": "selection_changed", "nodes": [], "edges": ["e4"]})
    assert built == ["e4"]


def test_editor_rebuilt_when_node_replaced_under_same_id(recording_editor) -> None:
    flow = ReactFlow(nodes=[Node(id="a", data={"v": 1})], default_node_editor=recording_editor)
    first = flow._get_node_editor("a")
    flow.nodes = [Node(id="a", data={"v": 2})]
    second = flow._get_node_editor("a")
    assert second is not first
    assert second._data == {"v": 2}


def test_editor_rebuilt_when_node_type_patched() -> None:
    flow = ReactFlow(nodes=_nodes(1), node_editors={"json": JsonEditor})
    first = flow._get_node_editor("n0")
    assert not isinstance(first, JsonEditor)
    flow.patch_node_props("n0", {"type": "json"})
    assert isinstance(flow._get_node_editor("n0"), JsonEditor)


def test_editor_rebuilt_when_node_types_change() -> None:
    flow = ReactFlow(nodes=[{"id": "a", "type": "t", "position": {"x": 0, "y": 0}, "data": {"x": 1}}])
    first = flow._get_node_editor("a")
    assert first._schema is None
    flow.node_types = {"t": NodeType(type="t", schema={"type": "object", "properties": {"x": {"type": "number"}}})}
    second = flow._get_node_editor("a")
    assert second is not first
    assert second._schema["properties"] == {"x": {"type": "number"}}


def test_external_data_patch_updates_editor_data(recording_editor) -> None:
    flow = ReactFlow(nodes=_nodes(1), default_node_editor=recording_editor)
    editor = flow._get_node_editor("n0")
    flow.patch_node_data("n0", {"v": 42})
    assert flow._get_node_editor("n0") is editor
    assert editor._data == {"v": 42}


def test_editor_patch_is_not_echoed_into_the_editor(recording_editor) -> None:
    flow = ReactFlow(nodes=_nodes(1), default_node_editor=recording_editor)
    editor = flow._get_node_editor("n0")
    updates = []
    editor.param.watch(updates.append, "_data")
    editor._on_patch({"v": 5})
    assert flow.nodes[0]["data"] == {"v": 5}
    assert updates == []


def test_external_data_patch_rebuilds_non_editor_editor() -> None:
    built = []

    def factory(data, schema, *, id, type, on_patch):
        built.append(dict(data))
        return pn.pane.Markdown(str(data))

    flow = ReactFlow(nodes=_nodes(1), default_node_editor=factory)
    flow._handle_msg({"type": "editor_requested", "node_id": "n0"})
    flow.patch_node_data("n0", {"v": 9})
    assert built == [{"v": 0}, {"v": 9}]


# --- selection ---


def test_selection_only_touches_changed_items() -> None:
    flow = ReactFlow(nodes=_nodes(3), edges=_edges(3))
    flow._handle_msg({"type": "selection_changed", "nodes": ["n1"], "edges": []})
    assert [node.get("selected") for node in flow.nodes] == [None, True, None]
    assert all("selected" not in edge for edge in flow.edges)
    flow._handle_msg({"type": "selection_changed", "nodes": [], "edges": ["e0"]})
    assert [node.get("selected") for node in flow.nodes] == [None, False, None]
    assert flow.edges[0]["selected"] is True
    assert flow.selection == {"nodes": [], "edges": ["e0"]}


def test_selection_of_node_instances_is_not_echoed_to_frontend() -> None:
    nodes = [Node(id=f"n{i}") for i in range(3)]
    flow = ReactFlow(nodes=nodes)
    msgs = []
    flow._send_msg = msgs.append
    props_changed = []
    flow.on("node_props_changed", props_changed.append)
    flow._handle_msg({"type": "selection_changed", "nodes": ["n0", "n2"], "edges": []})
    assert [node.selected for node in nodes] == [True, False, True]
    assert msgs == []
    assert props_changed == []


def test_selection_updates_selected_editor_once() -> None:
    flow = ReactFlow(nodes=_nodes(2), editor_mode="side")
    editors = _count_events(flow, "_selected_editor")
    flow._handle_msg({"type": "selection_changed", "nodes": ["n0"], "edges": []})
    assert len(editors) == 1


# --- id lookups ---


def test_node_lookup_survives_id_reassignment() -> None:
    node = Node(id="a")
    flow = ReactFlow(nodes=[node])
    node.id = "b"
    assert flow._get_node("b") is node
    assert flow._get_node("a") is None


def test_node_appended_in_place_is_found() -> None:
    flow = ReactFlow(nodes=_nodes(2))
    flow.nodes.append({"id": "late", "position": {"x": 0, "y": 0}, "data": {"v": 0}})
    flow.patch_node_data("late", {"v": 1})
    flow.patch_node_props("late", {"label": "Late"})
    flow._handle_msg({"type": "node_moved", "node_id": "late", "position": {"x": 5, "y": 6}})
    late = flow.nodes[-1]
    assert late["data"] == {"v": 1}
    assert late["label"] == "Late"
    assert late["position"] == {"x": 5, "y": 6}


def test_dict_id_changed_in_place_is_found() -> None:
    flow = ReactFlow(nodes=_nodes(2), edges=_edges(2))
    flow.nodes[0]["id"] = "renamed"
    flow.edges[0]["id"] = "renamed-edge"
    flow.patch_node_data("renamed", {"v": 7})
    flow.patch_edge_data("renamed-edge", {"w": 1})
    assert flow.nodes[0]["data"] == {"v": 7}
    assert flow.edges[0]["data"] == {"w": 1}


def test_raising_editor_factory_does_not_break_editor_requests(caplog) -> None:
    def broken(data, schema, *, id, type, on_patch):
        raise RuntimeError("boom")

    flow = ReactFlow(nodes=_nodes(2), node_editors={"broken": broken})
    flow.patch_node_props("n0", {"type": "broken"})
    flow._handle_msg({"type": "editor_requested", "node_id": "n0"})
    assert flow._node_editor_ids == []
    flow._handle_msg({"type": "editor_requested", "node_id": "n1"})
    assert flow._node_editor_ids == ["n1"]
