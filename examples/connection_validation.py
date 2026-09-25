"""Try frontend rules and Python connection hooks in one graph.

Run from the repository root with:

    PYTHONPATH=src pixi run panel serve examples/connection_validation.py --show
"""

import panel as pn
import panel_material_ui as pmui

from panel_reactflow import NodeSpec, NodeType, ReactFlow

pn.extension()


class ConnectionValidationDemo(pn.viewable.Viewer):
    def __init__(self, **params):
        super().__init__(**params)
        self._status = pn.pane.Markdown("No connections yet.")
        self._flow = ReactFlow(
            nodes=[
                NodeSpec(id="source", type="source", label="Source", position={"x": 0, "y": 60}).to_dict(),
                NodeSpec(id="number", type="number", label="Number", position={"x": 0, "y": 280}).to_dict(),
                NodeSpec(id="transform", type="transform", label="Transform", position={"x": 290, "y": 60}).to_dict(),
                NodeSpec(id="publish", type="publish", label="Publish", position={"x": 610, "y": 60}).to_dict(),
                NodeSpec(id="monitor", type="monitor", label="Monitor", position={"x": 610, "y": 280}).to_dict(),
            ],
            node_types={
                "source": NodeType(type="source", outputs=[{"id": "text", "type": "Text"}]),
                "number": NodeType(type="number", outputs=[{"id": "value", "type": "Number"}]),
                "transform": NodeType(
                    type="transform",
                    inputs=[{"id": "text", "type": "Text", "maxConnections": 1}],
                    outputs=[{"id": "cleaned", "type": "Text"}],
                ),
                "publish": NodeType(type="publish", inputs=[{"id": "text", "type": "Text", "maxConnections": 1}]),
                "monitor": NodeType(type="monitor", inputs=[{"id": "text", "type": "Text"}]),
            },
            connection_validation={
                "direction": True,
                "types": True,
                "capacity": True,
                "duplicates": True,
                "cycles": True,
            },
            height=510,
            sizing_mode="stretch_width",
        )
        self._flow.add_connection_validator(self._validate_connection)
        self._flow.on("edge_added", self._on_edge_added)
        self._page = pmui.Page(
            title="Connection validation",
            main=[
                pmui.Container(
                    pmui.Column(
                        pn.pane.Markdown(
                            "Drag **Source.text** to **Transform.text**, then **Transform.cleaned** to **Publish.text**. "
                            "**Number.value** to **Transform.text** fails the frontend type check; "
                            "**Source.text** to **Publish.text** is rejected by Python. "
                            "Try connecting both text outputs to **Monitor.text**, or creating a cycle."
                        ),
                        self._flow,
                        self._status,
                        sizing_mode="stretch_width",
                    ),
                    width_option="lg",
                ),
            ],
        )

    def _validate_connection(self, edge, flow):
        if edge["source"] == "source" and edge["target"] == "publish":
            return "Publish requires text from Transform, not Source."

    def _on_edge_added(self, event, flow):
        edge = event["edge"]
        reason = self._validate_connection(edge, flow)
        if reason:
            flow.remove_edge(edge["id"])
            self._status.object = f"Rejected: {reason}"
        else:
            self._status.object = f"Connected: {edge['source']}.{edge['sourceHandle']} to {edge['target']}.{edge['targetHandle']}"

    def __panel__(self):
        return self._page


demo = ConnectionValidationDemo()
demo.servable()
