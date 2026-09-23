"""Inspect typed port values by hovering over handles and edges.

Run with:

    panel serve examples/port_value_inspection.py --show
"""

import panel as pn

from panel_reactflow import EdgeSpec, NodeSpec, NodeType, ReactFlow

pn.extension("jsoneditor")

node_types = {
    "source": NodeType(
        type="source",
        label="Data Source",
        outputs=[{"id": "records", "label": "Output records", "type": "list[dict]"}],
    ),
    "transform": NodeType(
        type="transform",
        label="Transform",
        inputs=[{"id": "records", "label": "Input records", "type": "list[dict]"}],
        outputs=[{"id": "summary", "label": "Summary", "type": "dict[str, int]"}],
    ),
    "sink": NodeType(
        type="sink",
        label="Sink",
        inputs=[{"id": "summary", "label": "Input summary", "type": "dict[str, int]"}],
    ),
}

nodes = [
    NodeSpec(id="source", type="source", position={"x": 0, "y": 100}, data={}).to_dict(),
    NodeSpec(id="transform", type="transform", position={"x": 300, "y": 100}, data={}).to_dict(),
    NodeSpec(id="sink", type="sink", position={"x": 600, "y": 100}, data={}).to_dict(),
]

edges = [
    EdgeSpec(id="records", source="source", target="transform", sourceHandle="records", targetHandle="records").to_dict(),
    EdgeSpec(id="summary", source="transform", target="sink", sourceHandle="summary", targetHandle="summary").to_dict(),
]

# In an application this mapping would be updated by the code that executes
# the graph. It is intentionally separate from ReactFlow's graph metadata.
live_values = {
    "source": {"records": [{"city": "Berlin", "sales": 12}, {"city": "Oslo", "sales": 8}]},
    "transform": {"summary": {"record_count": 2, "total_sales": 20}},
    "sink": {"summary": {"record_count": 2, "total_sales": 20}},
}

edge_sources = {
    "records": ("source", "records"),
    "summary": ("transform", "summary"),
}

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types=node_types,
    popup_trigger="hover",  # Change to "click" to inspect on click instead.
    popup_hover_delay=500,
    sizing_mode="stretch_both",
    min_height=450,
)


active_target = None


def show_value(title, value, position, target):
    global active_target
    active_target = target
    flow.show_popup(
        pn.Column(
            pn.pane.Markdown(f"**{title}**", margin=(0, 0, 6, 0)),
            pn.pane.JSON(value, depth=3, sizing_mode="stretch_width"),
            sizing_mode="stretch_width",
        ),
        position,
    )


def on_handle_inspected(payload, flow):
    node_id = payload["node_id"]
    handle_id = payload["handle_id"]
    value = live_values.get(node_id, {}).get(handle_id)
    show_value(f"{node_id}.{handle_id}", value, payload["position"], ("handle", node_id, handle_id, payload["direction"]))


def on_edge_inspected(payload, flow):
    node_id, handle_id = edge_sources[payload["edge_id"]]
    value = live_values[node_id][handle_id]
    show_value(f"{node_id}.{handle_id}", value, payload["position"], ("edge", payload["edge_id"]))


def on_handle_unhovered(payload, flow):
    global active_target
    if active_target == ("handle", payload["node_id"], payload["handle_id"], payload["direction"]):
        active_target = None
        flow.close_popup()


def on_edge_unhovered(payload, flow):
    global active_target
    if active_target == ("edge", payload["edge_id"]):
        active_target = None
        flow.close_popup()


flow.on("handle_clicked", on_handle_inspected)
flow.on("edge_clicked", on_edge_inspected)
flow.on("handle_hovered", on_handle_inspected)
flow.on("edge_hovered", on_edge_inspected)
flow.on("handle_unhovered", on_handle_unhovered)
flow.on("edge_unhovered", on_edge_unhovered)

pn.Column(
    "# Port value inspection",
    "Hover a port to see its type and pause over a port or edge to inspect its current value.",
    flow,
    sizing_mode="stretch_both",
).servable()
