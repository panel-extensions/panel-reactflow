"""Example demonstrating edge editors.

Edges with a typed schema get auto-generated form editors (via
``SchemaEditor``).  Click an edge to open its editor in the
right-hand side panel.  A custom callable editor is also shown for
the ``signal`` edge type.
"""

import panel as pn
import panel_material_ui as pmui

from panel_reactflow import EdgeType, NodeType, ReactFlow

pn.extension("jsoneditor")

# -- Edge types with schemas -------------------------------------------------

pipe_schema = {
    "type": "object",
    "properties": {
        "throughput": {"type": "number", "title": "Throughput"},
        "protocol": {
            "type": "string",
            "enum": ["tcp", "udp", "http"],
            "title": "Protocol",
        },
    },
}


def signal_editor(data, schema, *, id, type, on_patch):
    """Custom callable editor for signal edges."""
    freq = pmui.FloatSlider(
        value=data.get("frequency", 1.0), start=0.1, end=100, step=0.1,
        label="Frequency (Hz)",
    )
    active = pmui.Checkbox(value=data.get("active", True), label="Active")

    freq.param.watch(lambda e: on_patch({"frequency": e.new}), "value")
    active.param.watch(lambda e: on_patch({"active": e.new}), "value")

    return pmui.Paper(pmui.Column(freq, active, margin=5), margin=0)


# -- Graph -------------------------------------------------------------------

nodes = [
    {
        "id": "src",
        "type": "device",
        "label": "Source",
        "position": {"x": 0, "y": 0},
        "data": {},
    },
    {
        "id": "relay",
        "type": "device",
        "label": "Relay",
        "position": {"x": 300, "y": 0},
        "data": {},
    },
    {
        "id": "sink",
        "type": "device",
        "label": "Sink",
        "position": {"x": 600, "y": 0},
        "data": {},
    },
]

edges = [
    {
        "id": "e1",
        "source": "src",
        "target": "relay",
        "type": "pipe",
        "label": "pipe",
        "data": {"throughput": 100.0, "protocol": "tcp"},
    },
    {
        "id": "e2",
        "source": "relay",
        "target": "sink",
        "type": "signal",
        "label": "signal",
        "data": {"frequency": 50.0, "active": True},
    },
]

# -- Assemble ----------------------------------------------------------------

status = pn.pane.Markdown("Click an edge to edit it in the side panel.")

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types={
        "device": NodeType(type="device", label="Device"),
    },
    edge_types={
        "pipe": EdgeType(type="pipe", label="Pipe", schema=pipe_schema),
        "signal": EdgeType(type="signal", label="Signal"),
    },
    edge_editors={
        "signal": signal_editor,
    },
    top_panel=[status],
    sizing_mode="stretch_both",
)


def on_event(evt):
    status.object = f"**Last event:** `{evt.get('type')}`"


flow.on("*", on_event)

pn.Column(flow, sizing_mode="stretch_both").servable()
