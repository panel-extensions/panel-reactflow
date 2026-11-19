# Group Nodes

Nodes can be nested in other nodes. A nested node has a `parentId`, its
`position` is relative to its parent and it moves with the parent. The
built-in `"group"` node type draws a frame with a header around its
children, which can be collapsed into a single compact node.

---

## Group the selected nodes

```python
import panel as pn
import panel_material_ui as pmui

from panel_reactflow import NodeSpec, ReactFlow

pn.extension()

flow = ReactFlow(
    nodes=[
        NodeSpec(id="load", position={"x": 0, "y": 80}, label="Load").to_dict(),
        NodeSpec(id="clean", position={"x": 260, "y": 0}, label="Clean").to_dict(),
        NodeSpec(id="join", position={"x": 260, "y": 160}, label="Join").to_dict(),
        NodeSpec(id="plot", position={"x": 620, "y": 80}, label="Plot").to_dict(),
    ],
    edges=[
        {"id": "e1", "source": "load", "target": "clean"},
        {"id": "e2", "source": "clean", "target": "join"},
        {"id": "e3", "source": "join", "target": "plot"},
    ],
    sizing_mode="stretch_both",
)

group = pmui.Button(label="Group selection")
ungroup = pmui.Button(label="Ungroup")

group.on_click(lambda _: flow.group_nodes(label="Prepare"))


def dissolve(_):
    groups = {node["id"] for node in flow.nodes if node.get("type") == "group"}
    for node_id in groups & set(flow.selection["nodes"]):
        flow.ungroup(node_id)


ungroup.on_click(dissolve)
flow.on("group_toggled", lambda event: print(event))

pmui.Column(pmui.Row(group, ungroup), flow, sizing_mode="stretch_both").servable()
```

Select nodes with ++shift++ + click (or ++shift++ + drag a box) and click
*Group selection*. `group_nodes()` creates a group around the nodes, makes
them its children and returns the group's id. The nodes keep their place on
the canvas; their positions are converted to be relative to the group.
`ungroup()` does the reverse and removes the group.

`set_parent(node_ids, parent_id)` moves nodes into another node (or to the
top level with `None`) the same way, and `absolute_position(node_id)` returns
a node's position on the canvas, adding up the positions of its parents.

## Parent fields

`NodeSpec`, `Node` and plain node dicts accept the React Flow fields for
nesting, all of which can be updated with `patch_node_props()`:

| Field | Meaning |
| --- | --- |
| `parentId` | Id of the parent node; `position` is relative to it. |
| `extent` | `"parent"` keeps the node inside its parent, `[[x0, y0], [x1, y1]]` inside that rectangle. |
| `expandParent` | Grow the parent when the node is dragged past its border. |
| `hidden` | Hide the node and its edges. |
| `zIndex` | Stacking order. |

Parents may appear anywhere in `nodes`; the browser orders them before their
children as React Flow requires. Removing a node removes the nodes nested in
it.

## Collapse and expand

The chevron in a group's header collapses it, as does
`flow.collapse_group(group_id)`; `flow.expand_group(group_id)` reverses it.
Both emit a `group_toggled` event. The state is kept in the group's
`data["collapsed"]`, so it is saved with the graph.

Collapsing only changes what is drawn. The group's descendants are hidden,
edges between them are hidden too, and edges between a hidden node and the
rest of the graph are drawn to the collapsed group instead. `flow.nodes` and
`flow.edges` keep the real graph.

A collapsed group shows one input and one output handle unless its data
declares its own, in the same format as `NodeType` `inputs` and `outputs`.
`data["handle_map"]` decides which group handle stands in for which inner
handle:

```python
flow.group_nodes(
    ["clean", "join"],
    label="Prepare",
    data={
        "inputs": [{"id": "raw", "label": "raw"}],
        "outputs": [{"id": "table", "label": "table"}],
        "handle_map": [
            {"node": "clean", "group_handle": "raw"},
            {"node": "join", "handle": None, "group_handle": "table"},
        ],
    },
)
```

Each entry maps a handle of a node inside the group (omit `handle` to map
all of the node's handles) to a group handle. An entry can add
`"direction": "input"` or `"direction": "output"` to apply to that side
only, for a node with an input and an output of the same id; without it the
entry applies to both. Edges that are not mapped
attach to the group's first handle on that side. Dragging a connection to a
mapped handle of a collapsed group creates an edge to each inner handle
mapped to it.

## Events

| Event | Payload |
| --- | --- |
| `nodes_grouped` | `group_id`, `node_ids` |
| `nodes_ungrouped` | `group_id`, `node_ids` (the former children) |
| `group_toggled` | `node_id`, `collapsed` |

`group_nodes()` also emits `node_added` for the new group and `ungroup()`
`node_deleted` for the removed one.

## Styling

The group's frame is the `.rf-group` element inside
`.react-flow__node-group`, with `.rf-group--collapsed` or
`.rf-group--expanded`; its header is `.rf-group-header`. Edges drawn to a
collapsed group carry the `rf-edge--rerouted` class.
