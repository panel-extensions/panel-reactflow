"""UI tests for group (parent) nodes."""

import pytest
from panel.tests.util import serve_component, wait_until

from panel_reactflow import NodeSpec, ReactFlow

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
