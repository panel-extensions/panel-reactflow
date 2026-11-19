"""UI tests for group (parent) nodes."""

import re

import panel as pn
import pytest
from panel.tests.util import serve_component, wait_until

from panel_reactflow import NodeSpec, ReactFlow
from panel_reactflow.base import GROUP_HEADER_HEIGHT

pytest.importorskip("playwright")

from playwright.sync_api import expect

pytestmark = pytest.mark.ui


def _flow(**kwargs):
    nodes = [
        NodeSpec(id="src", position={"x": 0, "y": 80}, label="Source").to_dict(),
        NodeSpec(id="a", position={"x": 260, "y": 0}, label="Inner A").to_dict(),
        NodeSpec(id="b", position={"x": 260, "y": 160}, label="Inner B").to_dict(),
        NodeSpec(id="dst", position={"x": 620, "y": 80}, label="Sink").to_dict(),
    ]
    edges = [
        {"id": "src-a", "source": "src", "target": "a"},
        {"id": "a-b", "source": "a", "target": "b"},
        {"id": "b-dst", "source": "b", "target": "dst"},
    ]
    return ReactFlow(nodes=nodes, edges=edges, width=1000, height=600, **kwargs)


def _node(page, node_id):
    return page.locator(f'.react-flow__node[data-id="{node_id}"]')


def _edge(page, edge_id):
    return page.locator(f'.react-flow__edge[data-id="{edge_id}"]')


def _center(box):
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _contains(outer, inner):
    return (
        outer["x"] <= inner["x"]
        and outer["y"] <= inner["y"]
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + 1
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + 1
    )


def _wait_settled(page, locator):
    previous = None
    for _ in range(50):
        current = locator.bounding_box()
        if current is not None and current == previous:
            return current
        previous = current
        page.wait_for_timeout(100)
    return previous


def _edge_end(page, edge_id):
    """Return the screen position of the end of an edge's path."""
    return (
        _edge(page, edge_id)
        .locator("path.react-flow__edge-path")
        .evaluate(
            """path => {
            const point = path.getPointAtLength(path.getTotalLength());
            const matrix = path.getScreenCTM();
            return {x: point.x * matrix.a + matrix.e, y: point.y * matrix.d + matrix.f};
        }"""
        )
    )


def _edge_start(page, edge_id):
    return (
        _edge(page, edge_id)
        .locator("path.react-flow__edge-path")
        .evaluate(
            """path => {
            const point = path.getPointAtLength(0);
            const matrix = path.getScreenCTM();
            return {x: point.x * matrix.a + matrix.e, y: point.y * matrix.d + matrix.f};
        }"""
        )
    )


def test_group_frames_its_children(page):
    flow = _flow()
    group_id = flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    serve_component(page, flow)

    group = _node(page, group_id)
    expect(group.locator(".rf-group-label")).to_have_text("Pipeline")
    frame = _wait_settled(page, group)
    for child in ("a", "b"):
        assert _contains(frame, _node(page, child).bounding_box())
    assert not _contains(frame, _node(page, "src").bounding_box())


def test_group_created_from_selection(page):
    flow = _flow()
    grouped = []
    flow.on("nodes_grouped", grouped.append)
    serve_component(page, flow)
    _wait_settled(page, _node(page, "a"))

    _node(page, "a").locator(".rf-node-label").click()
    _node(page, "b").locator(".rf-node-label").click(modifiers=["Shift"])
    wait_until(lambda: flow.selection["nodes"] == ["a", "b"] or sorted(flow.selection["nodes"]) == ["a", "b"], timeout=5000)

    group_id = flow.group_nodes()
    expect(_node(page, group_id)).to_be_visible()
    assert grouped == [{"type": "nodes_grouped", "group_id": group_id, "node_ids": flow.selection["nodes"]}]
    frame = _wait_settled(page, _node(page, group_id))
    assert _contains(frame, _node(page, "a").bounding_box())


def test_dragging_group_moves_children(page):
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    moves = []
    flow.on("node_moved", moves.append)
    serve_component(page, flow)

    group = _node(page, "g")
    _wait_settled(page, group)
    child_before = _node(page, "a").bounding_box()
    relative_before = dict(flow._get_node("a")["position"])
    group_before = dict(flow._get_node("g")["position"])

    x, y = _center(group.locator(".rf-group-label").bounding_box())
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 120, y + 60, steps=8)
    page.mouse.up()

    wait_until(lambda: any(m["node_id"] == "g" for m in moves), timeout=5000)
    child_after = _node(page, "a").bounding_box()
    assert child_after["x"] > child_before["x"] + 50
    assert child_after["y"] > child_before["y"] + 20
    assert flow._get_node("g")["position"]["x"] > group_before["x"] + 50
    assert flow._get_node("a")["position"] == relative_before
    assert {m["node_id"] for m in moves} == {"g"}


def test_collapse_and_expand_reroutes_edges(page):
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    toggles = []
    flow.on("group_toggled", toggles.append)
    serve_component(page, flow)

    group = _node(page, "g")
    expanded = _wait_settled(page, group)
    group.get_by_role("button", name="Collapse group").click()

    expect(_node(page, "a")).to_be_hidden()
    expect(_node(page, "b")).to_be_hidden()
    expect(_edge(page, "a-b")).to_be_hidden()
    wait_until(lambda: toggles == [{"type": "group_toggled", "node_id": "g", "collapsed": True}], timeout=5000)
    assert flow._get_node("g")["data"]["collapsed"] is True
    assert [e["id"] for e in flow.edges] == ["src-a", "a-b", "b-dst"]

    collapsed = _wait_settled(page, group)
    assert collapsed["width"] < expanded["width"] and collapsed["height"] < expanded["height"]
    # The crossing edges now end on the collapsed group's border.
    end = _edge_end(page, "src-a")
    assert abs(end["x"] - collapsed["x"]) < 12
    assert collapsed["y"] <= end["y"] <= collapsed["y"] + collapsed["height"]
    start = _edge_start(page, "b-dst")
    assert abs(start["x"] - (collapsed["x"] + collapsed["width"])) < 12

    group.get_by_role("button", name="Expand group").click()
    expect(_node(page, "a")).to_be_visible()
    expect(_edge(page, "a-b")).to_be_visible()
    wait_until(lambda: len(toggles) == 2 and toggles[-1]["collapsed"] is False, timeout=5000)
    a_box = _wait_settled(page, _node(page, "a"))
    end = _edge_end(page, "src-a")
    assert abs(end["x"] - a_box["x"]) < 12


def test_collapse_from_python_uses_handle_map(page):
    flow = _flow()
    flow.group_nodes(
        ["a", "b"],
        group_id="g",
        label="Pipeline",
        data={
            "inputs": [{"id": "in", "label": "in"}],
            "outputs": [{"id": "first", "label": "first"}, {"id": "out", "label": "out"}],
            "handle_map": [{"node": "a", "group_handle": "in"}, {"node": "b", "handle": None, "group_handle": "out"}],
        },
    )
    serve_component(page, flow)
    _wait_settled(page, _node(page, "g"))

    flow.collapse_group("g")
    expect(_node(page, "a")).to_be_hidden()
    group = _node(page, "g")
    out_handle = _wait_settled(page, group.locator('.react-flow__handle[data-handleid="out"]'))
    start = _edge_start(page, "b-dst")
    assert abs(start["y"] - (out_handle["y"] + out_handle["height"] / 2)) < 4

    flow.expand_group("g")
    expect(_node(page, "a")).to_be_visible()


def test_connecting_to_collapsed_group_handle_targets_inner_node(page):
    flow = _flow()
    flow.add_node({"id": "extra", "position": {"x": 0, "y": 300}, "label": "Extra", "data": {}})
    flow.group_nodes(
        ["a", "b"],
        group_id="g",
        data={"collapsed": True, "inputs": [{"id": "in"}], "handle_map": [{"node": "b", "group_handle": "in"}]},
    )
    serve_component(page, flow)
    expect(_node(page, "a")).to_be_hidden()

    source = _wait_settled(page, _node(page, "extra").locator(".react-flow__handle.source"))
    target = _node(page, "g").locator('.react-flow__handle[data-handleid="in"]').bounding_box()
    page.mouse.move(*_center(source))
    page.mouse.down()
    page.mouse.move(*_center(target), steps=10)
    page.mouse.up()

    wait_until(lambda: any(e["source"] == "extra" for e in flow.edges), timeout=5000)
    (edge,) = [e for e in flow.edges if e["source"] == "extra"]
    assert edge["target"] == "b"


def test_ungroup_keeps_children_in_place(page):
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    ungrouped = []
    flow.on("nodes_ungrouped", ungrouped.append)
    serve_component(page, flow)
    _wait_settled(page, _node(page, "g"))
    before = {child: _node(page, child).bounding_box() for child in ("a", "b")}

    flow.ungroup("g")
    expect(_node(page, "g")).to_have_count(0)
    for child in ("a", "b"):
        after = _wait_settled(page, _node(page, child))
        assert abs(after["x"] - before[child]["x"]) < 2 and abs(after["y"] - before[child]["y"]) < 2
        assert "parentId" not in flow._get_node(child)
    assert ungrouped[0]["node_ids"] == ["a", "b"]
    assert flow._get_node("a")["position"] == {"x": 260.0, "y": 0.0}


def test_serialized_groups_round_trip(page):
    original = _flow()
    original.group_nodes(["a", "b"], group_id="g", label="Pipeline", data={"collapsed": True})
    # Children listed before their parent still render inside it.
    nodes = sorted((dict(node) for node in original.nodes), key=lambda node: node["id"] == "g")
    flow = ReactFlow(nodes=nodes, edges=[dict(edge) for edge in original.edges], width=1000, height=600)
    serve_component(page, flow)

    expect(_node(page, "g")).to_be_visible()
    expect(_node(page, "a")).to_be_hidden()
    flow.expand_group("g")
    frame = _wait_settled(page, _node(page, "g"))
    assert _contains(frame, _wait_settled(page, _node(page, "a")))
    assert [n["id"] for n in flow.nodes] == [n["id"] for n in nodes]


def test_nested_groups_collapse_outer(page):
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="inner", label="Inner")
    flow.group_nodes(["inner", "dst"], group_id="outer", label="Outer")
    serve_component(page, flow)

    outer = _wait_settled(page, _node(page, "outer"))
    assert _contains(outer, _wait_settled(page, _node(page, "inner")))
    flow.collapse_group("outer")
    for hidden in ("inner", "a", "b", "dst"):
        expect(_node(page, hidden)).to_be_hidden()
    expect(_edge(page, "src-a")).to_be_visible()
    collapsed = _wait_settled(page, _node(page, "outer"))
    assert abs(_edge_end(page, "src-a")["x"] - collapsed["x"]) < 12
    flow.expand_group("outer")
    expect(_node(page, "a")).to_be_visible()


def test_delete_key_skips_nodes_hidden_by_collapse(page):
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    serve_component(page, flow)
    _wait_settled(page, _node(page, "a"))

    _node(page, "a").locator(".rf-node-label").click()
    wait_until(lambda: flow.selection["nodes"] == ["a"], timeout=5000)
    _node(page, "g").get_by_role("button", name="Collapse group").click()
    expect(_node(page, "a")).to_be_hidden()
    wait_until(lambda: flow.selection["nodes"] == [], timeout=5000)

    page.keyboard.press("Delete")
    page.wait_for_timeout(300)
    assert [node["id"] for node in flow.nodes] == ["src", "g", "a", "b", "dst"]

    _node(page, "g").get_by_role("button", name="Expand group").click()
    expect(_node(page, "a")).to_be_visible()
    expect(_node(page, "a")).not_to_have_class(re.compile(r"\bselected\b"))
    assert flow.selection["nodes"] == []


def test_handle_map_direction_separates_same_named_handles(page):
    from panel_reactflow import NodeType

    flow = ReactFlow(
        nodes=[
            NodeSpec(id="src", position={"x": 0, "y": 0}, label="Source").to_dict(),
            NodeSpec(id="extra", position={"x": 0, "y": 200}, label="Extra").to_dict(),
            NodeSpec(id="mid", type="relay", position={"x": 300, "y": 80}, label="Relay").to_dict(),
            NodeSpec(id="dst", position={"x": 640, "y": 80}, label="Sink").to_dict(),
        ],
        edges=[
            {"id": "src-mid", "source": "src", "target": "mid", "targetHandle": "x"},
            {"id": "mid-dst", "source": "mid", "sourceHandle": "x", "target": "dst"},
        ],
        node_types={"relay": NodeType(type="relay", inputs=["x"], outputs=["x"])},
        width=1000,
        height=600,
    )
    flow.group_nodes(
        ["mid"],
        group_id="g",
        data={
            "collapsed": True,
            "inputs": [{"id": "in"}],
            "outputs": [{"id": "pad"}, {"id": "out"}],
            "handle_map": [
                {"node": "mid", "handle": "x", "direction": "input", "group_handle": "in"},
                {"node": "mid", "handle": "x", "direction": "output", "group_handle": "out"},
            ],
        },
    )
    serve_component(page, flow)
    expect(_node(page, "mid")).to_be_hidden()

    group = _node(page, "g")
    in_handle = _wait_settled(page, group.locator('.react-flow__handle[data-handleid="in"]'))
    out_handle = group.locator('.react-flow__handle[data-handleid="out"]').bounding_box()
    end = _edge_end(page, "src-mid")
    assert abs(end["y"] - (in_handle["y"] + in_handle["height"] / 2)) < 4
    start = _edge_start(page, "mid-dst")
    assert abs(start["y"] - (out_handle["y"] + out_handle["height"] / 2)) < 4

    source = _wait_settled(page, _node(page, "extra").locator(".react-flow__handle.source"))
    page.mouse.move(*_center(source))
    page.mouse.down()
    page.mouse.move(*_center(in_handle), steps=10)
    page.mouse.up()

    wait_until(lambda: any(e["source"] == "extra" for e in flow.edges), timeout=5000)
    added = [e for e in flow.edges if e["source"] == "extra"]
    assert [(e["target"], e["targetHandle"]) for e in added] == [("mid", "x")]


def _connect(page, source, target):
    page.mouse.move(*_center(source))
    page.mouse.down()
    page.mouse.move(*_center(target), steps=10)
    page.mouse.up()


def _collapsed_flow(**kwargs):
    flow = _flow(**kwargs)
    flow.add_node({"id": "extra", "position": {"x": 0, "y": 300}, "label": "Extra", "data": {}})
    flow.group_nodes(
        ["a", "b"],
        group_id="g",
        data={"collapsed": True, "inputs": [{"id": "in"}], "outputs": [{"id": "out"}], "handle_map": [{"node": "a", "group_handle": "in"}]},
    )
    return flow


@pytest.mark.parametrize("allowed", [True, False])
def test_python_validator_checks_collapsed_group_inner_ends(page, allowed):
    flow = _collapsed_flow()
    seen = []

    def validator(connection):
        seen.append((connection["source"], connection["target"]))
        if connection["target"] == "a" and not allowed:
            return "a is full"
        return None

    flow.add_connection_validator(validator)
    serve_component(page, flow)
    expect(_node(page, "a")).to_be_hidden()

    source = _wait_settled(page, _node(page, "extra").locator(".react-flow__handle.source"))
    target_handle = _node(page, "g").locator('.react-flow__handle[data-handleid="in"]')
    page.mouse.move(*_center(source))
    page.mouse.down()
    page.mouse.move(*_center(target_handle.bounding_box()), steps=10)
    expect(target_handle).to_have_class(re.compile("rf-handle-valid" if allowed else "rf-handle-invalid"))
    page.mouse.up()

    assert ("extra", "a") in seen and ("extra", "g") not in seen
    if allowed:
        wait_until(lambda: any(e["source"] == "extra" for e in flow.edges), timeout=5000)
        assert [e["target"] for e in flow.edges if e["source"] == "extra"] == ["a"]
    else:
        page.wait_for_timeout(500)
        assert not any(e["source"] == "extra" for e in flow.edges)


def test_cycle_policy_applies_to_inner_edges(page):
    flow = _collapsed_flow(connection_validation={"cycles": True})
    serve_component(page, flow)
    expect(_node(page, "a")).to_be_hidden()

    # dst -> g.in maps to dst -> a, which closes a -> b -> dst -> a.
    source = _wait_settled(page, _node(page, "dst").locator(".react-flow__handle.source"))
    target_handle = _node(page, "g").locator('.react-flow__handle[data-handleid="in"]')
    page.mouse.move(*_center(source))
    page.mouse.down()
    page.mouse.move(*_center(target_handle.bounding_box()), steps=10)
    expect(target_handle).to_have_class(re.compile("rf-handle-invalid"))
    page.mouse.up()
    page.wait_for_timeout(500)
    assert [e["id"] for e in flow.edges] == ["src-a", "a-b", "b-dst"]


def test_mapping_without_direction_uses_group_handle_side(page):
    flow = _flow()
    # b is only mapped to an output, so the edge into a from outside keeps
    # going to the group's input instead of being dropped.
    flow.group_nodes(
        ["a", "b"],
        group_id="g",
        data={"collapsed": True, "inputs": [{"id": "in"}], "outputs": [{"id": "out"}], "handle_map": [{"node": "a", "group_handle": "out"}]},
    )
    serve_component(page, flow)
    expect(_node(page, "a")).to_be_hidden()
    group = _wait_settled(page, _node(page, "g"))
    expect(_edge(page, "src-a")).to_be_visible()
    assert abs(_edge_end(page, "src-a")["x"] - group["x"]) < 12


def test_extent_parent_keeps_child_inside_fixed_size_group(page):
    flow = _flow()
    flow.group_nodes(["a"], group_id="g", style={"width": 320, "height": 200}, extent="parent", expand_parent=None)
    moves = []
    flow.on("node_moved", moves.append)
    serve_component(page, flow)
    frame = _wait_settled(page, _node(page, "g"))

    x, y = _center(_node(page, "a").locator(".rf-node-label").bounding_box())
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 600, y + 400, steps=10)
    page.mouse.up()
    wait_until(lambda: any(m["node_id"] == "a" for m in moves), timeout=5000)

    # Dragging to the edge of the canvas pans it, so compare sizes.
    after = _wait_settled(page, _node(page, "g"))
    assert (after["width"], after["height"]) == (frame["width"], frame["height"])
    assert _contains(after, _node(page, "a").bounding_box())


def test_children_stay_below_group_header(page):
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    moves = []
    flow.on("node_moved", moves.append)
    serve_component(page, flow)
    _wait_settled(page, _node(page, "g"))

    x, y = _center(_node(page, "b").locator(".rf-node-label").bounding_box())
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 150, steps=10)
    page.mouse.up()
    wait_until(lambda: any(m["node_id"] == "b" for m in moves), timeout=5000)
    assert flow._get_node("b")["position"]["y"] >= GROUP_HEADER_HEIGHT
    expect(_node(page, "g").get_by_role("button", name="Collapse group")).to_be_visible()


def test_collapse_is_view_only_when_not_editable(page):
    flow = _flow(editable=False)
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    toggles = []
    flow.on("group_toggled", toggles.append)
    serve_component(page, flow)
    _wait_settled(page, _node(page, "g"))

    _node(page, "g").get_by_role("button", name="Collapse group").click()
    expect(_node(page, "a")).to_be_hidden()
    page.wait_for_timeout(500)
    assert toggles == []
    assert "collapsed" not in flow._get_node("g")["data"]
    _node(page, "g").get_by_role("button", name="Expand group").click()
    expect(_node(page, "a")).to_be_visible()


def _selected_in_page(page):
    return sorted(page.locator(".react-flow__node.selected").evaluate_all("els => els.map(n => n.dataset.id)"))


def _select_a_and_b(page, flow):
    _wait_settled(page, _node(page, "a"))
    _node(page, "a").locator(".rf-node-label").click()
    _node(page, "b").locator(".rf-node-label").click(modifiers=["Shift"])
    wait_until(lambda: sorted(flow.selection["nodes"]) == ["a", "b"], timeout=5000)
    page.wait_for_timeout(300)


def test_python_selection_after_grouping_page_selection(page):
    flow = _flow()
    serve_component(page, flow)
    _select_a_and_b(page, flow)

    group_id = flow.group_nodes()
    flow.nodes = [dict(node, selected=node["id"] == group_id) for node in flow.nodes]
    page.wait_for_timeout(1000)
    assert _selected_in_page(page) == [group_id]
    assert flow.selection["nodes"] == [group_id]


def test_full_sync_does_not_restore_selection_cleared_in_page(page):
    flow = _flow()
    serve_component(page, flow)
    _select_a_and_b(page, flow)

    # The page clears the selection and a full sync made before Python
    # handled the clear arrives.
    pane = page.locator(".react-flow__pane").bounding_box()
    page.mouse.click(pane["x"] + pane["width"] - 20, pane["y"] + pane["height"] - 20)
    flow.group_nodes(["src"], group_id="g")
    page.wait_for_timeout(1000)
    assert _selected_in_page(page) == []
    assert flow.selection["nodes"] == []


def test_full_sync_keeps_selection_python_has_not_received(page):
    flow = _flow(sync_mode="debounce", debounce_ms=1500)
    serve_component(page, flow)
    _wait_settled(page, _node(page, "a"))

    _node(page, "a").locator(".rf-node-label").click()
    _node(page, "b").locator(".rf-node-label").click(modifiers=["Shift"])
    wait_until(lambda: sorted(flow.selection["nodes"]) == ["a", "b"], timeout=5000)

    # The page clears the selection, then a full sync still carrying the
    # old flags arrives before the debounced report reaches Python.
    pane = page.locator(".react-flow__pane").bounding_box()
    page.mouse.click(pane["x"] + pane["width"] - 20, pane["y"] + pane["height"] - 20)
    flow.group_nodes(["src"], group_id="g")
    expect(_node(page, "g")).to_be_visible()
    page.wait_for_timeout(300)
    expect(page.locator(".react-flow__node.selected")).to_have_count(0)
    wait_until(lambda: flow.selection["nodes"] == [], timeout=5000)
    page.wait_for_timeout(300)
    expect(page.locator(".react-flow__node.selected")).to_have_count(0)


def test_python_collapse_overrides_read_only_toggle(page):
    flow = _flow(editable=False)
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    serve_component(page, flow)
    _wait_settled(page, _node(page, "g"))

    _node(page, "g").get_by_role("button", name="Collapse group").click()
    expect(_node(page, "a")).to_be_hidden()
    _node(page, "g").get_by_role("button", name="Expand group").click()
    expect(_node(page, "a")).to_be_visible()

    flow.collapse_group("g")
    expect(_node(page, "a")).to_be_hidden()
    flow.expand_group("g")
    expect(_node(page, "a")).to_be_visible()


def _press_delete_on(page, node_id, *also):
    _node(page, node_id).locator(".rf-group-label, .rf-node-label").first.click()
    for other in also:
        _node(page, other).locator(".rf-node-label").click(modifiers=["Shift"])
    page.keyboard.press("Delete")


@pytest.mark.parametrize("collapsed", [False, True])
def test_delete_key_on_group_ungroups_with_ungroup_policy(page, collapsed):
    flow = _flow(group_delete="ungroup")
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline", data={"collapsed": collapsed})
    events = []
    flow.on("*", lambda event: events.append(event["type"]))
    serve_component(page, flow)
    _wait_settled(page, _node(page, "g"))
    before = {child: dict(flow.absolute_position(child)) for child in ("a", "b")}

    _press_delete_on(page, "g")
    wait_until(lambda: flow._get_node("g") is None, timeout=5000)
    page.wait_for_timeout(500)

    assert [n["id"] for n in flow.nodes] == ["src", "a", "b", "dst"]
    assert [e["id"] for e in flow.edges] == ["src-a", "a-b", "b-dst"]
    for child in ("a", "b"):
        assert flow.absolute_position(child) == before[child]
        assert "parentId" not in flow._get_node(child)
    assert [e for e in events if e in ("nodes_ungrouped", "node_deleted", "edge_deleted")] == ["nodes_ungrouped", "node_deleted"]
    expect(_node(page, "a")).to_be_visible()
    expect(_edge(page, "a-b")).to_be_visible()
    expect(page.locator(".react-flow__node")).to_have_count(4)


def test_delete_group_and_selected_member_with_ungroup_policy(page):
    flow = _flow(group_delete="ungroup")
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    serve_component(page, flow)
    _wait_settled(page, _node(page, "g"))
    b_box = _node(page, "b").bounding_box()

    _press_delete_on(page, "g", "a")
    wait_until(lambda: flow._get_node("g") is None, timeout=5000)
    page.wait_for_timeout(500)

    assert [n["id"] for n in flow.nodes] == ["src", "b", "dst"]
    assert [e["id"] for e in flow.edges] == ["b-dst"]
    after = _wait_settled(page, _node(page, "b"))
    assert abs(after["x"] - b_box["x"]) < 2 and abs(after["y"] - b_box["y"]) < 2


def test_delete_key_on_group_cascades_by_default(page):
    flow = _flow()
    flow.group_nodes(["a", "b"], group_id="g", label="Pipeline")
    serve_component(page, flow)
    _wait_settled(page, _node(page, "g"))

    _press_delete_on(page, "g")
    wait_until(lambda: flow._get_node("g") is None, timeout=5000)
    assert [n["id"] for n in flow.nodes] == ["src", "dst"]
    assert flow.edges == []


def test_select_new_group_right_after_grouping(page):
    # Inside an event callback the nodes sync is held until the callback
    # returns, so the selection patch reaches the page before the new group.
    flow = _flow()
    created = []

    def group_and_select(event):
        group_id = flow.group_nodes(["a", "b"])
        flow.patch_node_props(group_id, {"selected": True})
        created.append(group_id)

    flow.on("node_clicked", group_and_select)
    serve_component(page, flow)
    _wait_settled(page, _node(page, "src"))

    _node(page, "src").locator(".rf-node-label").click()
    wait_until(lambda: bool(created), timeout=5000)
    expect(_node(page, created[0])).to_have_class(re.compile(r"\bselected\b"))
    wait_until(lambda: created[0] in flow.selection["nodes"], timeout=5000)


@pytest.mark.parametrize("deselect_first", [False, True])
def test_selection_patches_in_callback_survive_stale_nodes_sync(page, deselect_first):
    # The nodes sync from group_nodes() is serialized while a and b are still
    # selected and reaches the page after the patches that follow it.
    flow = _flow()

    def group(event):
        if flow._get_node("g") is not None:
            return
        if deselect_first:
            for node_id in ("a", "b"):
                flow.patch_node_props(node_id, {"selected": False})
        flow.group_nodes(["a", "b"], group_id="g", label="G")
        flow.patch_node_props("g", {"selected": True})
        if not deselect_first:
            for node_id in ("a", "b"):
                flow.patch_node_props(node_id, {"selected": False})

    button = pn.widgets.Button(label="Group")
    button.on_click(group)
    serve_component(page, pn.Column(button, flow))
    _select_a_and_b(page, flow)

    page.get_by_role("button", name="Group").click()
    wait_until(lambda: flow._get_node("g") is not None, timeout=5000)
    page.wait_for_timeout(1500)
    assert _selected_in_page(page) == ["g"]
    assert flow.selection["nodes"] == ["g"]
