"""UI tests for the frontend error boundary and safe mode recovery."""

import pytest
from panel.tests.util import serve_component, wait_until

from panel_reactflow import ReactFlow

pytest.importorskip("playwright")

from playwright.sync_api import expect

pytestmark = pytest.mark.ui


def _nodes(*, broken=False):
    """Two nodes, optionally with a position React Flow cannot render.

    A ``None`` position makes React Flow dereference ``position.x`` during
    render, which is the shape of corruption that used to leave the canvas
    permanently blank.
    """
    return [
        {"id": "n1", "position": None if broken else {"x": 0, "y": 0}, "label": "Start", "data": {}},
        {"id": "n2", "position": {"x": 260, "y": 60}, "label": "End", "data": {}},
    ]


def _edges(*, dangling=False):
    return [{"id": "e1", "source": "n1", "target": "missing" if dangling else "n2", "data": {}}]


def _flow(**params):
    params.setdefault("nodes", _nodes())
    params.setdefault("edges", _edges())
    params.setdefault("sizing_mode", "stretch_both")
    return ReactFlow(**params)


def test_healthy_flow_renders_without_recovery_ui(page) -> None:
    serve_component(page, _flow())

    expect(page.locator(".react-flow__node")).to_have_count(2)
    expect(page.locator(".rf-recovery")).to_have_count(0)
    expect(page.locator(".rf-safe-mode-banner")).to_have_count(0)


def test_render_error_recovers_into_safe_mode(page) -> None:
    """A node React Flow cannot render must degrade the view, not blank it."""
    flow = _flow()
    errors = []
    flow.on("client_error", errors.append)
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(2)

    flow.nodes = _nodes(broken=True)

    # Auto recovery remounts once, hits the same error, then retries in safe mode
    # where the invalid position is repaired to the origin.
    expect(page.locator(".rf-safe-mode-banner")).to_be_visible(timeout=20000)
    expect(page.locator(".react-flow__node")).to_have_count(2)
    expect(page.locator(".react-flow__edge")).to_have_count(1)
    expect(page.locator(".rf-recovery")).to_have_count(0)

    render_errors = [error for error in errors if error["source"] == "render"]
    assert [error["attempt"] for error in render_errors] == [1, 2]
    assert [error["mode"] for error in render_errors] == ["normal", "safe"]
    assert render_errors[0]["message"]
    assert render_errors[0]["stack"]
    assert render_errors[0]["component_stack"]

    wait_until(lambda: any(error["source"] == "safe_mode" for error in errors), page)
    issues = next(error for error in errors if error["source"] == "safe_mode")["issues"]
    assert [(issue["kind"], issue["id"], issue["action"]) for issue in issues] == [("invalid_position", "n1", "repaired")]


def test_safe_mode_hides_dangling_edge_without_deleting_it(page) -> None:
    flow = _flow()
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(2)

    flow.nodes = _nodes(broken=True)
    flow.edges = _edges(dangling=True)
    expect(page.locator(".rf-safe-mode-banner")).to_be_visible(timeout=20000)

    # The dangling edge is not rendered, but the server still holds it.
    expect(page.locator(".react-flow__edge")).to_have_count(0)
    assert len(flow.edges) == 1
    assert flow.edges[0]["target"] == "missing"

    page.locator(".rf-safe-mode-banner").get_by_text("Details").click()
    expect(page.locator(".rf-safe-mode-issues")).to_contain_text("missing")


def test_safe_mode_banner_can_be_dismissed(page) -> None:
    flow = _flow()
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(2)

    flow.nodes = _nodes(broken=True)
    banner = page.locator(".rf-safe-mode-banner")
    expect(banner).to_be_visible(timeout=20000)

    banner.get_by_text("Dismiss").click()
    expect(banner).to_have_count(0)
    expect(page.locator(".react-flow__node")).to_have_count(2)


def test_manual_mode_shows_recovery_panel_and_retry_works(page) -> None:
    flow = _flow(error_recovery="manual")
    errors = []
    flow.on("client_error", errors.append)
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(2)

    flow.nodes = _nodes(broken=True)

    panel = page.locator(".rf-recovery--failed")
    expect(panel).to_be_visible(timeout=20000)
    expect(panel).to_contain_text("still held on the server")
    # Manual mode reports the error but does not retry on its own.
    assert [error["auto_retry"] for error in errors if error["source"] == "render"] == [False]

    # Repairing the state on the server and retrying restores the canvas.
    flow.nodes = _nodes()
    panel.get_by_text("Try again").click()
    expect(page.locator(".react-flow__node")).to_have_count(2, timeout=20000)
    expect(page.locator(".rf-recovery")).to_have_count(0)


def test_recovery_panel_copies_diagnostics(page) -> None:
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    flow = _flow(error_recovery="manual")
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(2)

    flow.nodes = _nodes(broken=True)
    panel = page.locator(".rf-recovery--failed")
    expect(panel).to_be_visible(timeout=20000)

    panel.get_by_text("Copy details").click()
    expect(panel).to_contain_text("Copied")

    clipboard = page.evaluate("navigator.clipboard.readText()")
    assert "component_stack" in clipboard
    assert "user_agent" in clipboard


def test_error_recovery_off_does_not_intervene(page) -> None:
    """With the boundary disabled nothing is reported and nothing is recovered."""
    flow = _flow(error_recovery="off")
    errors = []
    flow.on("client_error", errors.append)
    serve_component(page, flow)
    expect(page.locator(".react-flow__node")).to_have_count(2)

    flow.nodes = _nodes(broken=True)

    expect(page.locator(".react-flow__node")).to_have_count(0, timeout=20000)
    expect(page.locator(".rf-recovery")).to_have_count(0)
    expect(page.locator(".rf-safe-mode-banner")).to_have_count(0)
    assert not errors
