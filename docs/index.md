# :material-share-variant: Build Interactive Graph Editors in Python

**panel-reactflow is a lightweight bridge between Panel and ReactFlow for**
**building node‑based editors with schema‑driven data and custom Python UI.**

Define nodes and edges as plain dictionaries, attach JSON Schemas to
generate forms, and plug in editors where you need full control.

!!! tip "Quick Demo"
    New to Panel‑ReactFlow? **[Try the 2‑minute quickstart →](quickstart.md)**

## Why Panel‑ReactFlow

- **Python‑first workflow**: stay in Python while using a modern React Flow canvas.
- **Schema‑driven editing**: auto‑generate widgets from JSON Schema.
- **Decoupled editors**: keep types simple; register editors separately.
- **Flexible styling**: style by node type and edge type with CSS.

## Quickstart

```python
import panel as pn
from panel_reactflow import NodeType, ReactFlow

pn.extension()

task_schema = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["idle", "running", "done"]},
        "priority": {"type": "integer"},
    },
}

nodes = [
    {"id": "start", "type": "task", "label": "Start", "position": {"x": 0, "y": 0}, "data": {"status": "idle"}},
    {"id": "finish", "type": "task", "label": "Finish", "position": {"x": 260, "y": 60}, "data": {"status": "done"}},
]
edges = [{"id": "e1", "source": "start", "target": "finish"}]

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types={"task": NodeType(type="task", label="Task", schema=task_schema)},
    editor_mode="node",
    sizing_mode="stretch_both",
)

flow.servable()
```

## How‑to guides

- [Define Nodes & Edges](how-to/define-nodes-edges.md)
- [Declare Node & Edge Types](how-to/declare-types.md)
- [Define Editors](how-to/define-editors.md) — node *and* edge editors
- [Embed Views in Nodes](how-to/embed-views-in-nodes.md)
- [Style Nodes & Edges](how-to/style-nodes-edges.md)
- [React to Events](how-to/react-to-events.md)

## Reference

- [API reference](reference/panel_reactflow.md)
