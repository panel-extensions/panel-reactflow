# Style Nodes & Edges

Panel-ReactFlow renders each node and edge with predictable CSS classes, so
you can apply any visual treatment — colors, borders, shadows, animations —
purely through CSS.  Styles are passed to the `ReactFlow` component via
its `stylesheets` parameter, which accepts a list of CSS strings or file
paths.

This approach keeps your styling concerns completely separate from your
Python logic and lets you iterate on visuals without restarting the server.

![Screenshot: styled nodes with custom colors, borders, and shadow effects](../assets/screenshots/style-nodes-edges.png)

---

## Complete runnable example

This script is a minimal, working example that produces the visualization
shown above.

```python
import panel as pn

from panel_reactflow import EdgeType, NodeType, ReactFlow

pn.extension("jsoneditor")

TASK_NODE_CSS = """
.react-flow__node-task {
    border-radius: 8px;
    border: 1.5px solid #7c3aed;
    background: linear-gradient(168deg, #faf5ff 0%, #ffffff 60%);
    box-shadow: 0 1px 3px rgba(124, 58, 237, 0.10);
    min-width: 160px;
    transition: box-shadow 0.2s ease, border-color 0.2s ease;
}
.react-flow__node-task.selectable:hover {
    border-color: #6d28d9;
    box-shadow: 0 4px 12px rgba(124, 58, 237, 0.18);
    transform: translateY(-1px);
}
.react-flow__node-task.selected {
    border-color: #7c3aed;
    box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.25),
                0 4px 14px rgba(124, 58, 237, 0.15);
}
"""

TYPED_EDGE_CSS = """
.react-flow__edge-pipe .react-flow__edge-path {
    stroke: #2563eb;
    stroke-width: 2.5px;
}
.react-flow__edge-signal .react-flow__edge-path {
    stroke: #dc2626;
    stroke-width: 2px;
    stroke-dasharray: 6 3;
}
.react-flow__edge-text {
    fill: #475569;
    font-size: 12px;
}
"""

nodes = [
    {"id": "n1", "type": "task", "label": "Ingest", "position": {"x": 0, "y": 0}, "data": {"status": "idle"}},
    {"id": "n2", "type": "task", "label": "Transform", "position": {"x": 280, "y": 0}, "data": {"status": "running"}},
    {"id": "n3", "type": "task", "label": "Export", "position": {"x": 560, "y": 0}, "data": {"status": "done"}},
]

edges = [
    {"id": "e1", "source": "n1", "target": "n2", "type": "pipe", "label": "pipe", "data": {}},
    {"id": "e2", "source": "n2", "target": "n3", "type": "signal", "label": "signal", "data": {}},
]

task_schema = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["idle", "running", "done"]},
    },
}

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types={"task": NodeType(type="task", label="Task", schema=task_schema)},
    edge_types={
        "pipe": EdgeType(type="pipe", label="Pipe"),
        "signal": EdgeType(type="signal", label="Signal"),
    },
    stylesheets=[TASK_NODE_CSS, TYPED_EDGE_CSS],
    sizing_mode="stretch_both",
)

pn.Column(flow, sizing_mode="stretch_both").servable()
```

## How this code maps to the visualization

- `nodes` and `edges` define the same three-node, two-edge layout.
- `.react-flow__node-task` styles all task nodes (background, border, shadow).
- `.react-flow__edge-pipe` and `.react-flow__edge-signal` style each edge type differently.
- `stylesheets=[TASK_NODE_CSS, TYPED_EDGE_CSS]` applies the custom CSS.

---

## How CSS classes are assigned

| Element | Class pattern | Example |
|---------|---------------|---------|
| Node by type | `.react-flow__node-{type}` | `.react-flow__node-task` |
| Edge by type | `.react-flow__edge-{type}` | `.react-flow__edge-pipe` |
| Edge path | `.react-flow__edge-path` | — |
| Edge label | `.react-flow__edge-text` | — |
| Selected | `.selected` (added to node or edge) | `.react-flow__node-task.selected` |
| Hoverable | `.selectable` (added to node) | `.react-flow__node-task.selectable:hover` |

---

## Style a node type

```python
TASK_NODE_CSS = """
.react-flow__node-task {
    border-radius: 8px;
    border: 1.5px solid #7c3aed;
    background: linear-gradient(168deg, #faf5ff 0%, #ffffff 60%);
    box-shadow: 0 1px 3px rgba(124, 58, 237, 0.10);
    min-width: 160px;
    transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.react-flow__node-task.selectable:hover {
    border-color: #6d28d9;
    box-shadow: 0 4px 12px rgba(124, 58, 237, 0.18);
    transform: translateY(-1px);
}

.react-flow__node-task.selected {
    border-color: #7c3aed;
    box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.25),
                0 4px 14px rgba(124, 58, 237, 0.15);
}
"""

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    stylesheets=[TASK_NODE_CSS],
)
```

---

## Style edges globally

Target all edge paths and labels regardless of type:

```python
EDGE_CSS = """
.react-flow__edge-path {
    stroke: #64748b;
    stroke-width: 2px;
}
.react-flow__edge-text {
    fill: #475569;
    font-size: 12px;
}
"""
```

---

## Style by edge type

Just like nodes, edges receive a class based on their `type`.  Use this
to give different edge types distinct appearances:

```python
TYPED_EDGE_CSS = """
/* Solid blue for "pipe" edges */
.react-flow__edge-pipe .react-flow__edge-path {
    stroke: #2563eb;
    stroke-width: 2.5px;
}

/* Dashed red for "signal" edges */
.react-flow__edge-signal .react-flow__edge-path {
    stroke: #dc2626;
    stroke-width: 2px;
    stroke-dasharray: 6 3;
}
"""
```

---

## Combine multiple stylesheets

Pass multiple CSS strings (or a mix of strings and file paths) to
`stylesheets`.  They are applied in order, so later entries override
earlier ones.

```python
flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    stylesheets=[TASK_NODE_CSS, EDGE_CSS, TYPED_EDGE_CSS],
)
```

---

## Color mode and theme integration

`ReactFlow` exposes a `color_mode` parameter (`"light"` or `"dark"`). If you
do not set it explicitly, it defaults to the current `pn.config.theme` when
the component is created.

```python
flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    color_mode="dark",  # optional override
)
```

When using `panel-material-ui`, theme toggles are reflected automatically, so
the graph updates along with the active light/dark theme.

---

## Style selected elements

The `.selected` class is added automatically when a user clicks a node or
edge.  Use it to provide clear visual feedback.

```python
SELECTION_CSS = """
.react-flow__node.selected {
    outline: 2px solid #2563eb;
    outline-offset: 2px;
}
.react-flow__edge.selected .react-flow__edge-path {
    stroke: #2563eb;
    stroke-width: 3px;
}
"""
```

---

## Tips

- Use CSS `transition` for smooth hover and selection effects.
- Scope styles to a node or edge type to keep visuals consistent across
  instances of the same type.
- If you define a custom node type and do not provide `stylesheets`, nodes fall
  back to the default node styling (`.react-flow__node-default`).
- For rapid prototyping, define styles as inline Python strings.  For
  production apps, move them to `.css` files and reference by path.
