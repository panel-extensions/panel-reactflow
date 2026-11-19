"""Tests for group (parent) nodes."""

import param
import pytest

from panel_reactflow import Node, NodeSpec, ReactFlow
from panel_reactflow.base import GROUP_HEADER_HEIGHT


def _flow(**kwargs):
    return ReactFlow(
        nodes=[
            NodeSpec(id="a", position={"x": 100, "y": 100}).to_dict(),
            NodeSpec(id="b", position={"x": 300, "y": 220}).to_dict(),
            NodeSpec(id="c", position={"x": 0, "y": 0}).to_dict(),
        ],
        edges=[{"id": "c-a", "source": "c", "target": "a"}, {"id": "a-b", "source": "a", "target": "b"}],
        **kwargs,
    )


def _ids(flow):
    return [flow._node_id(node) for node in flow.nodes]


def test_node_spec_parent_fields_roundtrip():
    spec = NodeSpec(id="n", parentId="g", extent="parent", expandParent=True, hidden=False, zIndex=3)
    payload = spec.to_dict()
    assert payload["parentId"] == "g"
    assert payload["extent"] == "parent"
    assert payload["expandParent"] is True
    assert payload["hidden"] is False
    assert payload["zIndex"] == 3
    assert NodeSpec.from_dict(payload) == spec


def test_node_spec_omits_unset_parent_fields():
    payload = NodeSpec(id="n").to_dict()
    assert not {"parentId", "extent", "expandParent", "hidden", "zIndex"} & set(payload)


def test_node_instance_serializes_parent_fields():
    node = Node(id="n", parentId="g", extent=[[0, 0], [100, 50]], zIndex=2)
    payload = node.to_dict()
    assert payload["parentId"] == "g"
    assert payload["extent"] == [[0, 0], [100, 50]]
    assert payload["zIndex"] == 2
    assert "hidden" not in payload


def test_node_instance_parent_assignment_is_patched():
    flow = ReactFlow(nodes=[Node(id="g", type="group"), Node(id="n")])
    msgs = []
    flow._send_msg = msgs.append
    flow.nodes[1].parentId = "g"
    assert msgs == [{"type": "patch_node_props", "node_id": "n", "patch": {"parentId": "g"}}]


def test_patch_node_props_accepts_parent_fields():
    flow = _flow()
    flow.add_node({"id": "g", "type": "group", "position": {"x": 0, "y": 0}, "data": {}})
    flow.patch_node_props("a", {"parentId": "g", "hidden": True, "zIndex": 5})
    node = flow._get_node("a")
    assert (node["parentId"], node["hidden"], node["zIndex"]) == ("g", True, 5)
    flow.patch_node_props("a", {"parentId": None})
    assert "parentId" not in flow._get_node("a")


def test_add_node_rejects_unknown_parent():
    flow = _flow()
    with pytest.raises(ValueError, match="unknown parent"):
        flow.add_node({"id": "x", "position": {"x": 0, "y": 0}, "data": {}, "parentId": "missing"})


def test_add_nodes_accepts_parent_later_in_batch():
    flow = ReactFlow()
    flow.add_nodes(
        [
            {"id": "child", "position": {"x": 10, "y": 10}, "data": {}, "parentId": "g"},
            {"id": "g", "type": "group", "position": {"x": 0, "y": 0}, "data": {}},
        ]
    )
    assert _ids(flow) == ["child", "g"]


def test_add_nodes_rejects_parent_cycle():
    flow = ReactFlow()
    with pytest.raises(ValueError, match="nested in itself"):
        flow.add_nodes(
            [
                {"id": "x", "position": {"x": 0, "y": 0}, "data": {}, "parentId": "y"},
                {"id": "y", "position": {"x": 0, "y": 0}, "data": {}, "parentId": "x"},
            ]
        )


def test_group_nodes_places_group_around_nodes():
    flow = _flow()
    events = []
    flow.on("*", events.append)
    group_id = flow.group_nodes(["a", "b"], group_id="g", label="Group", padding=10)
    assert group_id == "g"
    group = flow._get_node("g")
    assert group["type"] == "group"
    assert group["label"] == "Group"
    assert group["position"] == {"x": 90, "y": 90 - GROUP_HEADER_HEIGHT}
    assert _ids(flow) == ["g", "a", "b", "c"]
    for child in ("a", "b"):
        node = flow._get_node(child)
        assert node["parentId"] == "g"
        assert node["expandParent"] is True
        assert "extent" not in node
    assert flow._get_node("a")["position"] == {"x": 10, "y": 10 + GROUP_HEADER_HEIGHT}
    assert flow.absolute_position("a") == {"x": 100, "y": 100}
    assert flow.absolute_position("b") == {"x": 300, "y": 220}
    assert [e["type"] for e in events] == ["node_added", "nodes_grouped"]
    assert events[1] == {"type": "nodes_grouped", "group_id": "g", "node_ids": ["a", "b"]}
    assert [edge["id"] for edge in flow.edges] == ["c-a", "a-b"]


def test_group_nodes_defaults_to_selection():
    flow = _flow()
    flow._handle_msg({"type": "selection_changed", "nodes": ["b", "c"], "edges": []})
    group_id = flow.group_nodes()
    assert group_id.startswith("group-")
    assert {flow._get_node(n).get("parentId") for n in ("b", "c")} == {group_id}
    assert "parentId" not in flow._get_node("a")


def test_group_nodes_options():
    flow = _flow()
    flow.group_nodes(["a"], group_id="g", data={"collapsed": True}, style={"width": 400}, extent="parent", expand_parent=None)
    group, child = flow._get_node("g"), flow._get_node("a")
    assert group["data"] == {"collapsed": True}
    assert group["style"] == {"width": 400}
    assert child["extent"] == "parent"
    assert "expandParent" not in child


def test_group_nodes_errors():
    flow = _flow()
    with pytest.raises(ValueError, match="No nodes"):
        flow.group_nodes()
    with pytest.raises(ValueError, match="No node with id"):
        flow.group_nodes(["a", "missing"])
    flow.group_nodes(["a"], group_id="g")
    with pytest.raises(ValueError, match="same parent"):
        flow.group_nodes(["a", "b"])
    with pytest.raises(ValueError, match="already exists"):
        flow.group_nodes(["b"], group_id="g")
    assert _ids(flow) == ["g", "a", "b", "c"]


def test_nested_groups():
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="inner")
    flow.group_nodes(["inner", "c"], group_id="outer")
    assert flow._get_node("inner")["parentId"] == "outer"
    assert flow.absolute_position("a") == {"x": 100, "y": 100}
    assert flow.absolute_position("c") == {"x": 0, "y": 0}
    with pytest.raises(ValueError, match="in itself"):
        flow.set_parent("outer", "a")


def test_group_nodes_with_node_instances():
    flow = ReactFlow(nodes=[Node(id="a", position={"x": 50, "y": 60}), Node(id="b", position={"x": 150, "y": 60})])
    msgs = []
    flow._send_msg = msgs.append
    flow.group_nodes(["a", "b"], group_id="g")
    a = flow.nodes[1]
    assert isinstance(a, Node)
    assert a.parentId == "g"
    assert a.position == {"x": 20, "y": 20 + GROUP_HEADER_HEIGHT}
    assert flow.absolute_position("a") == {"x": 50, "y": 60}
    # The nodes are synced as a whole, not patched one by one.
    assert not [m for m in msgs if m["type"] == "patch_node_props"]


def test_ungroup_restores_positions():
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g", extent="parent")
    flow.add_edge({"id": "c-g", "source": "c", "target": "g"})
    events = []
    flow.on("*", events.append)
    assert flow.ungroup("g") == ["a", "b"]
    assert _ids(flow) == ["a", "b", "c"]
    assert flow._get_node("a")["position"] == {"x": 100, "y": 100}
    for child in ("a", "b"):
        assert not {"parentId", "extent", "expandParent"} & set(flow._get_node(child))
    assert [edge["id"] for edge in flow.edges] == ["c-a", "a-b"]
    assert [e["type"] for e in events] == ["node_deleted", "nodes_ungrouped"]
    assert events[-1] == {"type": "nodes_ungrouped", "group_id": "g", "node_ids": ["a", "b"]}


def test_ungroup_nested_moves_children_to_outer_group():
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="inner")
    flow.group_nodes(["inner", "c"], group_id="outer")
    flow.ungroup("inner")
    assert flow._get_node("a")["parentId"] == "outer"
    assert flow.absolute_position("a") == {"x": 100, "y": 100}


def test_set_parent_keeps_absolute_position():
    flow = _flow()
    flow.add_node({"id": "g", "type": "group", "position": {"x": 40, "y": 30}, "data": {}})
    flow.set_parent(["a"], "g", extent="parent")
    node = flow._get_node("a")
    assert node["position"] == {"x": 60, "y": 70}
    assert node["extent"] == "parent"
    flow.set_parent("a", None)
    assert flow._get_node("a")["position"] == {"x": 100, "y": 100}
    assert not {"parentId", "extent"} & set(flow._get_node("a"))


def test_remove_group_removes_descendants():
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="inner")
    flow.group_nodes(["inner"], group_id="outer")
    deleted = []
    flow.on("node_deleted", deleted.append)
    flow.remove_node("outer")
    assert _ids(flow) == ["c"]
    assert flow.edges == []
    assert [e["node_id"] for e in deleted] == ["outer", "inner", "a", "b"]


def test_collapse_group_patches_data_and_emits():
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g")
    msgs, toggles = [], []
    flow._send_msg = msgs.append
    flow.on("group_toggled", toggles.append)
    flow.collapse_group("g")
    assert flow._get_node("g")["data"]["collapsed"] is True
    assert {"type": "patch_node_data", "node_id": "g", "patch": {"collapsed": True}} in msgs
    flow.collapse_group("g")
    flow.expand_group("g")
    assert flow._get_node("g")["data"]["collapsed"] is False
    assert toggles == [
        {"type": "group_toggled", "node_id": "g", "collapsed": True},
        {"type": "group_toggled", "node_id": "g", "collapsed": False},
    ]
    assert [edge["id"] for edge in flow.edges] == ["c-a", "a-b"]
    with pytest.raises(ValueError):
        flow.collapse_group("missing")


def test_group_toggled_message_from_browser():
    flow = _flow()
    flow.group_nodes(["a"], group_id="g")
    toggles = []
    flow.on("group_toggled", toggles.append)
    flow._handle_msg({"type": "group_toggled", "node_id": "g", "collapsed": True})
    flow._handle_msg({"type": "group_toggled", "node_id": "missing", "collapsed": True})
    assert flow._get_node("g")["data"]["collapsed"] is True
    assert toggles == [{"type": "group_toggled", "node_id": "g", "collapsed": True}]


def test_serialized_group_round_trip():
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g", label="G", data={"collapsed": True, "handle_map": [{"node": "a", "group_handle": "in"}]})
    restored = ReactFlow(nodes=[dict(node) for node in flow.nodes], edges=[dict(edge) for edge in flow.edges])
    assert restored.nodes == flow.nodes
    assert restored.absolute_position("b") == {"x": 300, "y": 220}
    restored.ungroup("g")
    assert restored._get_node("b")["position"] == {"x": 300, "y": 220}


def test_frontend_payload_includes_parent_fields(flow_model):
    flow = _flow()
    flow.group_nodes(["a"], group_id="g")
    model = flow_model(flow)
    payload = {node["id"]: node for node in model.data.nodes}
    assert payload["a"]["parentId"] == "g"
    assert payload["g"]["type"] == "group"


class Task(Node):
    status = param.String(default="idle", precedence=0)


def test_sync_applies_parent_fields_to_instances():
    flow = ReactFlow(nodes=[Node(id="g", type="group"), Task(id="t")])
    flow._handle_msg({"type": "sync", "nodes": [{"id": "g", "type": "group"}, {"id": "t", "parentId": "g", "hidden": True}]})
    task = flow.nodes[1]
    assert (task.parentId, task.hidden) == ("g", True)


def _collapsed(handle_map):
    flow = _flow()
    flow.add_node({"id": "x", "position": {"x": 0, "y": 400}, "data": {}})
    flow.group_nodes(
        ["a", "b"],
        group_id="g",
        data={"collapsed": True, "inputs": [{"id": "in"}], "outputs": ["out"], "handle_map": handle_map},
    )
    return flow


def test_inner_ends_infer_side_from_group_handles():
    flow = _collapsed([{"node": "a", "group_handle": "in"}, {"node": "b", "handle": None, "group_handle": "out"}])
    group = flow._get_node("g")
    assert flow._inner_ends(group, "in", "input") == [("a", None)]
    assert flow._inner_ends(group, "in", "output") == [("g", "in")]
    assert flow._inner_ends(group, "out", "output") == [("b", None)]
    assert flow._inner_ends(flow._get_node("x"), None, "input") == [("x", None)]
    flow.expand_group("g")
    assert flow._inner_ends(flow._get_node("g"), "in", "input") == [("g", "in")]


def test_inner_ends_explicit_direction_wins():
    flow = _collapsed([{"node": "a", "group_handle": "in", "direction": "output"}])
    assert flow._inner_ends(flow._get_node("g"), "in", "input") == [("g", "in")]


def test_validation_candidates_for_collapsed_group_use_inner_ends():
    flow = _collapsed([{"node": "a", "group_handle": "in"}, {"node": "b", "group_handle": "in"}])
    seen = []

    def validator(connection):
        seen.append((connection["source"], connection["target"]))
        return "b is full" if connection["target"] == "b" else None

    flow.add_connection_validator(validator)
    results = flow._connection_candidates("x", None, "source")
    (group_result,) = [r for r in results if r["node_id"] == "g"]
    assert group_result == {"node_id": "g", "handle_id": "in", "handle_type": "target", "reason": "b is full"}
    assert ("x", "a") in seen and ("x", "b") in seen and ("x", "g") not in seen


def test_validation_candidates_when_dragging_from_collapsed_group():
    flow = _collapsed([{"node": "b", "group_handle": "out"}])
    seen = []
    flow.add_connection_validator(lambda connection: seen.append((connection["source"], connection["sourceHandle"])))
    flow._connection_candidates("g", "out", "source")
    assert set(seen) == {("b", None)}
