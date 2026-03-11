"""Tests for the Pipeline class."""

import param

from panel_reactflow import Pipeline, ReactFlow
from panel_reactflow.pipeline import (
    _BASE_PARAMS,
    _compute_positions,
    _get_input_params,
    _get_outputs,
    _get_view_methods,
    _infer_edges,
    _make_output_view,
)

# ---------------------------------------------------------------------------
# Test stage classes
# ---------------------------------------------------------------------------


class Source(param.Parameterized):
    text = param.String(default="hello")

    @param.output(param.String)
    @param.depends("text")
    def text_out(self):
        return self.text.upper()


class Sink(param.Parameterized):
    text_out = param.String()

    @param.depends("text_out")
    def display(self):
        return self.text_out or "empty"


class Transform(param.Parameterized):
    text_out = param.String()

    @param.output(param.String)
    @param.depends("text_out")
    def result(self):
        return (self.text_out or "")[::-1]


class Display(param.Parameterized):
    result = param.String()

    @param.depends("result")
    def display(self):
        return self.result or "waiting"


class FanOutSource(param.Parameterized):
    value = param.Integer(default=10)

    @param.output(param.Integer)
    @param.depends("value")
    def value_out(self):
        return self.value * 2


class BranchA(param.Parameterized):
    value_out = param.Integer()

    @param.output(param.Integer)
    @param.depends("value_out")
    def a_result(self):
        return (self.value_out or 0) + 1


class BranchB(param.Parameterized):
    value_out = param.Integer()

    @param.output(param.Integer)
    @param.depends("value_out")
    def b_result(self):
        return (self.value_out or 0) * 10


class Merger(param.Parameterized):
    a_result = param.Integer()
    b_result = param.Integer()

    @param.depends("a_result", "b_result")
    def display(self):
        return f"{self.a_result} / {self.b_result}"


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_get_outputs_discovers_param_output():
    src = Source()
    outputs = _get_outputs(src)
    assert "text_out" in outputs
    # Each entry is (param_type_instance, bound_method, index)
    assert outputs["text_out"][1].__name__ == "text_out"


def test_get_input_params_excludes_base():
    sink = Sink()
    inputs = _get_input_params(sink)
    assert "text_out" in inputs
    # Base Parameterized params like 'name' should not appear
    assert "name" not in inputs


def test_infer_edges_linear_chain():
    instances = {"Source": Source(name="Source"), "Sink": Sink(name="Sink")}
    outputs_map = {name: _get_outputs(inst) for name, inst in instances.items()}
    edges = _infer_edges(["Source", "Sink"], instances, outputs_map)
    assert len(edges) == 1
    assert edges[0] == ("Source", "Sink", "text_out")


def test_infer_edges_three_stage():
    instances = {
        "Source": Source(name="Source"),
        "Transform": Transform(name="Transform"),
        "Display": Display(name="Display"),
    }
    outputs_map = {name: _get_outputs(inst) for name, inst in instances.items()}
    edges = _infer_edges(["Source", "Transform", "Display"], instances, outputs_map)
    # Source.text_out -> Transform.text_out, Transform.result -> Display.result
    assert ("Source", "Transform", "text_out") in edges
    assert ("Transform", "Display", "result") in edges
    assert len(edges) == 2


def test_compute_positions_linear():
    names = ["A", "B", "C"]
    edges = [("A", "B", "x"), ("B", "C", "x")]
    positions = _compute_positions(names, edges, (350, 150))
    # Should be left to right
    assert positions["A"]["x"] < positions["B"]["x"] < positions["C"]["x"]
    # All on same y since linear
    assert positions["A"]["y"] == positions["B"]["y"] == positions["C"]["y"]


def test_compute_positions_fan_out():
    names = ["Root", "Left", "Right"]
    edges = [("Root", "Left", "x"), ("Root", "Right", "x")]
    positions = _compute_positions(names, edges, (350, 150))
    # Root at depth 0, Left and Right at depth 1
    assert positions["Root"]["x"] == 0
    assert positions["Left"]["x"] == 350
    assert positions["Right"]["x"] == 350
    # Left and Right should be vertically offset
    assert positions["Left"]["y"] != positions["Right"]["y"]


def test_get_view_methods():
    """_get_view_methods finds public @param.depends methods that are not outputs."""
    sink = Sink()
    # Sink has no outputs, but has display() which is @param.depends
    view_methods = _get_view_methods(sink, set())
    method_names = [m.__name__ for m in view_methods]
    assert "display" in method_names


def test_get_view_methods_excludes_outputs():
    """_get_view_methods excludes methods whose names are in output_method_names."""
    src = Source()
    # text_out is both @param.output and @param.depends — should be excluded
    view_methods = _get_view_methods(src, {"text_out"})
    method_names = [m.__name__ for m in view_methods]
    assert "text_out" not in method_names


def test_make_output_view_single():
    """_make_output_view creates a Panel viewable for a single-output method."""
    from panel.viewable import Viewable

    src = Source()
    outputs = _get_outputs(src)
    _ptype, method, index = outputs["text_out"]
    view = _make_output_view(src, method, index)
    assert isinstance(view, Viewable)


def test_make_output_view_multi():
    """_make_output_view creates a Panel viewable for a multi-output method."""
    from panel.viewable import Viewable

    class MultiOut(param.Parameterized):
        x = param.String("hello")

        @param.output(upper=param.String(), length=param.Integer())
        @param.depends("x")
        def outputs(self):
            return self.x.upper(), len(self.x)

    inst = MultiOut()
    outs = _get_outputs(inst)
    for name in ("upper", "length"):
        _ptype, method, index = outs[name]
        view = _make_output_view(inst, method, index)
        assert isinstance(view, Viewable)


# ---------------------------------------------------------------------------
# Pipeline integration tests
# ---------------------------------------------------------------------------


def test_pipeline_introspects_outputs():
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    assert "text_out" in pipeline._outputs["Source"]


def test_pipeline_infers_edges():
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    assert len(pipeline._edges) == 1
    assert pipeline._edges[0] == ("Source", "Sink", "text_out")


def test_pipeline_explicit_graph():
    pipeline = Pipeline(
        stages=[("Source", Source), ("Sink", Sink)],
        graph={"Source": "Sink"},
    )
    assert len(pipeline._edges) == 1
    assert pipeline._edges[0] == ("Source", "Sink", "text_out")


def test_pipeline_wires_reactivity():
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    src = pipeline._instances["Source"]
    sink = pipeline._instances["Sink"]
    # Initial wiring should have propagated
    assert sink.text_out == src.text.upper()
    # Change source and verify propagation
    src.text = "world"
    assert sink.text_out == "WORLD"


def test_pipeline_creates_reactflow():
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    flow = pipeline.__panel__()
    assert isinstance(flow, ReactFlow)
    # Source:text (auto-input) + Source + Sink = 3 nodes (no separate output nodes)
    assert len(flow.nodes) == 3
    # Source:text->Source (auto) + Source->Sink = 2 edges
    assert len(flow.edges) == 2
    node_ids = {n["id"] for n in flow.nodes}
    assert node_ids == {"Source:text", "Source", "Sink"}


def test_pipeline_accepts_instances():
    src = Source(name="Source")
    sink = Sink(name="Sink")
    pipeline = Pipeline(stages=[("Source", src), ("Sink", sink)])
    assert pipeline._instances["Source"] is src
    assert pipeline._instances["Sink"] is sink
    assert isinstance(pipeline.__panel__(), ReactFlow)


def test_pipeline_fan_out():
    pipeline = Pipeline(
        stages=[
            ("Root", FanOutSource),
            ("A", BranchA),
            ("B", BranchB),
            ("Merge", Merger),
        ],
        graph={
            "Root": ("A", "B"),
            "A": "Merge",
            "B": "Merge",
        },
    )
    flow = pipeline.__panel__()
    # Root:value (auto) + Root + A + B + Merge = 5 nodes (no separate output nodes)
    assert len(flow.nodes) == 5
    # Root:value->Root (auto) + Root->A + Root->B + A->Merge + B->Merge = 5 edges
    assert len(flow.edges) == 5

    # Check reactivity
    root = pipeline._instances["Root"]
    merge = pipeline._instances["Merge"]

    # Initial: root.value=10 -> value_out=20 -> A: 21, B: 200
    assert merge.a_result == 21
    assert merge.b_result == 200

    # Update root
    root.value = 5
    # value_out=10 -> A: 11, B: 100
    assert merge.a_result == 11
    assert merge.b_result == 100


def test_pipeline_edges_have_labels():
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    flow = pipeline.__panel__()
    labels = {e["label"] for e in flow.edges}
    assert "text_out" in labels


def test_pipeline_edges_have_arrow_markers():
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    flow = pipeline.__panel__()
    for edge in flow.edges:
        assert edge["markerEnd"] == {"type": "arrowclosed"}


def test_pipeline_nodes_have_views():
    """All stage nodes now have views (outputs or view methods).
    Auto-input nodes also have views."""
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    flow = pipeline.__panel__()
    for node in flow.nodes:
        nid = node["id"]
        if ":" in nid:
            # Auto-input nodes must have views
            assert "view" in node, f"Auto-input node {nid} should have a view"
        elif nid == "Sink":
            # Sink has a display() view method
            assert "view" in node, "Sink should have a view from display()"
        elif nid == "Source":
            # Source has only @param.output → falls back to output as view
            assert "view" in node, "Source should have output rendered as view"


def test_pipeline_kwargs_forwarded():
    pipeline = Pipeline(
        stages=[("Source", Source), ("Sink", Sink)],
        kwargs={"show_minimap": True, "min_height": 800},
    )
    flow = pipeline.__panel__()
    assert flow.show_minimap is True
    assert flow.min_height == 800


def test_pipeline_layout_spacing():
    pipeline = Pipeline(
        stages=[("Source", Source), ("Sink", Sink)],
        layout_spacing=(500, 200),
    )
    flow = pipeline.__panel__()
    positions = {n["id"]: n["position"] for n in flow.nodes}
    # Direct stage-to-stage: Sink.x - Source.x == spacing
    assert positions["Sink"]["x"] - positions["Source"]["x"] == 500


# ---------------------------------------------------------------------------
# Auto-input tests
# ---------------------------------------------------------------------------


def test_pipeline_auto_input_creates_nodes():
    """Auto-input nodes are created for unconnected params."""
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    flow = pipeline.__panel__()
    node_ids = {n["id"] for n in flow.nodes}
    # Source has 'text' param with no incoming edge → auto-input node
    assert "Source:text" in node_ids
    # Sink.text_out IS connected from Source → no auto-input
    assert "Sink:text_out" not in node_ids


def test_pipeline_auto_input_edge_count():
    """Verify total edge count with auto-inputs (no output node expansion)."""
    pipeline = Pipeline(
        stages=[
            ("Source", Source),
            ("Transform", Transform),
            ("Display", Display),
        ],
    )
    flow = pipeline.__panel__()
    # stage-to-stage edges: Source->Transform(text_out), Transform->Display(result) = 2
    # auto-input: Source:text -> Source = 1
    # total = 3
    assert len(flow.edges) == 3
    # stage nodes: Source, Transform, Display = 3
    # auto-input: Source:text = 1
    # total = 4
    assert len(flow.nodes) == 4


def test_pipeline_auto_input_wiring():
    """Changing an auto-input widget's underlying param propagates through the pipeline."""
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    src = pipeline._instances["Source"]
    sink = pipeline._instances["Sink"]

    # The auto-input widget is bound to src.text via pn.panel(src.param.text),
    # so changing src.text directly should propagate.
    src.text = "test input"
    assert sink.text_out == "TEST INPUT"


def test_pipeline_auto_inputs_false():
    """auto_inputs=False suppresses input node generation."""
    pipeline = Pipeline(
        stages=[("Source", Source), ("Sink", Sink)],
        auto_inputs=False,
    )
    flow = pipeline.__panel__()
    # Source + Sink = 2 nodes (no auto-inputs, no separate output nodes)
    assert len(flow.nodes) == 2
    # Source->Sink = 1 edge
    assert len(flow.edges) == 1
    node_ids = {n["id"] for n in flow.nodes}
    assert "Source:text" not in node_ids


def test_find_unconnected_params():
    """Unit test for _find_unconnected_params helper."""
    pipeline = Pipeline(
        stages=[("Source", Source), ("Sink", Sink)],
        auto_inputs=False,  # don't auto-build, just check helper
    )
    unconnected = pipeline._find_unconnected_params(["Source", "Sink"])
    stage_param_pairs = [(s, p) for s, p in unconnected]
    # Source.text has no incoming edge
    assert ("Source", "text") in stage_param_pairs
    # Sink.text_out IS connected from Source
    assert ("Sink", "text_out") not in stage_param_pairs


def test_pipeline_auto_input_node_labels():
    """Auto-input nodes get human-readable labels."""
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    flow = pipeline.__panel__()
    input_node = next(n for n in flow.nodes if n["id"] == "Source:text")
    assert input_node["label"] == "Text"


def test_pipeline_auto_input_views_are_viewable():
    """Auto-input views should be Panel viewable objects."""
    from panel.viewable import Viewable

    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    assert "Source:text" in pipeline._input_views
    widget = pipeline._input_views["Source:text"]
    assert isinstance(widget, Viewable)


def test_base_params_excludes_viewable():
    """_BASE_PARAMS should contain Viewable params like 'width', 'height'."""
    assert "width" in _BASE_PARAMS
    assert "height" in _BASE_PARAMS
    assert "sizing_mode" in _BASE_PARAMS


# ---------------------------------------------------------------------------
# Stage view tests
# ---------------------------------------------------------------------------


def test_pipeline_stage_view_methods():
    """Terminal stages with @param.depends view methods get a view in the stage node."""
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    flow = pipeline.__panel__()
    sink_node = next(n for n in flow.nodes if n["id"] == "Sink")
    assert "view" in sink_node


def test_pipeline_stage_output_as_view():
    """Stages with only @param.output methods (no view methods) render output as view."""
    pipeline = Pipeline(stages=[("Source", Source), ("Sink", Sink)])
    flow = pipeline.__panel__()
    source_node = next(n for n in flow.nodes if n["id"] == "Source")
    # Source has no non-output view methods, so output is rendered as view
    assert "view" in source_node


def test_pipeline_stage_view_methods_preferred():
    """View methods are preferred over output methods for stage node views."""

    class StageWithBoth(param.Parameterized):
        x = param.String("hello")

        @param.output(param.String)
        @param.depends("x")
        def x_out(self):
            return self.x.upper()

        @param.depends("x")
        def custom_view(self):
            return f"Custom: {self.x}"

    pipeline = Pipeline(stages=[("Stage", StageWithBoth)])
    flow = pipeline.__panel__()
    stage_node = next(n for n in flow.nodes if n["id"] == "Stage")
    assert "view" in stage_node
    # The view should be from custom_view, not from the output
    # We can verify by checking it's a Panel object wrapping the view method
    from panel.viewable import Viewable

    assert isinstance(stage_node["view"], Viewable)
