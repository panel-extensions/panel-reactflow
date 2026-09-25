# Validate Connections

Use `connection_validation` to reject invalid connections as users drag between handles. The policies run in the browser; a Python validator can add application-specific rules. Both are opt-in and can be used together.

## Configure browser policies

Set only the checks your graph needs:

| Policy | Rejects |
|--------|---------|
| `direction` | Connections that do not run from a declared output to a declared input. Users can still start a drag at either end. |
| `types` | Connections between handles with different declared `type` values (case-insensitive). If either type is missing, the connection is allowed. |
| `capacity` | Connections to an input handle that has reached its positive integer `maxConnections` limit. Inputs without a limit are unrestricted. |
| `duplicates` | A second edge with the same source, target, source handle, and target handle. Different handle pairs can still connect the same nodes. |
| `cycles` | Connections that would create a cycle, including a self-connection. |

Policies default to off. Existing `NodeType` connectable flags still control whether a handle can start or receive a drag; see [Control Handle Connectivity](control-handle-connectivity.md).

The following app uses handle types and capacity limits alongside a Python rule. Save it as `validation_app.py` and run `panel serve validation_app.py --show`:

```python
import panel as pn

from panel_reactflow import NodeSpec, NodeType, ReactFlow

pn.extension("jsoneditor")

flow = ReactFlow(
    nodes=[
        NodeSpec(id="source", type="source", label="Source", position={"x": 0, "y": 0}),
        NodeSpec(id="transform", type="transform", label="Transform", position={"x": 260, "y": 0}),
        NodeSpec(id="publish", type="publish", label="Publish", position={"x": 520, "y": 0}),
    ],
    node_types={
        "source": NodeType(type="source", outputs=[{"id": "text", "type": "Text"}]),
        "transform": NodeType(
            type="transform",
            inputs=[{"id": "text", "type": "text", "maxConnections": 1}],
            outputs=[{"id": "cleaned", "type": "Text"}],
        ),
        "publish": NodeType(type="publish", inputs=[{"id": "text", "type": "Text", "maxConnections": 1}]),
    },
    connection_validation={
        "direction": True,
        "types": True,
        "capacity": True,
        "duplicates": True,
        "cycles": True,
    },
    height=350,
    width=800,
)


def validate_connection(edge, flow):
    if edge["source"] == "source" and edge["target"] == "publish":
        return "Publish requires the Transform output."
    return None


flow.add_connection_validator(validate_connection)


def on_edge_added(event, flow):
    edge = event["edge"]
    if validate_connection(edge, flow):
        flow.remove_edge(edge["id"])


flow.on("edge_added", on_edge_added)
flow.servable()
```

Drag from **Source.text** to **Transform.text**, then from **Transform.cleaned** to **Publish.text**. A direct connection from Source to Publish is rejected by Python. Try connecting Source.text to Transform.cleaned (two outputs) to see the direction check, or connect an output to an already occupied input to see the capacity check.

## Add application rules

Register callbacks with `flow.add_connection_validator(callback)` and unregister them with `flow.remove_connection_validator(callback)`. Each callback accepts either `edge` or `(edge, flow)` and returns `None` to allow the connection or a reason string to reject it. The payload has `source`, `target`, `sourceHandle`, and `targetHandle` keys; handles without an explicit ID use `None`.

When a user starts dragging from an output or input, Python checks every candidate handle on the opposite side. Validators run in registration order until one rejects a candidate. Browser policies and Python reasons are shown on handles while dragging; the browser prevents a rejected connection. Python results must arrive before the connection is dropped: pending requests and requests taking longer than three seconds block the connection. Exceptions also reject the candidate and are logged on the server. Keep validators fast.

Validation is for interactive drags, not a constraint on `flow.edges` or `flow.add_edge()`. Python validators run at drag start, not when the edge is added. If the rule depends on graph state that may change during the drag, check it again in an `edge_added` handler before keeping or persisting the edge. The example above removes an edge if its application rule no longer holds.
