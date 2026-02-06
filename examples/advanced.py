"""Advanced example with schema-driven auto-generated form editors.

Defines a ``task`` node type with a JSON Schema.  The default
:class:`SchemaEditor` reads the schema and renders appropriate
widgets (text inputs, selectors, integer inputs) automatically.
"""

import panel as pn
import panel_material_ui as pmui

from panel_reactflow import NodeType, ReactFlow

pn.extension('jsoneditor')

# -- Stylesheet for the task node type ---------------------------------------

TASK_NODE_CSS = """
.react-flow__node-task {
    padding: 0;
    border-radius: 8px;
    border: 1.5px solid #7c3aed;
    background: linear-gradient(168deg, #faf5ff 0%, #ffffff 60%);
    box-shadow: 0 1px 3px rgba(124, 58, 237, 0.10),
                0 1px 2px rgba(16, 24, 40, 0.06);
    color: #1e1b4b;
    font-size: 13px;
    min-width: 160px;
    transition: box-shadow 0.2s ease, border-color 0.2s ease,
                transform 0.15s ease;
}

.react-flow__node-task.selectable:hover {
    border-color: #6d28d9;
    box-shadow: 0 4px 12px rgba(124, 58, 237, 0.18),
                0 2px 4px rgba(16, 24, 40, 0.08);
    transform: translateY(-1px);
}

.react-flow__node-task.selected {
    border-color: #7c3aed;
    box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.25),
                0 4px 14px rgba(124, 58, 237, 0.15);
}

.react-flow__node-task.selected:hover {
    box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.30),
                0 6px 16px rgba(124, 58, 237, 0.20);
}
"""

# -- Define a node type with a JSON Schema ----------------------------------

task_schema = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["idle", "running", "done"],
            "title": "Status",
        },
        "priority": {"type": "integer", "title": "Priority"},
        "notes": {"type": "string", "title": "Notes"},
    },
}

# -- Build nodes and edges ---------------------------------------------------

nodes = [
    {
        "id": "start",
        "type": "task",
        "position": {"x": 0, "y": 0},
        "label": "Start",
        "data": {"status": "idle", "priority": 1, "notes": ""},
        "view": pn.pane.Markdown("Start node body"),
    },
    {
        "id": "process",
        "type": "task",
        "position": {"x": 260, "y": 80},
        "label": "Process",
        "data": {"status": "running", "priority": 2, "notes": ""},
        "view": pn.pane.Markdown("Process node body"),
    },
    {
        "id": "finish",
        "type": "task",
        "position": {"x": 520, "y": 0},
        "label": "Finish",
        "data": {"status": "done", "priority": 1, "notes": ""},
        "view": pn.pane.Markdown("Finish node body"),
    },
]

edges = [
    {"id": "e1", "source": "start", "target": "process", "label": "0.2"},
    {"id": "e2", "source": "process", "target": "finish", "label": "0.5"},
]

# -- Wire up the flow --------------------------------------------------------

status = pn.pane.Markdown("**Last event:** _none_")

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types={
        "task": NodeType(type="task", label="Task", schema=task_schema),
    },
    stylesheets=[TASK_NODE_CSS],
    top_panel=[status],
    sizing_mode="stretch_both",
)

flow.left_panel = [pmui.RadioBoxGroup.from_param(flow.param.editor_mode)]


def handle_event(evt):
    status.object = f"**Last event:** `{evt.get('type')}`"


flow.on("*", handle_event)

pn.Column(flow, sizing_mode="stretch_both").servable()
