"""Tests for client-side error reporting and recovery."""

import logging

import pytest

from panel_reactflow import ReactFlow

RENDER_ERROR = {
    "type": "client_error",
    "source": "render",
    "name": "TypeError",
    "message": "Cannot read properties of null (reading 'x')",
    "stack": "TypeError: Cannot read properties of null\n    at Flow",
    "component_stack": "\n    at FlowInner\n    at FlowErrorBoundary",
    "attempt": 1,
    "mode": "normal",
    "auto_retry": True,
}

SAFE_MODE_REPORT = {
    "type": "client_error",
    "source": "safe_mode",
    "name": "SafeModeDegraded",
    "message": "Safe mode: repaired 1 element and hid 1 element that could not be rendered.",
    "issues": [
        {"kind": "dangling_edge", "id": "e1", "detail": "Connects a missing node (n1 -> nX)", "action": "dropped"},
        {
            "kind": "invalid_position",
            "id": "n2",
            "detail": "Position is not finite, reset to the origin",
            "action": "repaired",
        },
    ],
}


def test_error_recovery_default() -> None:
    assert ReactFlow().error_recovery == "auto"


def test_error_recovery_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        ReactFlow(error_recovery="retry-forever")


def test_client_error_emits_event() -> None:
    flow = ReactFlow()
    received = []
    flow.on("client_error", received.append)

    flow._handle_msg(dict(RENDER_ERROR))

    assert len(received) == 1
    assert received[0]["message"] == RENDER_ERROR["message"]
    assert received[0]["component_stack"] == RENDER_ERROR["component_stack"]


def test_client_error_reaches_wildcard_handler() -> None:
    flow = ReactFlow()
    received = []
    flow.on("*", received.append)

    flow._handle_msg(dict(RENDER_ERROR))

    assert [event["type"] for event in received] == ["client_error"]


def test_client_error_passes_flow_to_two_arg_callback() -> None:
    flow = ReactFlow()
    received = []
    flow.on("client_error", lambda payload, source: received.append((payload, source)))

    flow._handle_msg(dict(RENDER_ERROR))

    assert received[0][1] is flow


def test_client_error_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    flow = ReactFlow()
    with caplog.at_level(logging.ERROR, logger="panel.reactflow"):
        flow._handle_msg(dict(RENDER_ERROR))

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert RENDER_ERROR["message"] in record.getMessage()
    assert "attempt 1" in record.getMessage()


def test_safe_mode_report_logs_issues_as_warning(caplog: pytest.LogCaptureFixture) -> None:
    flow = ReactFlow()
    with caplog.at_level(logging.WARNING, logger="panel.reactflow"):
        flow._handle_msg(dict(SAFE_MODE_REPORT))

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert "Safe mode" in record.getMessage()
    assert "dangling_edge" in record.getMessage()
    assert "repaired" in record.getMessage()


def test_client_error_does_not_modify_graph() -> None:
    flow = ReactFlow(
        nodes=[{"id": "n1", "position": {"x": 0, "y": 0}}, {"id": "n2", "position": {"x": 100, "y": 0}}],
        edges=[{"id": "e1", "source": "n1", "target": "n2"}],
    )
    nodes_before = list(flow.nodes)
    edges_before = list(flow.edges)

    flow._handle_msg(dict(RENDER_ERROR))
    flow._handle_msg(dict(SAFE_MODE_REPORT))

    assert flow.nodes == nodes_before
    assert flow.edges == edges_before


def test_malformed_client_error_is_tolerated(caplog: pytest.LogCaptureFixture) -> None:
    flow = ReactFlow()
    received = []
    flow.on("client_error", received.append)

    with caplog.at_level(logging.ERROR, logger="panel.reactflow"):
        flow._handle_msg({"type": "client_error"})

    assert "Unknown error" in caplog.records[0].getMessage()
    assert len(received) == 1
