# Control Handle Connectivity

Restrict which connections users can create by configuring the connectable
flags on `NodeType`. This lets you enforce directional flow (sources, sinks,
transforms) and prevent invalid edges at the UI level.

![Screenshot: connectable handles demo](../assets/screenshots/connectable-handles.png)

---

## Problem

By default, all handles are fully connectable — users can drag edges from or
to any handle on any node. But many graph types have directional semantics:

- **Data sources** should only output data, never accept input
- **Data sinks** should only accept input, never produce output
- **Monitor nodes** might accept input but only emit status (one direction)
- **Read-only nodes** might display data without allowing any new connections

Without restrictions, users can create semantically invalid edges that break
your application's logic.

---

## Solution

Use the six `*_connectable*` flags on `NodeType` to control which drag
operations are allowed for input and output handles.

### Available flags

| Flag | Default | Controls |
|------|---------|----------|
| `input_connectable` | `True` | Whether input handles are connectable at all |
| `input_connectable_start` | `True` | Whether edges can **start from** input handles |
| `input_connectable_end` | `True` | Whether edges can **end at** input handles |
| `output_connectable` | `True` | Whether output handles are connectable at all |
| `output_connectable_start` | `True` | Whether edges can **start from** output handles |
| `output_connectable_end` | `True` | Whether edges can **end at** output handles |

---

## Common patterns

### Data source (output only)

Produces data but cannot accept incoming connections:

```python
from panel_reactflow import NodeType

source = NodeType(
    type="source",
    label="Data Source",
    outputs=["data"],
    output_connectable_start=True,   # Can drag FROM output
    output_connectable_end=False,    # Cannot drag TO output
)
```

**Valid**: Drag from source output → another node's input ✓
**Invalid**: Drag from another node → source output ✗

### Data sink (input only)

Consumes data but cannot produce outgoing connections:

```python
sink = NodeType(
    type="sink",
    label="Data Sink",
    inputs=["data"],
    input_connectable_start=False,   # Cannot drag FROM input
    input_connectable_end=True,      # Can drag TO input
)
```

**Valid**: Drag from another node's output → sink input ✓
**Invalid**: Drag from sink input → another node ✗

### Transform (bidirectional)

Full connectivity in both directions (default):

```python
transform = NodeType(
    type="transform",
    label="Transform",
    inputs=["in"],
    outputs=["out"],
    # All flags default to True — no need to specify
)
```

**Valid**: Any drag operation ✓

### Monitor node

Accepts input and emits status, but status cannot receive incoming edges:

```python
monitor = NodeType(
    type="monitor",
    label="Monitor",
    inputs=["in"],
    outputs=["status"],
    input_connectable_start=False,   # Cannot start edges from input
    output_connectable_end=False,    # Cannot end edges at output
)
```

**Valid**: Drag from another node → monitor input ✓
**Valid**: Drag from monitor output → another node ✓
**Invalid**: Drag from monitor input → another node ✗
**Invalid**: Drag from another node → monitor output ✗

### Read-only node

Displays data but allows no new connections:

```python
readonly = NodeType(
    type="readonly",
    label="Read Only",
    inputs=["in"],
    outputs=["out"],
    input_connectable=False,
    output_connectable=False,
)
```

**Invalid**: Any drag operation ✗

---

## Complete working example

This example builds a data pipeline with sources, transforms, and sinks:

```python
import panel as pn
from panel_reactflow import NodeType, NodeSpec, EdgeSpec, ReactFlow

pn.extension("jsoneditor")

# Define node types
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
    ),
    "monitor": NodeType(
        type="monitor",
        label="Monitor",
        inputs=["in"],
        outputs=["status"],
        input_connectable_start=False,
        output_connectable_end=False,
    ),
    "sink": NodeType(
        type="sink",
        label="Data Sink",
        inputs=["data"],
        input_connectable_start=False,
        input_connectable_end=True,
    ),
}

# Build pipeline
flow = ReactFlow(
    nodes=[
        NodeSpec(id="source1", type="source", position={"x": 100, "y": 150}, data={}).to_dict(),
        NodeSpec(id="transform1", type="transform", position={"x": 350, "y": 150}, data={}).to_dict(),
        NodeSpec(id="monitor1", type="monitor", position={"x": 600, "y": 150}, data={}).to_dict(),
        NodeSpec(id="sink1", type="sink", position={"x": 850, "y": 150}, data={}).to_dict(),
    ],
    edges=[
        EdgeSpec(id="e1", source="source1", target="transform1", sourceHandle="data", targetHandle="in").to_dict(),
        EdgeSpec(id="e2", source="transform1", target="monitor1", sourceHandle="out", targetHandle="in").to_dict(),
        EdgeSpec(id="e3", source="monitor1", target="sink1", sourceHandle="status", targetHandle="data").to_dict(),
    ],
    node_types=node_types,
    width=1200,
    height=400,
)

flow.servable()
```

### Try these interactions

1. **Valid**: Drag from source output → transform input ✓
2. **Valid**: Drag from transform output → monitor input ✓
3. **Valid**: Drag from monitor output → sink input ✓
4. **Invalid**: Try to drag TO source output ✗ (blocked)
5. **Invalid**: Try to drag FROM sink input ✗ (blocked)
6. **Invalid**: Try to drag TO monitor output ✗ (blocked)

The UI automatically prevents invalid connections — handles show different
cursor behavior and won't accept or initiate drag operations when restricted.

---

## Multiple handles per side

Connectable flags apply to **all handles on a given side** (input or output).
If a node has multiple input handles, `input_connectable_start=False` affects
all of them:

```python
multi_input = NodeType(
    type="multi",
    label="Multi-Input",
    inputs=["in1", "in2", "in3"],  # Three input handles
    outputs=["out"],
    input_connectable_start=False,  # Applies to all input handles
)
```

All three input handles will respect the same connectivity rules.

---

## Programmatic edges vs UI restrictions

Connectable flags only affect **user drag interactions** in the UI. You can
still create edges programmatically regardless of the flags:

```python
# This edge will be created even if handles are non-connectable
flow.add_edge(EdgeSpec(id="e1", source="source1", target="sink1"))
```

Or by passing edges directly to `ReactFlow`:

```python
edges = [
    EdgeSpec(id="e1", source="source1", target="sink1").to_dict()
]
flow = ReactFlow(nodes=nodes, edges=edges, node_types=node_types)
```

The flags control what users can do via drag-and-drop, not what your code
can create.

---

## Flag independence

Each flag is independent. Setting one doesn't affect others:

```python
# Only restrict starting edges from inputs
node = NodeType(
    type="restricted",
    inputs=["in"],
    outputs=["out"],
    input_connectable_start=False,  # Only this flag changes
    # input_connectable=True (default)
    # input_connectable_end=True (default)
    # All output_* flags also default to True
)
```

---

## Backwards compatibility

Existing code without connectable flags continues to work — all flags default
to `True`:

```python
# Old-style node type definition
legacy = NodeType(
    type="task",
    label="Task",
    inputs=["in"],
    outputs=["out"],
)
# Behaves exactly as before — all handles fully connectable
```

---

## Use cases

### ETL pipelines

```python
extract = NodeType(type="extract", outputs=["data"], output_connectable_end=False)
transform = NodeType(type="transform", inputs=["in"], outputs=["out"])
load = NodeType(type="load", inputs=["data"], input_connectable_start=False)
```

### State machines

```python
start = NodeType(type="start", outputs=["next"], output_connectable_end=False)
state = NodeType(type="state", inputs=["in"], outputs=["out"])
end = NodeType(type="end", inputs=["in"], input_connectable_start=False)
```

### DAG workflows

```python
trigger = NodeType(type="trigger", outputs=["event"], output_connectable_end=False)
task = NodeType(type="task", inputs=["trigger"], outputs=["result"])
logger = NodeType(type="logger", inputs=["log"], input_connectable_start=False)
```

---

## Tips

- **Start with defaults**: Don't add restrictions until you need them.
- **Test in UI**: Try dragging to confirm the restrictions work as expected.
- **Use patterns**: The source/sink/transform/monitor patterns cover most use cases.
- **Document intent**: Add comments explaining why specific flags are set.

---

## Related

- [Declare Node & Edge Types](declare-types.md) — full type declaration guide
- [Define Nodes & Edges](define-nodes-edges.md) — node and edge structure
- [API Reference](../reference/panel_reactflow.md) — complete API documentation
