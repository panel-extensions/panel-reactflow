"""Tests for ReactFlow model creation."""

from panel.pane import Markdown
from panel.viewable import Viewer

from panel_reactflow import ReactFlow


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


def test_view_idx_updates_on_remove_node(document, comm) -> None:
    flow = ReactFlow()
    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "data": {}, "view": Markdown("A")})
    flow.add_node({"id": "n2", "position": {"x": 1, "y": 1}, "data": {}, "view": Markdown("B")})
    flow.add_node({"id": "n3", "position": {"x": 2, "y": 2}, "data": {}})

    model = flow.get_root(document, comm=comm)

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


def test_reactflow_add_node_dynamically_creates_views(document, comm):
    flow = ReactFlow()
    model = flow.get_root(document, comm=comm)
    assert model.children == [
        "_views",
        "_node_editor_views",
        "_edge_editor_views",
        "top_panel",
        "bottom_panel",
        "left_panel",
        "right_panel",
    ]

    flow.add_node({"id": "n1", "position": {"x": 0, "y": 0}, "label": "Viewer Node", "data": {}, "view": Markdown("foo")})

    assert len(model.data._views) == 1
    assert len(model.data._node_editor_views) == 1
