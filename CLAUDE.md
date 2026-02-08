# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

panel-reactflow is a Panel extension that wraps the React Flow JS library (@xyflow/react), providing Python-first interactive node-graph editing with bidirectional sync between Python and the browser. It is part of the panel-extensions ecosystem under HoloViz.

## Development Setup

This project uses [pixi](https://pixi.sh) for environment management. After cloning:

```bash
pixi run pre-commit-install
pixi run postinstall          # editable pip install
pixi run test                 # run unit tests
pixi run test-coverage        # run tests with coverage
```

For UI tests (requires Playwright + Chromium):
```bash
pixi run -e test-ui test-ui
```

For docs:
```bash
pixi run docs-serve           # live-reload dev server
pixi run docs-build           # build static site to builtdocs/
```

Alternative setup with uv:
```bash
uv venv && source .venv/bin/activate
uv pip install -e .[dev]
pre-commit run install
pytest tests
```

### Running a Single Test

```bash
pixi run pytest tests/test_api.py::test_reactflow_add_remove -xvs
```

UI tests require `--ui` flag and are skipped by default.

### Linting

Pre-commit hooks handle linting (ruff, prettier, codespell, eslint for JS). Run all checks:
```bash
pixi run -e lint pre-commit-run
```

### Building

```bash
pixi run -e build build-wheel
pixi run -e build check-wheel
```

## Architecture

### Python Side (`src/panel_reactflow/`)

- **`base.py`** — The core module. Contains:
  - `ReactFlow` — Main component, extends `panel.custom.ReactComponent`. Manages nodes/edges as list-of-dicts params, syncs state bidirectionally with the JS frontend via `_handle_msg`/`_send_msg`. Supports event callbacks via `.on(event_type, callback)`.
  - `NodeSpec`/`EdgeSpec` — Dataclasses for constructing node/edge dicts with `.to_dict()`/`.from_dict()` roundtrip.
  - `NodeType`/`EdgeType` — Dataclasses for type definitions with optional schema (JSON Schema dict, `param.Parameterized` subclass, or Pydantic `BaseModel`). Schemas are normalized to JSON Schema via `_normalize_schema()`.
  - `Editor`/`JsonEditor`/`SchemaEditor` — Editor classes (extend `panel.viewable.Viewer`) following the signature `(data, schema, *, id, type, on_patch)`. `SchemaEditor` auto-generates widget forms from JSON Schema when properties are available, falling back to raw JSON editor.
  - Schema helpers: `_param_to_jsonschema()`, `_pydantic_to_jsonschema()`, `_normalize_schema()`, `_validate_data()`, `_coerce_spec_map()`.

- **`schema.py`** — `JSONSchema` pane (extends `panel.pane.base.PaneBase`). Converts JSON Schema property definitions into Panel Material UI widgets. Used by `SchemaEditor` when a schema with properties is available.

- **`models/reactflow.jsx`** — The React/JSX frontend component. Uses `@xyflow/react` v12. Handles canvas rendering, drag/select/connect/delete interactions, and syncs state back to Python via message passing.

### Build System

- Uses `hatchling` with a custom build hook (`hatch_build.py`) that compiles the JSX into a JS bundle via `panel.io.compile.compile_components`.
- Version is managed by `setuptools-scm` / `hatch-vcs` from git tags.
- The compiled bundle lives at `src/panel_reactflow/dist/panel-reactflow.bundle.js`.

### Key Patterns

- **Nodes carry `view` entries** — Panel viewables placed in `node["view"]` are extracted during `_process_param_change`, replaced with a `view_idx` integer, and rendered via the `_views` Children param. The JSX side reads `data.view_idx` to embed the corresponding Panel model.
- **Editor resolution** — Per-node/edge editors are resolved in `_update_node_editors`/`_update_edge_editors`. Priority: type-specific editor from `node_editors` dict > `default_node_editor` > `SchemaEditor` fallback.
- **Frontend sync** — The JS side sends typed messages (`sync`, `node_moved`, `selection_changed`, `edge_added`, `node_deleted`, etc.) handled by `_handle_msg` in Python. Python-to-JS patches go through `_send_msg`.
- **`node_types`/`edge_types`** are normalized to JSON-serializable dicts via `_coerce_spec_map()` on init and on param change, supporting raw dicts, `NodeType`/`EdgeType` dataclasses, or bare `param.Parameterized`/Pydantic classes as shorthand.

### Test Structure

- `tests/test_core.py` — Smoke test (import check).
- `tests/test_api.py` — Unit tests for specs, graph operations, schema normalization, editor resolution, type normalization, NetworkX interop.
- `tests/ui/test_ui.py` — Playwright browser tests (marked `@pytest.mark.ui`, require `--ui` flag).
- `tests/conftest.py` — Imports Panel's `document`/`comm` fixtures, adds `--ui` CLI option.

### Docs

Built with [zensical](https://github.com/zensical/zensical) (MkDocs-based). Config in `zensical.toml`. Docstring style is numpy.
