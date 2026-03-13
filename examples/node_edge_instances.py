"""Complex example using Node and Edge class instances.

Demonstrates:
- ``Node`` / ``Edge`` subclass instances in ``ReactFlow``
- Per-instance ``__panel__`` node views
- Per-instance custom editors via ``editor(...)``
- Node/edge event hooks (``on_data_change``, ``on_selection_changed``)
- Programmatic updates with ``patch_node_data`` / ``patch_edge_data``
"""

import random

import panel as pn
import panel_material_ui as pmui
import param

from panel_reactflow import Edge, Node, ReactFlow

pn.extension()


class PipelineNode(Node):
    status = param.Selector(default="idle", objects=["idle", "running", "done", "failed"], precedence=0)
    retries = param.Integer(default=0, bounds=(0, None), precedence=0)
    owner = param.String(default="ops", precedence=0)
    notes = param.String(default="", precedence=0)

    def __init__(self, **params):
        params.setdefault("type", "pipeline")
        super().__init__(**params)
        self._summary = pn.pane.Markdown(margin=(0, 0, 6, 0))
        self._activity = pn.pane.Markdown("", styles={"font-size": "12px", "opacity": "0.8"})
        self.param.watch(self._refresh_view, ["status", "owner", "retries", "label"])
        self._refresh_view()

    def _refresh_view(self, *_):
        self._summary.object = (
            f"**{self.label}**  \n"
            f"Status: `{self.status}`  \n"
            f"Owner: `{self.owner}`  \n"
            f"Retries: `{self.retries}`"
        )

    def __panel__(self):
        return pn.Column(self._summary, self._activity, margin=0, sizing_mode="stretch_width")

    def editor(self, data, schema, *, id, type, on_patch):
        status = pmui.Select.from_param(self.param.status, name="Status")
        retries = pmui.IntInput.from_param(self.param.retries, name="Retries")
        owner = pmui.TextInput.from_param(self.param.owner, name="Owner")
        notes = pmui.TextAreaInput.from_param(self.param.notes, name="Notes", height=80)
        return pn.Column(status, retries, owner, notes, sizing_mode="stretch_width")

    def on_data_change(self, payload, flow):
        if payload.get("node_id") == self.id:
            self._activity.object = f"Last patch: `{payload.get('patch', {})}`"

    def on_selection_changed(self, payload, flow):
        selected = self.id in (payload.get("nodes") or [])
        if selected:
            self._activity.object = "Selected in canvas"


class WeightedEdge(Edge):
    weight = param.Number(default=0.5, bounds=(0, 1), precedence=0)
    channel = param.Selector(default="main", objects=["main", "backup", "shadow"], precedence=0)
    enabled = param.Boolean(default=True, precedence=0)

    def __init__(self, **params):
        params.setdefault("type", "weighted")
        super().__init__(**params)

    def editor(self, data, schema, *, id, type, on_patch):
        weight = pmui.FloatSlider.from_param(self.param.weight, name="Weight", step=0.01)
        channel = pmui.Select.from_param(self.param.channel, name="Channel")
        enabled = pmui.Checkbox.from_param(self.param.enabled, name="Enabled")
        return pn.Column(weight, channel, enabled, sizing_mode="stretch_width")


nodes = [
    PipelineNode(id="extract", label="Extract", position={"x": 0, "y": 40}),
    PipelineNode(id="transform", label="Transform", position={"x": 300, "y": 160}, status="running", retries=1, owner="ml", notes="Batch window"),
    PipelineNode(id="load", label="Load", position={"x": 600, "y": 40}, owner="platform"),
]

edges = [
    WeightedEdge(id="e1", source="extract", target="transform", weight=0.72),
    WeightedEdge(id="e2", source="transform", target="load", weight=0.63, channel="backup"),
]

event_log = pmui.TextAreaInput(name="Events", value="", disabled=True, height=180, sizing_mode="stretch_width")
last_event = pn.pane.Markdown("**Last event:** _none_")

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    editor_mode="side",
    sizing_mode="stretch_both",
)

def _log_event(payload):
    event_type = payload.get("type", "unknown")
    last_event.object = f"**Last event:** `{event_type}`"
    snippet = str(payload)
    event_log.value = f"{event_log.value}\n{event_type}: {snippet}"[-6000:]


flow.on("*", _log_event)


def _advance_nodes(_):
    order = {"idle": "running", "running": "done", "done": "done", "failed": "idle"}
    for node in nodes:
        current = node.status
        flow.patch_node_data(node.id, {"status": order.get(current, "idle")})


def _randomize_weights(_):
    for edge in edges:
        flow.patch_edge_data(edge.id, {"weight": round(random.uniform(0.05, 0.95), 2)})


advance_btn = pmui.Button(name="Advance pipeline")
advance_btn.on_click(_advance_nodes)

weights_btn = pmui.Button(name="Randomize edge weights")
weights_btn.on_click(_randomize_weights)

controls = pn.Row(advance_btn, weights_btn, sizing_mode="stretch_width")

pn.Column(
    pn.pane.Markdown("## Node/Edge Instance Workflow"),
    controls,
    last_event,
    flow,
    event_log,
    sizing_mode="stretch_both",
).servable()
