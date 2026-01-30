import panel as pn

from panel_reactflow import ReactFlow

pn.extension('jsoneditor')

nodes = [
    {
        "id": "n1",
        "position": {"x": 0, "y": 0},
        "type": "panel",
        "data": {"label": "Start"},
        "view": pn.pane.Markdown("Node 1 content"),
    },
    {
        "id": "n2",
        "position": {"x": 260, "y": 60},
        "type": "panel",
        "data": {"label": "End"},
        "view": pn.pane.Markdown("Node 2 content"),
    },
]
edges = [
    {"id": "e1", "source": "n1", "target": "n2"},
]

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    sizing_mode="stretch_both",
    editor_mode="node",
    top_panel=[pn.pane.Markdown("Top Panel")],
)

flow.servable()
