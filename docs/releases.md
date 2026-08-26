# Release Notes

## Unreleased

### Bug fixes

- **Progressive re-render when deleting multiple elements** — deleting a
  multi-node selection removed the nodes one at a time, syncing an
  intermediate graph to the browser per node so the nodes visibly
  disappeared one by one. Updates triggered by a frontend message are now
  held and combined into a single patch, and node/edge deletion assigns
  `nodes` and `edges` once.

### Enhancements

- **`remove_nodes()` / `remove_edges()`** — new methods that remove
  several elements in a single update, for use instead of looping over
  `remove_node()` / `remove_edge()`. For any other batch of changes made
  from Python, wrap them in `pn.io.hold()` to render them at once.

## Version 0.4.1

A small enhancement release adding viewport zoom controls.

### Enhancements

- **Zoom limits** — new `min_zoom` (default `0.5`) and `max_zoom`
  (default `2`) parameters constrain how far the canvas can be zoomed
  out and in, and can be updated at runtime
  ([#69](https://github.com/panel-extensions/panel-reactflow/pull/69)).

## Version 0.4.0

This release adds new interaction features, edge routing options, and
handle customization capabilities.

### Highlights

- **Right-click context menu** — nodes and the canvas now support a
  configurable context menu triggered on right-click, enabling custom
  actions like delete, duplicate, or copy without toolbar buttons
  ([#65](https://github.com/panel-extensions/panel-reactflow/pull/65)).
- **Smart edge routing** — new smart edge types (`smartBezier`,
  `smartStep`, `smartStraight`) that automatically route around nodes
  instead of passing through them
  ([#64](https://github.com/panel-extensions/panel-reactflow/pull/64)).
- **Handle connectivity controls** — per-type flags
  (`input_connectable`, `input_connectable_start`,
  `input_connectable_end`, and their `output_*` counterparts) let you
  restrict which handles users can drag edges from or to
  ([#63](https://github.com/panel-extensions/panel-reactflow/pull/63)).
- **Handle tooltips** — handles can now carry a `label` that appears as
  an instant tooltip on hover, making port purpose discoverable without
  cluttering the canvas. Pass `{"id": "port_name", "label": "Description"}`
  in your `inputs`/`outputs` lists
  ([#67](https://github.com/panel-extensions/panel-reactflow/pull/67)).
- **`Node.flow` and `Edge.flow` back-references** — instance-based nodes
  and edges now hold a `.flow` reference to their parent `ReactFlow`,
  simplifying imperative graph manipulation from callbacks
  ([#56](https://github.com/panel-extensions/panel-reactflow/pull/56)).
- **Delete key support** — selected nodes and edges can now be removed
  with the Delete key, matching standard graph-editor UX expectations.
- **Bundle cache-busting** — the frontend bundle URL now includes a
  content hash, preventing stale cached bundles after upgrades
  ([#68](https://github.com/panel-extensions/panel-reactflow/pull/68)).

### Bug fixes

- Fixed stale node views not updating and deletion causing flicker
  ([#60](https://github.com/panel-extensions/panel-reactflow/pull/60)).
- Fixed `flow` attribute initialization on component creation
  ([#59](https://github.com/panel-extensions/panel-reactflow/pull/59)).
- Edge/node add/remove events are now emitted exactly once
  ([#58](https://github.com/panel-extensions/panel-reactflow/pull/58)).
- Fixed removing a node when edges are defined as `Edge` instances
  ([#57](https://github.com/panel-extensions/panel-reactflow/pull/57)).

## Version 0.3.0

This release focuses on core graph-model capabilities, callback ergonomics,
rendering/styling improvements, and major documentation expansion.

### Highlights

- **Parameterized `Node`/`Edge` instances as first-class graph elements** —
  `ReactFlow` now supports instance-based graph authoring with richer Python
  object workflows, including improved serialization/sync behavior and expanded
  API/core test coverage.
- **Event callback improvements** — `.on(...)` callbacks can now accept the
  `ReactFlow` instance as a second argument and support async callbacks, making
  app-level event handling significantly more flexible.
- **Rendering fixes for embedded views** — improved scaling and interaction
  behavior when rendering figures inside nodes, including better handling of
  drag/pan/scroll conflicts within embedded content.
- **Editor rendering robustness** — fixed node and edge editor rendering paths
  so editors can render reliably even when no node `view` is defined.
- **Color mode and default node styling support** — added color-mode-aware
  styling capabilities and refined baseline node CSS for better defaults out of
  the box.
- **Expanded how-to documentation** — substantial updates to guides for
  declaring types, defining nodes/edges, editors, embedding views, reacting to
  events, and styling, with refreshed screenshots across the docs.
- **Examples overhaul in docs** — added an examples gallery with grid cards and
  dedicated pages/screenshots for each example app, plus docs navigation
  updates for easier discovery.

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
