# Release Notes

## Version 0.1.0

*Initial release*

Panel-ReactFlow brings the power of [React Flow](https://reactflow.dev/) to
the [Panel](https://panel.holoviz.org/) ecosystem, giving Python developers a
fully interactive node-based graph editor with minimal boilerplate.

### Highlights

- **ReactFlow component** — a Panel component that renders an interactive
  React Flow canvas with drag, select, connect, and delete support.
- **Python-first graph state** — define nodes and edges as plain Python
  dictionaries; all changes sync bidirectionally between the frontend and
  Python.
- **Node & edge types** — use `NodeType` and `EdgeType` dataclasses to
  declare typed nodes and edges with optional JSON Schema for their `data`
  payloads.
- **Schema-driven editors** — auto-generate editing forms from JSON Schema
  (via `SchemaEditor`) or fall back to a raw JSON tree view
  (`JsonEditor`).  Custom editors can be any Python callable or class.
- **Editor display modes** — show editors inline inside nodes
  (`editor_mode="node"`), in a toolbar popover (`"toolbar"`), or in a
  side panel (`"side"`).
- **Embedded views** — pass any Panel `Viewable` as a node's `view` to
  render rich content (charts, indicators, widgets) directly inside graph
  nodes.
- **Event system** — subscribe to granular events (`node_added`,
  `edge_deleted`, `selection_changed`, …) or use `"*"` to listen to
  everything.
- **Custom handles** — configure named input and output ports per node type.
- **Stylesheets** — apply CSS stylesheets targeting `.react-flow__node-{type}`
  and `.react-flow__edge-{type}` classes for full visual control.
- **Panel integration** — `top_panel`, `bottom_panel`, `left_panel`, and
  `right_panel` slots for surrounding the canvas with arbitrary Panel
  components.
- **Helper dataclasses** — `NodeSpec` and `EdgeSpec` for validated node/edge
  construction; convenience methods `add_node`, `remove_node`, `add_edge`,
  `remove_edge`, and `patch_node_data` / `patch_edge_data` for live graph
  manipulation.
