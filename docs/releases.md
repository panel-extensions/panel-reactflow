# Release Notes

## Version 0.2.0

This release focuses on stronger typed graph specs, better node-view handling,
and improved docs and packaging.

### Highlights

- **`NodeSpec` view support** — added a `view` parameter to `NodeSpec` and
  fixed `add_node` behavior so embedded views are preserved when adding nodes
  programmatically.
- **Safer embedded view handling** — fixed an `AttributeError` when node views
  are Panel `Viewer` objects or other arbitrary view-like objects.
- **`EdgeSpec` handle targeting** — added `sourceHandle` and `targetHandle`
  fields to support explicit edge-to-port connections.
- **Spec auto-serialization** — added automatic serialization for `NodeSpec`
  and `EdgeSpec` objects to reduce boilerplate when using dataclass-based graph
  definitions.
- **Handle rendering fix** — corrected empty handle list behavior so `[]` is
  treated distinctly from missing/undefined handles.
- **Styling hook for labels** — added the `rf-node-label` CSS class on node
  labels for easier targeted styling.
- **Docs and onboarding updates** — quickstart and index docs now explicitly
  document the required `pn.extension("jsoneditor")` setup; additional how-to
  docs were updated for new handle and serialization behavior.
- **Packaging reliability** — ensured the frontend `dist` assets are included
  in distributions.

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
