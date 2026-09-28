"""Drag items from a panel-material-ui MenuList onto the canvas to add nodes.

Dropping an item on empty canvas adds a node there; dropping it on a node's
input handle also connects the new node to that input.

Run from the repository root with:

    PYTHONPATH=src pixi run panel serve examples/drop_from_menu.py --show
"""

import panel as pn
import panel_material_ui as pmui

from panel_reactflow import NodeSpec, NodeType, ReactFlow

pn.extension()

PALETTE_ITEMS = [
    {"label": "Sources", "draggable": False, "items": [
        {"label": "CSV file", "icon": "description", "kind": "source"},
        {"label": "Database", "icon": "storage", "kind": "source"},
    ]},
    {"label": "Transforms", "draggable": False, "items": [
        {"label": "Filter", "icon": "filter_alt", "kind": "transform"},
    ]},
]


def lookup(items, path):
    item = {"items": items}
    for index in path:
        item = item["items"][index]
    return item


palette = pmui.MenuList(
    items=PALETTE_ITEMS, draggable=True, expanded=[(0,), (1,)], width=220, label="Drag onto the canvas"
)

flow = ReactFlow(
    nodes=[NodeSpec(id="sink", type="transform", label="Sink", position={"x": 400, "y": 150}).to_dict()],
    node_types={
        "source": NodeType(type="source", inputs=[], outputs=["out"]),
        "transform": NodeType(type="transform", inputs=["in"], outputs=["out"]),
    },
    drop_types=[palette.drag_type],
    # Keeps fitView from zooming in on the lone starting node, so nodes placed
    # beside it stay on screen.
    max_zoom=1,
    sizing_mode="stretch_both",
    min_height=500,
)


def on_drop(payload, flow):
    item = lookup(palette.items, payload["data"]["path"])
    node_id = f"{item['kind']}-{len(flow.nodes)}"
    target = payload["target"]
    wire = target is not None and target["direction"] == "input"
    position = payload["position"]
    if wire:
        # Place the new node left of the input it feeds rather than on top of it.
        target_node = next(n for n in flow.nodes if n["id"] == target["node_id"])
        position = {"x": target_node["position"]["x"] - 320, "y": target_node["position"]["y"]}
    flow.add_node(NodeSpec(id=node_id, type=item["kind"], label=item["label"], position=position).to_dict())
    if wire:
        flow.add_edge(
            {"source": node_id, "sourceHandle": "out", "target": target["node_id"], "targetHandle": target["handle_id"]}
        )


flow.on("drop", on_drop)

demo = pmui.Row(palette, flow, sizing_mode="stretch_both")
demo.servable()
