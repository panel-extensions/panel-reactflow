"""Inspect typed port values by clicking handles and edges.

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
    sizing_mode="stretch_both",
    min_height=450,
)


def show_value(title, value, position):
    flow.show_popup(
        pn.Column(
            pn.pane.Markdown(f"**{title}**", margin=(0, 0, 6, 0)),
            pn.pane.JSON(value, depth=3, sizing_mode="stretch_width"),
            sizing_mode="stretch_width",
        ),
        position,
    )


def on_handle_clicked(payload, flow):
    node_id = payload["node_id"]
    handle_id = payload["handle_id"]
    value = live_values.get(node_id, {}).get(handle_id)
    show_value(f"{node_id}.{handle_id}", value, payload["position"])


def on_edge_clicked(payload, flow):
    node_id, handle_id = edge_sources[payload["edge_id"]]
    value = live_values[node_id][handle_id]
    show_value(f"{node_id}.{handle_id}", value, payload["position"])


flow.on("handle_clicked", on_handle_clicked)
flow.on("edge_clicked", on_edge_clicked)

pn.Column(
    "# Port value inspection",
    "Hover a port to see its type. Click a port or edge to inspect its current value.",
    flow,
    sizing_mode="stretch_both",
).servable()
