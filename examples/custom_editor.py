"""Example with a custom callable editor.

Shows how to register a plain function as a node editor.  The function
receives ``(data, schema, *, id, type, on_patch)`` and returns any
Panel viewable.  Widget changes are reported back to the graph via the
``on_patch`` callback.
"""

import panel as pn
import panel_material_ui as pmui

from panel_reactflow import NodeType, ReactFlow

pn.extension('jsoneditor')


# -- Custom callable editor --------------------------------------------------

def metric_editor(data, schema, *, id, type, on_patch):
    """Custom editor for 'metric' nodes with a slider and text input."""
    value = pmui.FloatSlider(
        value=data.get("value", 0),
        start=0,
        end=100,
        step=0.1,
        label="Value",
    )
    unit = pmui.Select(
        value=data.get("unit", "ms"),
        options=["ms", "s", "req/s", "%"],
        label="Unit",
    )

    value.param.watch(lambda e: on_patch({"value": e.new}), "value")
    unit.param.watch(lambda e: on_patch({"unit": e.new}), "value")

    return pmui.Paper(
        pmui.Column(value, unit, margin=5),
        margin=0,
    )


# -- Graph -------------------------------------------------------------------

nodes = [
    {
        "id": "m1",
        "type": "metric",
        "position": {"x": 0, "y": 0},
        "label": "Latency",
        "data": {"value": 42.5, "unit": "ms"},
    },
    {
        "id": "m2",
        "type": "metric",
        "position": {"x": 0, "y": 300},
        "label": "Throughput",
        "data": {"value": 1200, "unit": "req/s"},
    },
    {
        "id": "agg",
        "type": "panel",
        "position": {"x": 400, "y": 250},
        "label": "Aggregate",
        "data": {},
    },
]

edges = [
    {"id": "e1", "source": "m1", "target": "agg"},
    {"id": "e2", "source": "m2", "target": "agg"},
]

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types={
        "metric": NodeType(type="metric", label="Metric"),
    },
    node_editors={
        "metric": metric_editor,           # callable editor for metric nodes
    },
    editor_mode="node",
    sizing_mode="stretch_both",
)

pn.Column(flow, sizing_mode="stretch_both").servable()
