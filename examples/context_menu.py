"""Context menu example using Node subclasses.

Demonstrates:
- Per-node context menus via ``Node.context_menu()``
- Dynamic menu content based on node state
- Closing the menu on action (via ``flow.patch_node_data``)
"""

import panel as pn
import panel_material_ui as pmui
import param

from panel_reactflow import Node, ReactFlow

pn.extension()


class TaskNode(Node):
    status = param.Selector(
        default="idle", objects=["idle", "running", "done", "failed"], precedence=0
    )

    def __init__(self, **params):
        params.setdefault("type", "panel")
        super().__init__(**params)

    def __panel__(self):
        return pn.pane.Markdown(
            f"**{self.label}**\n\nStatus: `{self.status}`",
            sizing_mode="stretch_width",
        )

    def context_menu(self):
        def set_status(status):
            self.flow.patch_node_data(self.id, {"status": status})
            self.flow._handle_msg({"type": "close_context_menu"})

        run_btn = pmui.Button(
            name="Run", variant="text", size="small",
            on_click=lambda e: set_status("running"),
        )
        done_btn = pmui.Button(
            name="Mark Done", variant="text", size="small",
            on_click=lambda e: set_status("done"),
        )
        reset_btn = pmui.Button(
            name="Reset", variant="text", size="small",
            on_click=lambda e: set_status("idle"),
        )
        delete_btn = pmui.Button(
            name="Delete", variant="text", color="error", size="small",
            on_click=lambda e: self.flow.remove_node(self.id),
        )

        return pn.Column(
            pn.pane.Markdown(f"**{self.label}**", margin=(4, 8)),
            run_btn, done_btn, reset_btn, delete_btn,
            sizing_mode="stretch_width",
            margin=0,
        )


nodes = [
    TaskNode(id="extract", label="Extract", position={"x": 0, "y": 0}),
    TaskNode(id="transform", label="Transform", position={"x": 300, "y": 80}),
    TaskNode(id="load", label="Load", position={"x": 600, "y": 0}, status="done"),
]

flow = ReactFlow(
    nodes=nodes,
    edges=[
        {"id": "e1", "source": "extract", "target": "transform"},
        {"id": "e2", "source": "transform", "target": "load"},
    ],
    sizing_mode="stretch_both",
)

pn.Column(
    pn.pane.Markdown("## Context Menu Demo\nRight-click any node to see its context menu."),
    flow,
    sizing_mode="stretch_both",
).servable()
