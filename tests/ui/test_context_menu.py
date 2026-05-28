"""UI tests for node context menu feature."""

import panel as pn
import param
import pytest
from panel.tests.util import serve_component, wait_until

from panel_reactflow import Node, NodeSpec, ReactFlow

pytest.importorskip("playwright")

from playwright.sync_api import expect

pytestmark = pytest.mark.ui


class MenuNode(Node):
    status = param.Selector(default="idle", objects=["idle", "running", "done"], precedence=0)

    def context_menu(self):
        return pn.Column(
            pn.pane.Markdown(f"**Menu: {self.label}**"),
            pn.widgets.Button(label="Run"),
            pn.widgets.Button(label="Delete"),
        )


class NoMenuNode(Node):
    pass


def _node_locator(page, label):
    return page.locator(".react-flow__node").filter(has_text=label)


def test_context_menu_appears_on_right_click(page):
    flow = ReactFlow(
        nodes=[MenuNode(id="n1", position={"x": 0, "y": 0}, label="Task A", data={})],
        width=900,
        height=600,
    )
    serve_component(page, flow)

    node = _node_locator(page, "Task A")
    expect(node).to_be_visible()

    node.click(button="right")

    menu = page.locator(".rf-context-menu")
    expect(menu).to_be_visible()
    expect(menu.locator("text=Menu: Task A")).to_be_visible()

    wait_until(lambda: flow._context_menu is not None, timeout=8000)
    wait_until(lambda: flow._context_menu_position is not None, timeout=8000)


def test_context_menu_closes_on_pane_click(page):
    flow = ReactFlow(
        nodes=[MenuNode(id="n1", position={"x": 0, "y": 0}, label="Task A", data={})],
        width=900,
        height=600,
    )
    serve_component(page, flow)

    node = _node_locator(page, "Task A")
    node.click(button="right")

    menu = page.locator(".rf-context-menu")
    expect(menu).to_be_visible()

    pane = page.locator(".react-flow__pane")
    box = pane.bounding_box()
    page.mouse.click(box["x"] + box["width"] - 50, box["y"] + box["height"] - 50)

    expect(menu).not_to_be_visible()
    wait_until(lambda: flow._context_menu is None, timeout=8000)


def test_context_menu_not_shown_for_node_without_menu(page):
    flow = ReactFlow(
        nodes=[NoMenuNode(id="n1", position={"x": 0, "y": 0}, label="Plain", data={})],
        width=900,
        height=600,
    )
    serve_component(page, flow)

    node = _node_locator(page, "Plain")
    expect(node).to_be_visible()

    node.click(button="right")

    page.wait_for_timeout(500)
    menu = page.locator(".rf-context-menu")
    expect(menu).not_to_be_visible()
    assert flow._context_menu is None


def test_context_menu_not_shown_for_dict_node(page):
    flow = ReactFlow(
        nodes=[NodeSpec(id="n1", position={"x": 0, "y": 0}, label="Dict Node", data={}).to_dict()],
        width=900,
        height=600,
    )
    serve_component(page, flow)

    node = _node_locator(page, "Dict Node")
    expect(node).to_be_visible()

    node.click(button="right")

    page.wait_for_timeout(500)
    menu = page.locator(".rf-context-menu")
    expect(menu).not_to_be_visible()
    assert flow._context_menu is None


def test_context_menu_emits_event(page):
    events = []

    flow = ReactFlow(
        nodes=[MenuNode(id="n1", position={"x": 0, "y": 0}, label="Task A", data={})],
        width=900,
        height=600,
    )
    flow.on("node_context_menu", lambda payload: events.append(payload))
    serve_component(page, flow)

    node = _node_locator(page, "Task A")
    node.click(button="right")

    wait_until(lambda: len(events) == 1, timeout=8000)
    assert events[0]["node_id"] == "n1"
    assert "position" in events[0]


def test_context_menu_updates_on_different_node(page):
    flow = ReactFlow(
        nodes=[
            MenuNode(id="n1", position={"x": 0, "y": 0}, label="First"),
            MenuNode(id="n2", position={"x": 300, "y": 0}, label="Second"),
        ],
        width=900,
        height=600,
    )
    serve_component(page, flow)

    _node_locator(page, "First").click(button="right")
    menu = page.locator(".rf-context-menu")
    expect(menu.locator("text=Menu: First")).to_be_visible()

    _node_locator(page, "Second").click(button="right")
    expect(menu.locator("text=Menu: Second")).to_be_visible()
