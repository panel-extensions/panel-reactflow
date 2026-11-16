"""Graph validation and callback error isolation."""

import datetime
import logging

import pytest

from panel_reactflow import Edge, Node, NodeType, ReactFlow


def _node(node_id, **kwargs):
    return {"id": node_id, "position": {"x": 0, "y": 0}, "data": {}, **kwargs}


def _capture_msgs(flow):
    msgs = []
    flow._send_msg = msgs.append
    return msgs


@pytest.fixture
def reactflow_log(caplog):
    # The ``panel`` logger does not propagate, so caplog's root handler would
    # never see panel.reactflow records.
    logger = logging.getLogger("panel.reactflow")
    logger.addHandler(caplog.handler)
    caplog.handler.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


# Duplicate ids and dangling or malformed edges


def test_add_node_rejects_duplicate_id():
    flow = ReactFlow(nodes=[_node("a")])
    with pytest.raises(ValueError, match="'a' already exists"):
        flow.add_node(_node("a"))
    assert [n["id"] for n in flow.nodes] == ["a"]
    assert list(flow._node_editors) == ["a"]


def test_failed_add_node_leaves_node_instance_detached():
    flow = ReactFlow(nodes=[_node("a")])
    node = Node(id="a")
    with pytest.raises(ValueError):
        flow.add_node(node)
    assert node.flow is None


def test_add_edge_rejects_unknown_endpoints():
    flow = ReactFlow(nodes=[_node("a")])
    with pytest.raises(ValueError, match="unknown target node 'ghost'"):
        flow.add_edge({"id": "e", "source": "a", "target": "ghost"})
    with pytest.raises(ValueError, match="unknown source node 'ghost'"):
        flow.add_edge(Edge(source="ghost", target="a"))
    assert flow.edges == []


def test_add_edge_missing_source_raises_value_error():
    flow = ReactFlow(nodes=[_node("a")])
    with pytest.raises(ValueError, match="Missing 'source'"):
        flow.add_edge({"target": "a"})


def test_add_edge_rejects_duplicate_id():
    flow = ReactFlow(nodes=[_node("a"), _node("b")], edges=[{"id": "e", "source": "a", "target": "b"}])
    with pytest.raises(ValueError, match="'e' already exists"):
        flow.add_edge({"id": "e", "source": "b", "target": "a"})
    assert len(flow.edges) == 1


def test_add_edge_allows_self_loops():
    flow = ReactFlow(nodes=[_node("a")])
    flow.add_edge({"id": "loop", "source": "a", "target": "a"})
    assert [e["id"] for e in flow.edges] == ["loop"]


def test_constructor_warns_on_conflicts(reactflow_log):
    flow = ReactFlow(
        nodes=[_node("a"), _node("a")],
        edges=[{"id": "e", "source": "a", "target": "ghost"}],
    )
    assert len(flow.nodes) == 2
    assert "'a' already exists" in reactflow_log.text
    assert "unknown target node 'ghost'" in reactflow_log.text


def test_constructor_rejects_malformed_edge():
    with pytest.raises(ValueError, match="Missing 'source'"):
        ReactFlow(nodes=[_node("a")], edges=[{"id": "e", "target": "a"}])


def test_from_networkx_rejects_ids_colliding_as_strings():
    nx = pytest.importorskip("networkx")
    graph = nx.DiGraph()
    graph.add_node(1)
    graph.add_node("1")
    with pytest.raises(ValueError, match="both map to the id '1'"):
        ReactFlow.from_networkx(graph)


def test_from_networkx_multigraph_keys_stay_unique():
    nx = pytest.importorskip("networkx")
    graph = nx.MultiDiGraph()
    graph.add_edge("a", "b")
    graph.add_edge("a", "b")
    graph.add_edge("b", "c")
    flow = ReactFlow.from_networkx(graph)
    ids = [e["id"] for e in flow.edges]
    assert len(set(ids)) == 3


# Non-JSON data


@pytest.mark.parametrize("bad", [{"o": object()}, {"s": {1, 2}}, {"d": datetime.date(2024, 1, 1)}])
def test_add_node_rejects_non_json_data(bad):
    flow = ReactFlow(nodes=[_node("a")])
    with pytest.raises(ValueError, match="not JSON-serializable"):
        flow.add_node(_node("b", data=bad))
    assert [n["id"] for n in flow.nodes] == ["a"]
    flow.add_node(_node("c"))
    assert [n["id"] for n in flow.nodes] == ["a", "c"]


def test_add_node_with_view_is_not_json_checked():
    pn = pytest.importorskip("panel")
    flow = ReactFlow()
    flow.add_node(_node("a", view=pn.pane.Markdown("hi")))
    assert len(flow.nodes) == 1


def test_add_edge_rejects_non_json_data():
    flow = ReactFlow(nodes=[_node("a"), _node("b")])
    with pytest.raises(ValueError, match="not JSON-serializable"):
        flow.add_edge({"source": "a", "target": "b", "data": {"o": object()}})
    assert flow.edges == []


def test_patches_reject_non_json_values():
    flow = ReactFlow(nodes=[_node("a", data={"v": 1}), _node("b")], edges=[{"id": "e", "source": "a", "target": "b", "data": {}}])
    msgs = _capture_msgs(flow)
    with pytest.raises(ValueError, match="not JSON-serializable"):
        flow.patch_node_data("a", {"v": object()})
    with pytest.raises(ValueError, match="not JSON-serializable"):
        flow.patch_edge_data("e", {"v": object()})
    with pytest.raises(ValueError, match="not JSON-serializable"):
        flow.patch_node_props("a", {"style": {"color": object()}})
    assert flow.nodes[0]["data"] == {"v": 1}
    assert msgs == []


def test_constructor_rejects_non_json_data():
    with pytest.raises(ValueError, match="not JSON-serializable"):
        ReactFlow(nodes=[_node("a", data={"o": object()})])


# Callback isolation


def test_raising_callback_does_not_stop_other_callbacks(reactflow_log):
    flow = ReactFlow(nodes=[_node("a")])
    seen = []
    flow.on("node_added", lambda p: 1 / 0)
    flow.on("node_added", lambda p: seen.append(("specific", p["node"]["id"])))
    flow.on("*", lambda p: seen.append(("wildcard", p["type"])))
    flow.add_node(_node("b"))
    assert seen == [("specific", "b"), ("wildcard", "node_added")]
    assert "ZeroDivisionError" in reactflow_log.text


def test_remove_node_reports_every_id_when_a_callback_raises(reactflow_log):
    deleted = []

    class Hooked(Node):
        def on_delete(self, payload, flow):
            deleted.append(self.id)

    flow = ReactFlow(nodes=[Hooked(id="h1"), Hooked(id="h2")])
    calls = []

    def bad(payload):
        calls.append(payload["node_id"])
        raise RuntimeError("boom")

    flow.on("node_deleted", bad)
    flow._handle_msg({"type": "node_deleted", "node_ids": ["h1", "h2"]})
    assert flow.nodes == []
    assert calls == ["h1", "h2"]
    assert deleted == ["h1", "h2"]
    assert "boom" in reactflow_log.text


def test_raising_hook_is_logged(reactflow_log):
    class Bad(Node):
        def on_move(self, payload, flow):
            raise RuntimeError("hook bug")

        def on_event(self, payload, flow):
            events.append(payload["type"])

    events = []
    flow = ReactFlow(nodes=[Bad(id="a")])
    flow._handle_msg({"type": "node_moved", "node_id": "a", "position": {"x": 1, "y": 2}})
    assert events == ["node_moved"]
    assert "hook bug" in reactflow_log.text


def test_raising_drop_handler_is_isolated(reactflow_log):
    flow = ReactFlow(drop_types=["text/plain"])
    order = []

    def boom(payload):
        order.append("boom")
        raise RuntimeError("handler bug")

    flow.on("drop", boom)
    flow.on("drop", lambda p: order.append("second"))
    flow.on("*", lambda p: order.append("wildcard"))
    flow._handle_msg({"type": "drop", "drop_type": "text/plain", "data": "x"})
    assert order == ["boom", "second", "wildcard"]
    assert "handler bug" in reactflow_log.text


@pytest.mark.parametrize(
    "callback_factory",
    [
        lambda seen: lambda payload, flow, extra=None: seen.append(flow),
        lambda seen: lambda *args: seen.append(args[1]),
    ],
    ids=["optional-third-arg", "varargs"],
)
def test_event_callbacks_receive_flow_when_they_accept_it(callback_factory):
    flow = ReactFlow()
    seen = []
    flow.on("node_added", callback_factory(seen))
    flow.add_node(_node("a"))
    assert seen == [flow]


# Connection validation requests


def _typed_flow(**kwargs):
    flow = ReactFlow(node_types={"t": NodeType(type="t", inputs=["in"], outputs=["out"])}, **kwargs)
    for node_id in "ab":
        flow.add_node(_node(node_id, type="t"))
    return flow


def _request(**kwargs):
    return {"type": "connection_validation_requested", "request_id": 1, "node_id": "a", "handle_id": "out", "handle_type": "source", **kwargs}


@pytest.mark.parametrize("missing", ["node_id", "handle_type"])
def test_malformed_connection_request_still_replies(missing):
    flow = _typed_flow()
    flow.add_connection_validator(lambda p: None)
    msgs = _capture_msgs(flow)
    msg = _request()
    del msg[missing]
    flow._handle_msg(msg)
    assert msgs == [{"type": "connection_validation_result", "request_id": 1, "results": [], "error": "Malformed connection validation request."}]


def test_connection_request_without_handle_id_uses_default_handle():
    flow = _typed_flow()
    flow.add_connection_validator(lambda p: None)
    msgs = _capture_msgs(flow)
    msg = _request()
    del msg["handle_id"]
    flow._handle_msg(msg)
    assert len(msgs[0]["results"]) == 2
    assert "error" not in msgs[0]


def test_connection_request_failure_sends_error(monkeypatch, reactflow_log):
    flow = _typed_flow()
    flow.add_connection_validator(lambda p: None)
    msgs = _capture_msgs(flow)

    def broken(*args):
        raise RuntimeError("broken")

    monkeypatch.setattr(flow, "_connection_candidates", broken)
    flow._handle_msg(_request())
    assert msgs[0]["error"] == "Connection validation failed."
    assert msgs[0]["request_id"] == 1


def test_validator_with_optional_third_argument_receives_flow():
    flow = _typed_flow()
    seen = []

    def validate(payload, flow, extra=None):
        seen.append(flow)

    flow.add_connection_validator(validate)
    msgs = _capture_msgs(flow)
    flow._handle_msg(_request())
    assert [r["reason"] for r in msgs[0]["results"]] == [None, None]
    assert seen == [flow, flow]


def test_single_string_handle_is_not_split_into_characters():
    flow = ReactFlow(node_types={"t": {"type": "t", "inputs": "in", "outputs": ["out"]}})
    flow.add_node(_node("a", type="t"))
    flow.add_connection_validator(lambda p: None)
    msgs = _capture_msgs(flow)
    flow._handle_msg(_request())
    assert [r["handle_id"] for r in msgs[0]["results"]] == ["in"]
    assert flow.node_types["t"]["inputs"] == ["in"]


def test_connection_request_skips_unconnectable_candidates():
    flow = ReactFlow(
        node_types={
            "t": NodeType(type="t", inputs=["in"], outputs=["out"]),
            "closed": NodeType(type="closed", inputs=["in"], input_connectable_end=False),
        }
    )
    flow.add_node(_node("a", type="t"))
    flow.add_node(_node("b", type="t", connectable=False))
    flow.add_node(_node("c", type="closed"))
    flow.add_node(_node("d", type="t"))
    seen = []
    flow.add_connection_validator(lambda p: seen.append(p["target"]))
    msgs = _capture_msgs(flow)
    flow._handle_msg(_request())
    assert [r["node_id"] for r in msgs[0]["results"]] == ["a", "d"]
    assert seen == ["a", "d"]


# Edges connected in the browser


def test_browser_edge_to_unknown_node_is_undone(reactflow_log):
    flow = _typed_flow()
    msgs = _capture_msgs(flow)
    flow._handle_msg({"type": "edge_added", "edge": {"id": "e", "source": "a", "target": "ghost"}})
    assert flow.edges == []
    assert msgs == [{"type": "edge_rejected", "edge_id": "e", "reason": "Edge 'e' references unknown target node 'ghost'."}]
    assert "rejected edge 'e'" in reactflow_log.text


def test_browser_edge_failing_schema_validation_is_undone():
    from panel_reactflow import EdgeType

    flow = _typed_flow(
        edge_types={"w": EdgeType(type="w", schema={"type": "object", "properties": {"w": {"type": "number"}}, "required": ["w"]})},
        validate_on_add=True,
    )
    pytest.importorskip("jsonschema")
    msgs = _capture_msgs(flow)
    flow._handle_msg({"type": "edge_added", "edge": {"id": "e", "source": "a", "target": "b", "type": "w", "data": {}}})
    assert flow.edges == []
    assert msgs[0]["type"] == "edge_rejected"


def test_browser_edge_accepted_by_validators_is_added():
    flow = _typed_flow()
    flow.add_connection_validator(lambda p, flow: None)
    msgs = _capture_msgs(flow)
    flow._handle_msg({"type": "edge_added", "edge": {"id": "e", "source": "a", "target": "b", "sourceHandle": "out", "targetHandle": "in"}})
    assert [e["id"] for e in flow.edges] == ["e"]
    assert msgs == []


def test_browser_self_loop_is_kept_without_validators():
    flow = _typed_flow()
    flow._handle_msg({"type": "edge_added", "edge": {"id": "loop", "source": "a", "target": "a"}})
    assert [e["id"] for e in flow.edges] == ["loop"]
