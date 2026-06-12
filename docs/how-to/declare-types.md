# Declare Node & Edge Types

Node and edge types are lightweight descriptors that define **what data each
kind of node/edge carries**. A type can provide:

- a type name (`type`)
- a display label (`label`)
- node handles (`inputs` / `outputs`)
- handle connectivity controls (`input_connectable*` / `output_connectable*`)
- a schema for the `data` payload (`schema`)

Types are separate from editors. A type defines structure; an editor defines
the UI used to edit it.

![Screenshot: multiple node types with different schemas](../assets/screenshots/declare-types.png)

---

## Complete runnable example

This script is a minimal, working example that produces the visualization
shown above.

```python
import param
import panel as pn

from panel_reactflow import EdgeType, NodeType, ReactFlow

pn.extension("jsoneditor")


class Job(param.Parameterized):
    status = param.Selector(objects=["idle", "running", "done"])
    retries = param.Integer(default=0)


decision_schema = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "title": "Question"},
        "outcome": {
            "type": "string",
            "enum": ["yes", "no", "maybe"],
            "title": "Outcome",
        },
    },
}

node_types = {
    "job": NodeType(type="job", label="Job", schema=Job, inputs=["in"], outputs=["out"]),
    "decision": NodeType(
        type="decision",
        label="Decision",
        schema=decision_schema,
        inputs=["in"],
        outputs=["yes", "no"],
    ),
}

edge_types = {
    "flow": EdgeType(
        type="flow",
        label="Flow",
        schema={
            "type": "object",
            "properties": {"weight": {"type": "number", "title": "Weight"}},
        },
    ),
}

nodes = [
    {
        "id": "j1",
        "type": "job",
        "label": "Fetch Data",
        "position": {"x": 0, "y": 0},
        "data": {"status": "idle", "retries": 0},
    },
    {
        "id": "d1",
        "type": "decision",
        "label": "Valid?",
        "position": {"x": 300, "y": 250},
        "data": {"question": "Is data valid?", "outcome": "yes"},
    },
    {
        "id": "j2",
        "type": "job",
        "label": "Process",
        "position": {"x": 600, "y": 400},
        "data": {"status": "running", "retries": 1},
    },
]

edges = [
    {"id": "e1", "source": "j1", "target": "d1", "type": "flow", "data": {"weight": 1.0}},
    {"id": "e2", "source": "d1", "target": "j2", "type": "flow", "data": {"weight": 0.8}},
]

TASK_NODE_CSS = """
.react-flow__node-job {
    background-color: white;
    border-radius: 8px;
    border: 1.5px solid #7c3aed;
}

.react-flow__node-decision {
    background-color: white;
    border-radius: 8px;
    border: 1.5px solid green;
}
"""

flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types=node_types,
    edge_types=edge_types,
    editor_mode="node",
    sizing_mode="stretch_both",
    stylesheets=[TASK_NODE_CSS]
)

pn.Column(flow, sizing_mode="stretch_both").servable()
```

## How this code maps to the visualization

- `node_types["job"]` and `node_types["decision"]` define the two node kinds you see.
- `inputs` and `outputs` define the left/right handles rendered on each node.
- `edge_types["flow"]` defines the edge payload schema used by both connections.
- `nodes` controls labels (`Fetch Data`, `Valid?`, `Process`) and positions.
- `editor_mode="side"` makes selection open the schema-driven editor in the right panel.

---

## Node type snippet

Use `NodeType` to define node handles and payload schema.

```python
from panel_reactflow import NodeType

task_schema = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["idle", "running", "done"]},
        "priority": {"type": "integer"},
    },
}

node_types = {
    "task": NodeType(
        type="task",
        label="Task",
        schema=task_schema,
        inputs=["in"],
        outputs=["out"],
    ),
}
```

## Edge type snippet

Use `EdgeType` for edge payload schema and label.

```python
from panel_reactflow import EdgeType

edge_types = {
    "pipe": EdgeType(
        type="pipe",
        label="Pipe",
        schema={
            "type": "object",
            "properties": {
                "throughput": {"type": "number"},
                "protocol": {"type": "string", "enum": ["tcp", "udp", "http"]},
            },
        },
    ),
}
```

---

## Schema sources

The `schema` field accepts multiple inputs and normalizes them to JSON Schema.

| Source | Example |
|--------|---------|
| **JSON Schema dict** | `{"type": "object", "properties": {...}}` |
| **Param class** | A `param.Parameterized` subclass |
| **Pydantic model** | A `pydantic.BaseModel` subclass |

### Param class shorthand

```python
import param
from panel_reactflow import NodeType

class Job(param.Parameterized):
    status = param.Selector(objects=["idle", "running", "done"])
    retries = param.Integer(default=0)

node_types = {"job": NodeType(type="job", label="Job", schema=Job)}
```

### Pydantic model shorthand

```python
from pydantic import BaseModel
from panel_reactflow import NodeType

class Config(BaseModel):
    host: str = "localhost"
    port: int = 8080

node_types = {"config": NodeType(type="config", label="Config", schema=Config)}
```

---

## Register on `ReactFlow`

Pass `node_types` and `edge_types` as dictionaries keyed by type name:

```python
flow = ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types=node_types,
    edge_types=edge_types,
)
```

Types without a schema still work; they just do not get schema-driven
validation or auto-generated forms.

---

## Handle tooltips

By default, handles are plain connection points. You can add a tooltip (shown
on hover) by passing a dict with `"id"` and `"label"` instead of a plain string:

```python
from panel_reactflow import NodeType

node_types = {
    "transform": NodeType(
        type="transform",
        label="Transform",
        inputs=[{"id": "in", "label": "Data Input"}],
        outputs=[
            {"id": "success", "label": "Successful results"},
            {"id": "error", "label": "Failed records"},
        ],
    ),
}
```

Plain strings and dicts can be mixed freely in the same list:

```python
inputs=["simple_port", {"id": "documented_port", "label": "Hover to see this"}]
```

---

## Control handle connectivity

By default, all handles (inputs and outputs) are fully connectable — users can
drag edges from or to any handle. Use the `*_connectable*` flags to restrict
which connections are allowed.

### Common patterns

#### Data source (output only)

A node that produces data but cannot accept incoming connections to its output:

```python
from panel_reactflow import NodeType

source_type = NodeType(
    type="data_source",
    label="Data Source",
    outputs=["data"],
    output_connectable_start=True,   # Can drag FROM output
    output_connectable_end=False,    # Cannot drag TO output
)
```

#### Data sink (input only)

A node that consumes data but cannot produce outgoing connections from its input:

```python
sink_type = NodeType(
    type="data_sink",
    label="Data Sink",
    inputs=["data"],
    input_connectable_start=False,   # Cannot drag FROM input
    input_connectable_end=True,      # Can drag TO input
)
```

#### Monitor node

A node that accepts input but whose output is status-only (one direction):

```python
monitor_type = NodeType(
    type="monitor",
    label="Monitor",
    inputs=["in"],
    outputs=["status"],
    input_connectable_start=False,   # Cannot start edges from input
    output_connectable_end=False,    # Cannot end edges at output
)
```

### All connectivity flags

| Flag | Default | Controls |
|------|---------|----------|
| `input_connectable` | `True` | Whether input handles are connectable at all |
| `input_connectable_start` | `True` | Whether edges can start from input handles |
| `input_connectable_end` | `True` | Whether edges can end at input handles |
| `output_connectable` | `True` | Whether output handles are connectable at all |
| `output_connectable_start` | `True` | Whether edges can start from output handles |
| `output_connectable_end` | `True` | Whether edges can end at output handles |

### Complete example

```python
import panel as pn
from panel_reactflow import NodeType, NodeSpec, EdgeSpec, ReactFlow

pn.extension("jsoneditor")

# Define node types with different connectivity patterns
node_types = {
    "source": NodeType(
        type="source",
        label="Data Source",
        outputs=["data"],
        output_connectable_start=True,
        output_connectable_end=False,
    ),
    "transform": NodeType(
        type="transform",
        label="Transform",
        inputs=["in"],
        outputs=["out"],
        # All connectable flags default to True
    ),
    "sink": NodeType(
        type="sink",
        label="Data Sink",
        inputs=["data"],
        input_connectable_start=False,
        input_connectable_end=True,
    ),
}

# Create a data pipeline
flow = ReactFlow(
    nodes=[
        NodeSpec(id="src", type="source", position={"x": 0, "y": 100}, data={}).to_dict(),
        NodeSpec(id="tx", type="transform", position={"x": 250, "y": 100}, data={}).to_dict(),
        NodeSpec(id="snk", type="sink", position={"x": 500, "y": 100}, data={}).to_dict(),
    ],
    edges=[
        EdgeSpec(id="e1", source="src", target="tx").to_dict(),
        EdgeSpec(id="e2", source="tx", target="snk").to_dict(),
    ],
    node_types=node_types,
    sizing_mode="stretch_both",
)

flow.servable()
```

In this example:

- Users can drag from the **source** output to the **transform** input ✓
- Users cannot drag to the **source** output ✗
- Users can drag from the **transform** output to the **sink** input ✓
- Users cannot drag from the **sink** input ✗

The UI prevents invalid connections automatically — non-connectable handles
show different cursor behavior and won't accept drag operations.
