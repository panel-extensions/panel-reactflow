"""UI tests for handle/edge click-to-inspect (value popup) feature."""

import panel as pn
import pytest
from panel.tests.util import serve_component, wait_until

from panel_reactflow import EdgeSpec, NodeSpec, NodeType, ReactFlow

pytest.importorskip("playwright")

from playwright.sync_api import expect

pytestmark = pytest.mark.ui

NODE_TYPES = {
    "source": NodeType(
        type="source",
        label="Source",
        outputs=[{"id": "out", "label": "Output records", "type": "DataFrame"}],
    ),
    "sink": NodeType(
        type="sink",
        label="Sink",
        inputs=[{"id": "in", "label": "Input records", "type": "DataFrame"}],
    ),
}


def _flow():
    nodes = [
        NodeSpec(id="src", type="source", position={"x": 0, "y": 100}, data={}).to_dict(),
        NodeSpec(id="snk", type="sink", position={"x": 300, "y": 100}, data={}).to_dict(),
    ]
    edges = [EdgeSpec(id="e1", source="src", target="snk", sourceHandle="out", targetHandle="in").to_dict()]
    return ReactFlow(
        nodes=nodes,
        edges=edges,
        node_types=NODE_TYPES,
        width=900,
        height=600,
    )


def test_handle_tooltip_shows_label_and_type(page):
    flow = _flow()
    serve_component(page, flow)

    # "source"/"sink" node types only declare one side of handles, so each
    # node also renders its unlabeled default handle on the other side;
    # target the one carrying the tooltip explicitly rather than `.first`.
    handle = page.locator(".react-flow__handle[data-tooltip]").first
    expect(handle).to_have_attribute("data-tooltip", "Output records (DataFrame)")


def test_handle_click_emits_event_with_direction(page):
    events = []
    flow = _flow()
    flow.on("handle_clicked", lambda payload: events.append(payload))
    serve_component(page, flow)

    output_handle = page.locator(".react-flow__handle-right").first
    output_handle.click()

    wait_until(lambda: len(events) == 1, timeout=8000)
    assert events[0]["node_id"] == "src"
    assert events[0]["handle_id"] == "out"
    assert events[0]["direction"] == "output"
    assert "position" in events[0]


def test_show_popup_renders_content_at_click(page):
    flow = _flow()

    def on_handle_clicked(payload, flow):
        flow.show_popup(pn.pane.Markdown("Value: 42"), payload["position"])

    flow.on("handle_clicked", on_handle_clicked)
    serve_component(page, flow)

    output_handle = page.locator(".react-flow__handle-right").first
    output_handle.click()

    popup = page.locator(".rf-value-popup")
    expect(popup).to_be_visible()
    expect(popup.locator("text=Value: 42")).to_be_visible()
    wait_until(lambda: flow._value_popup is not None, timeout=8000)


def test_popup_closes_on_pane_click(page):
    flow = _flow()
    flow.on("handle_clicked", lambda payload, flow: flow.show_popup(pn.pane.Markdown("Value: 42"), payload["position"]))
    serve_component(page, flow)

    page.locator(".react-flow__handle-right").first.click()
    popup = page.locator(".rf-value-popup")
    expect(popup).to_be_visible()

    pane = page.locator(".react-flow__pane")
    box = pane.bounding_box()
    page.mouse.click(box["x"] + box["width"] - 50, box["y"] + box["height"] - 50)

    expect(popup).not_to_be_visible()
    wait_until(lambda: flow._value_popup is None, timeout=8000)


def test_edge_click_emits_event(page):
    events = []
    flow = _flow()
    flow.on("edge_clicked", lambda payload: events.append(payload))
    serve_component(page, flow)

    edge = page.locator(".react-flow__edge-path").first
    edge.click(force=True)

    wait_until(lambda: len(events) == 1, timeout=8000)
    assert events[0]["edge_id"] == "e1"
    assert "position" in events[0]
