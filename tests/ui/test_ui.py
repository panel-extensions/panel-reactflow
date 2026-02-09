"""UI tests for ReactFlow using Playwright."""

import panel as pn
import pytest
from panel.tests.util import serve_component, wait_until

from panel_reactflow import EdgeSpec, NodeSpec, NodeType, ReactFlow

pytest.importorskip("playwright")

from playwright.sync_api import expect

pn.extension("jsoneditor")

pytestmark = pytest.mark.ui


_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["idle", "running"],
            "title": "Status",
        },
        "notes": {"type": "string", "title": "Notes"},
    },
}


def _make_flow(*, editor_mode="toolbar", include_edge=True):
    nodes = [
        NodeSpec(
            id="n1",
            position={"x": 0, "y": 0},
            label="Start",
            data={"status": "idle", "notes": "Alpha"},
        ).to_dict()
        | {"view": pn.pane.Markdown("Node view for start")},
        NodeSpec(
            id="n2",
            position={"x": 260, "y": 60},
            label="End",
            data={"status": "running", "notes": "Beta"},
        ).to_dict(),
    ]
    edges = []
    if include_edge:
        edges = [
            EdgeSpec(
                id="e1",
                source="n1",
                target="n2",
                label="Edge A",
            ).to_dict()
        ]
    flow = ReactFlow(
        nodes=nodes,
        edges=edges,
        node_types={
            "panel": NodeType(type="panel", label="Panel", schema=_TASK_SCHEMA),
        },
        editor_mode=editor_mode,
        top_panel=[pn.pane.Markdown("Top panel content")],
        bottom_panel=[pn.pane.Markdown("Bottom panel content")],
        left_panel=[pn.pane.Markdown("Left panel content")],
        right_panel=[pn.pane.Markdown("Right panel content")],
        width=900,
        height=600,
    )
    return flow


def _node_locator(page, label):
    return page.locator(".react-flow__node").filter(has_text=label)


def _edge_label_locator(page, label):
    return page.locator(".react-flow__edge-text").filter(has_text=label)


def _pane_locator(page):
    return page.locator(".react-flow__pane")


def test_render_nodes_edges_labels_views_and_panels(page):
    flow = _make_flow(editor_mode="toolbar", include_edge=True)
    serve_component(page, flow)

    expect(page.locator(".react-flow > div > .react-flow")).to_be_visible()
    expect(page.locator(".react-flow__node")).to_have_count(2)
    expect(page.locator(".react-flow__edge")).to_have_count(1)

    expect(_node_locator(page, "Start")).to_have_count(1)
    expect(_node_locator(page, "End")).to_have_count(1)
    expect(_edge_label_locator(page, "Edge A")).to_have_count(1)

    expect(_node_locator(page, "Node view for start")).to_have_count(1)
    expect(page.locator(".react-flow__panel").filter(has_text="Top panel content")).to_have_count(1)
    expect(page.locator(".react-flow__panel").filter(has_text="Bottom panel content")).to_have_count(1)
    expect(page.locator(".react-flow__panel").filter(has_text="Left panel content")).to_have_count(1)
    expect(page.locator(".react-flow__panel").filter(has_text="Right panel content")).to_have_count(1)


def test_move_node_updates_python_state(page):
    flow = _make_flow()
    serve_component(page, flow)

    node = _node_locator(page, "Start")
    box = node.bounding_box()
    assert box is not None
    start_x = flow.nodes[0]["position"]["x"]
    start_y = flow.nodes[0]["position"]["y"]

    node.click(force=True)
    for _ in range(5):
        page.keyboard.press("ArrowRight")
    for _ in range(5):
        page.keyboard.press("ArrowDown")
    page.locator(".react-flow > div > .react-flow").click(force=True)

    def _moved():
        node_state = next(node for node in flow.nodes if node["id"] == "n1")
        pos = node_state["position"]
        return abs(pos["x"] - start_x) > 5 or abs(pos["y"] - start_y) > 5

    wait_until(_moved, timeout=8000)


def test_viewport_syncs_to_python(page):
    flow = _make_flow()
    flow.viewport = {"x": 0, "y": 0, "zoom": 1}
    serve_component(page, flow)

    pane = _pane_locator(page)
    box = pane.bounding_box()
    assert box is not None

    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 160, box["y"] + box["height"] / 2 + 90)
    page.mouse.up()

    def _viewport_updated():
        viewport = flow.viewport or {}
        return abs(viewport.get("x", 0)) > 1 or abs(viewport.get("y", 0)) > 1

    wait_until(_viewport_updated, timeout=8000)


def test_connecting_edge_updates_python(page):
    flow = _make_flow(include_edge=False)
    serve_component(page, flow)

    source_handle = _node_locator(page, "Start").locator(".react-flow__handle-right").first
    target_handle = _node_locator(page, "End").locator(".react-flow__handle-left").first

    source_handle.drag_to(target_handle)

    def _edge_added():
        return len(flow.edges) == 1 and flow.edges[0]["source"] == "n1" and flow.edges[0]["target"] == "n2"

    wait_until(_edge_added, timeout=8000)
    expect(page.locator(".react-flow__edge")).to_have_count(1)


def test_selection_syncs_between_ui_and_python(page):
    flow = _make_flow()
    serve_component(page, flow)

    _node_locator(page, "Start").click(force=True)
    wait_until(lambda: "n1" in flow.selection["nodes"], timeout=8000)

    page.locator(".react-flow__edge-path").first.click(force=True)
    wait_until(lambda: "e1" in flow.selection["edges"], timeout=8000)


def test_patch_node_and_edge_labels_update_ui(page):
    flow = _make_flow()
    serve_component(page, flow)

    flow.nodes = [{**node, "label": "Start patched"} if node["id"] == "n1" else node for node in flow.nodes]
    flow.edges = [{**edge, "label": "Edge patched"} if edge["id"] == "e1" else edge for edge in flow.edges]

    expect(_node_locator(page, "Start patched")).to_have_count(1)
    expect(_edge_label_locator(page, "Edge patched")).to_have_count(1)


def test_programmatic_add_remove_nodes_edges(page):
    flow = _make_flow()
    serve_component(page, flow)

    flow.add_node(
        NodeSpec(
            id="n3",
            position={"x": 520, "y": 140},
            label="New Node",
            data={"status": "idle", "notes": "Gamma"},
        )
    )
    flow.add_edge(EdgeSpec(id="e2", source="n2", target="n3", label="Edge B"))

    expect(_node_locator(page, "New Node")).to_have_count(1)
    expect(_edge_label_locator(page, "Edge B")).to_have_count(1)

    flow.remove_edge("e2")
    flow.remove_node("n3")

    expect(_node_locator(page, "New Node")).to_have_count(0)
    expect(_edge_label_locator(page, "Edge B")).to_have_count(0)


def test_delete_node_reindexes_views(page):
    nodes = [
        NodeSpec(
            id="n1",
            position={"x": 0, "y": 0},
            label="Node A",
            data={},
        ).to_dict()
        | {"view": pn.pane.Markdown("View A")},
        NodeSpec(
            id="n2",
            position={"x": 260, "y": 60},
            label="Node B",
            data={},
        ).to_dict()
        | {"view": pn.pane.Markdown("View B")},
        NodeSpec(
            id="n3",
            position={"x": 520, "y": 120},
            label="Node C",
            data={},
        ).to_dict(),
    ]
    flow = ReactFlow(nodes=nodes, width=900, height=600)
    serve_component(page, flow)

    expect(_node_locator(page, "View A")).to_have_count(1)
    expect(_node_locator(page, "View B")).to_have_count(1)

    _node_locator(page, "Node A").click(force=True)
    page.keyboard.press("Backspace")

    wait_until(lambda: all(node["id"] != "n1" for node in flow.nodes), timeout=8000)

    expect(_node_locator(page, "View A")).to_have_count(0)
    expect(_node_locator(page, "Node B").filter(has_text="View B")).to_have_count(1)


def test_python_remove_node_reindexes_views(page):
    nodes = [
        NodeSpec(
            id="n1",
            position={"x": 0, "y": 0},
            label="Node A",
            data={},
        ).to_dict()
        | {"view": pn.pane.Markdown("View A")},
        NodeSpec(
            id="n2",
            position={"x": 260, "y": 60},
            label="Node B",
            data={},
        ).to_dict()
        | {"view": pn.pane.Markdown("View B")},
        NodeSpec(
            id="n3",
            position={"x": 520, "y": 120},
            label="Node C",
            data={},
        ).to_dict(),
    ]
    flow = ReactFlow(nodes=nodes, width=900, height=600)
    serve_component(page, flow)

    expect(_node_locator(page, "View A")).to_have_count(1)
    expect(_node_locator(page, "View B")).to_have_count(1)

    flow.remove_node("n1")

    wait_until(lambda: all(node["id"] != "n1" for node in flow.nodes), timeout=8000)

    expect(_node_locator(page, "View A")).to_have_count(0)
    expect(_node_locator(page, "Node B").filter(has_text="View B")).to_have_count(1)


def test_editor_renders_in_toolbar_mode(page):
    flow = _make_flow(editor_mode="toolbar")
    serve_component(page, flow)

    _node_locator(page, "Start").get_by_label("Show node toolbar").click()
    expect(page.locator(".jsoneditor").nth(0)).to_be_visible()
    expect(page.locator(".jsoneditor").nth(1)).not_to_be_visible()


def test_editor_renders_in_node_mode(page):
    flow = _make_flow(editor_mode="node")
    serve_component(page, flow)

    expect(page.locator(".jsoneditor").nth(0)).to_be_visible()
    expect(page.locator(".jsoneditor").nth(1)).to_be_visible()


def test_editor_renders_in_side_mode(page):
    flow = _make_flow(editor_mode="side")
    serve_component(page, flow)

    _node_locator(page, "Start").click()
    expect(page.locator(".jsoneditor").nth(0)).to_be_visible()
