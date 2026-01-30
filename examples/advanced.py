import param
import panel as pn
import panel_material_ui as pmui

from panel_reactflow import ParamNodeEditor, ReactFlow

pn.extension()


class CustomEditor(ParamNodeEditor):

    label = param.String()

    status = param.Selector(objects=["idle", "running", "done"])

    priority = param.Integer(default=1)

    notes = param.String()


nodes = [
    {
        "id": "start",
        "type": "panel",
        "position": {"x": 0, "y": 0},
        "data": {"label": "Start", "status": "idle", "priority": 1},
        "view": pn.pane.Markdown("Start node body"),
    },
    {
        "id": "process",
        "type": "panel",
        "position": {"x": 260, "y": 80},
        "data": {"label": "Process", "status": "running", "priority": 2},
        "view": pn.pane.Markdown("Process node body"),
    },
    {
        "id": "finish",
        "type": "panel",
        "position": {"x": 520, "y": 0},
        "data": {"label": "Finish", "status": "done", "priority": 1},
        "view": pn.pane.Markdown("Finish node body"),
    },
]

edges = [
    {"id": "e1", "source": "start", "target": "process"},
    {"id": "e2", "source": "process", "target": "finish"},
]

status = pn.pane.Markdown("**Last event:** _none_")

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types={"panel": CustomEditor},
    top_panel=[status],
    sizing_mode="stretch_both",
)

flow.left_panel = [pmui.RadioBoxGroup.from_param(flow.param.editor_mode)]

def handle_event(evt):
    status.object = f"**Last event:** `{evt.get('type')}`"

flow.on("*", handle_event)

pn.Column(flow, sizing_mode="stretch_both").servable()
