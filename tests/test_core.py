"""Tests for ReactFlow model creation."""

import param
from panel.io.state import state
from panel.pane import Markdown
from panel.viewable import Viewer
from panel_material_ui.theme import MaterialDesign

from panel_reactflow import Edge, EdgeType, Node, NodeType, ReactFlow


def test_reactflow_add_node_with_arbitrary_object(document, comm) -> None:
    """Test that arbitrary objects (e.g., HoloViews) work as node views via pn.panel().

    This addresses issue #13 where objects without __panel__() method
    (like HoloViews Curve objects) would raise AttributeError.
    """

    class MockPlot:
        """Mock object simulating HoloViews/hvplot objects (no __panel__ method)."""

        def __repr__(self):
            return "MockPlot(data)"

    flow = ReactFlow()
    mock_plot = MockPlot()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "label": "Plot Node", "data": {}, "view": mock_plot})

    # This should not raise AttributeError about '_models'
    # The object should be converted via pn.panel()
    _ = flow.get_root(document, comm=comm)
    assert len(flow.nodes) == 1
    assert flow.nodes[0]["id"] == "n1"


def test_view_idx_updates_on_remove_node(flow_model) -> None:
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}, "view": Markdown("A")})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}, "view": Markdown("B")})
    flow.add_node({"id": "n3", "position": {"x": 2, "y": 2}, "data": {}})

    model = flow_model(flow)

    flow.remove_node("n1")

    remaining = {node["id"]: node for node in model.data.nodes}
    assert remaining["n2"]["data"]["view_idx"] == 0
    assert remaining["n3"]["data"].get("view_idx") is None


def test_reactflow_add_node_with_viewer(document, comm) -> None:
    """Test that Viewer objects with __panel__() method work as node views."""

    class MyViewer(Viewer):
        def __panel__(self):
            return Markdown("Hello from Viewer!")

    flow = ReactFlow()
    my_viewer = MyViewer()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "label": "Viewer Node", "data": {}, "view": my_viewer})

    # This should not raise AttributeError about '_models'
    _ = flow.get_root(document, comm=comm)
    assert len(flow.nodes) == 1
    assert flow.nodes[0]["id"] == "n1"


def test_reactflow_add_node_dynamically_creates_views(flow_model):
    flow = ReactFlow()
    model = flow_model(flow)
    assert model.children == [
        "_views",
        "_node_editor_views",
        "top_panel",
        "bottom_panel",
        "left_panel",
        "right_panel",
        "_context_menu",
        "_value_popup",
        "_selected_editor",
    ]

    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "label": "Viewer Node", "data": {}, "view": Markdown("foo")})

    assert len(model.data._views) == 1
    # Toolbar editors are only built once the frontend opens the toolbar.
    assert len(model.data._node_editor_views) == 0

    flow._handle_msg({"type": "editor_requested", "node_id": "n1"})

    assert len(model.data._node_editor_views) == 1
    assert model.data._node_editor_ids == ["n1"]


def test_bokeh_children_initialize_for_object_views_and_editors(flow_model) -> None:
    class ViewNode(Node):
        def __panel__(self):
            return Markdown("Node view content")

        def editor(self, data, schema, *, id, type, on_patch):
            return Markdown(f"Node editor {id}")

    class EditorEdge(Edge):
        def editor(self, data, schema, *, id, type, on_patch):
            return Markdown(f"Edge editor {id}")

    flow = ReactFlow(
        nodes=[
            ViewNode(id="n1", position={"x": 0, "y": 0}, data={}),
            {"id": "n2", "position": {"x": 150, "y": 0}, "data": {}},
        ],
        edges=[EditorEdge(id="e1", source="n1", target="n2", data={})],
        editor_mode="node",
    )

    model = flow_model(flow)
    assert model.children == [
        "_views",
        "_node_editor_views",
        "top_panel",
        "bottom_panel",
        "left_panel",
        "right_panel",
        "_context_menu",
        "_value_popup",
        "_selected_editor",
    ]
    assert len(model.data._views) == 1
    assert len(model.data._node_editor_views) == 2
    assert model.data._node_editor_ids == ["n1", "n2"]

    by_id = {node["id"]: node for node in model.data.nodes}
    assert by_id["n1"]["data"]["view_idx"] == 0
    assert by_id["n2"]["data"].get("view_idx") is None


class _Config(param.Parameterized):
    x = param.Integer(default=1)


def test_updated_node_types_reach_model_as_descriptors(flow_model) -> None:
    flow = ReactFlow()
    model = flow_model(flow)
    synced = []
    model.data.on_change("node_types", lambda attr, old, new: synced.append(new))

    flow.node_types = {"a": NodeType(type="a", schema=_Config)}

    assert synced
    assert all(isinstance(value["a"], dict) for value in synced)
    assert "x" in model.data.node_types["a"]["schema"]["properties"]


def test_node_and_edge_types_updated_together(flow_model) -> None:
    flow = ReactFlow()
    model = flow_model(flow)

    flow.param.update(
        node_types={"a": NodeType(type="a", schema=_Config)},
        edge_types={"e": EdgeType(type="e", schema=_Config)},
    )

    assert isinstance(flow.node_types["a"], dict)
    assert isinstance(flow.edge_types["e"], dict)
    assert isinstance(model.data.node_types["a"], dict)
    assert isinstance(model.data.edge_types["e"], dict)


def test_design_wrapped_root_routes_updates_to_flow_model(document, flow_model) -> None:
    flow = ReactFlow(design=MaterialDesign)
    model = flow_model(flow)
    (ref,) = flow._models
    root = state._views[ref][1]

    assert root is not model
    assert root.data.__class__.__name__.startswith("MaterialWrapper")
    assert model.data.__class__.__name__.startswith("ReactFlow")

    sent = []
    document.callbacks.send_event = sent.append
    flow._send_msg({"type": "ping"})
    assert [event.model for event in sent] == [model]

    flow.nodes = [{"id": "n1", "position": {"x": 0, "y": 0}, "data": {}}]
    assert [node["id"] for node in model.data.nodes] == ["n1"]
