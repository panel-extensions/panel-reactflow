"""Core React Flow component and helpers."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import panel as pn
import param
from bokeh.embed.bundle import extension_dirs
from panel.config import config
from panel.custom import Children, ReactComponent
from panel.io.resources import EXTENSION_CDN
from panel.io.state import state
from panel.util import base_version, classproperty
from panel.viewable import Viewer
from panel.widgets import JSONEditor
from panel_material_ui import Paper

from .__version import __version__  # noqa

if TYPE_CHECKING:
    from bokeh.models import UIElement

IS_RELEASE = __version__ == base_version(__version__)
BASE_PATH = Path(__file__).parent
DIST_PATH = BASE_PATH / "dist"
CDN_BASE = f"https://cdn.holoviz.org/panel-reactflow/v{base_version(__version__)}"
CDN_DIST = f"{CDN_BASE}/panel-reactflow.bundle.js"

extension_dirs["panel-reactflow"] = DIST_PATH
EXTENSION_CDN[DIST_PATH] = CDN_BASE


def _ensure_jsonable(value: Any, path: str) -> None:
    """Ensure value can be JSON-serialized for syncing to the frontend."""

    try:
        json.dumps(value)
    except Exception as exc:
        raise ValueError(f"Value at {path} is not JSON-serializable.") from exc


def _is_param_class(obj: Any) -> bool:
    """Check if *obj* is a ``param.Parameterized`` **subclass** (not instance)."""
    return isinstance(obj, type) and issubclass(obj, param.Parameterized)


def _is_pydantic_class(obj: Any) -> bool:
    """Check if *obj* is a Pydantic ``BaseModel`` subclass."""
    try:
        from pydantic import BaseModel

        return isinstance(obj, type) and issubclass(obj, BaseModel)
    except ImportError:
        return False


def _param_to_jsonschema(parameterized_cls: type) -> dict[str, Any]:
    """Convert a ``param.Parameterized`` class to a JSON Schema dict.

    Uses ``parameterized_cls.param.schema()`` for the per-property
    schemas, then wraps them in a standard JSON Schema object envelope
    while filtering out base ``Parameterized`` params and private
    (``_``-prefixed) params.
    """
    base_params = set(param.Parameterized.param)
    raw = parameterized_cls.param.schema()
    properties = {name: prop for name, prop in raw.items() if name not in base_params and not name.startswith("_")}
    return {"type": "object", "properties": properties}


def _pydantic_to_jsonschema(model_cls: type) -> dict[str, Any]:
    """Convert a Pydantic ``BaseModel`` class to a JSON Schema dict."""
    return model_cls.model_json_schema()


def _normalize_schema(schema: Any) -> dict[str, Any] | None:
    """Normalize a schema source to a JSON Schema dict (or ``None``)."""
    if schema is None:
        return None
    if isinstance(schema, SchemaSource):
        if schema.kind == "jsonschema":
            return schema.value
        elif schema.kind == "param":
            return _param_to_jsonschema(schema.value)
        elif schema.kind == "pydantic":
            return _pydantic_to_jsonschema(schema.value)
    if isinstance(schema, dict):
        return schema
    if _is_param_class(schema):
        return _param_to_jsonschema(schema)
    if _is_pydantic_class(schema):
        return _pydantic_to_jsonschema(schema)
    raise ValueError(f"Cannot normalize schema: {schema!r}")


def _validate_data(data: dict[str, Any], schema: dict[str, Any] | None) -> None:
    """Validate *data* against a JSON Schema if available."""
    if schema is None:
        return
    try:
        import jsonschema as _js
    except ImportError:
        return
    try:
        _js.validate(data, schema)
    except _js.ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ValueError(f"Validation failed at {path}: {exc.message}") from exc


def _coerce_spec_map(specs: dict[str, Any] | None, *, edge: bool = False) -> dict[str, dict[str, Any]]:
    """Normalize a dict of type specs to JSON-serializable descriptors."""
    if not specs:
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in specs.items():
        if hasattr(value, "to_dict") and callable(value.to_dict):
            normalized[key] = value.to_dict()
        elif isinstance(value, dict):
            normalized[key] = value
        elif _is_param_class(value):
            klass = EdgeType if edge else NodeType
            normalized[key] = klass(type=key, schema=value).to_dict()
        elif _is_pydantic_class(value):
            klass = EdgeType if edge else NodeType
            normalized[key] = klass(type=key, schema=value).to_dict()
        else:
            raise ValueError(f"Unsupported spec type for '{key}'.")
    return normalized


@dataclass
class SchemaSource:
    """Explicit schema source wrapper.

    Parameters
    ----------
    kind:
        One of ``"jsonschema"``, ``"param"``, or ``"pydantic"``.
    value:
        The schema value (a JSON Schema dict, Param class, or Pydantic class).
    """

    kind: Literal["jsonschema", "param", "pydantic"]
    value: Any


@dataclass
class NodeType:
    """Type definition for a node.

    Parameters
    ----------
    type:
        Unique type name.
    label:
        Optional human-readable label.
    schema:
        Optional data schema. Accepts a JSON Schema dict, a
        ``param.Parameterized`` subclass, a Pydantic ``BaseModel``
        subclass, or a :class:`SchemaSource` wrapper. Normalized to
        JSON Schema when serialized.
    inputs:
        Optional list of input port names.
    outputs:
        Optional list of output port names.
    pane_policy:
        Display policy (default ``"single"``).
    """

    type: str
    label: str | None = None
    schema: Any = None
    inputs: list[str] | None = None
    outputs: list[str] | None = None
    pane_policy: str = "single"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "schema": _normalize_schema(self.schema),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "pane_policy": self.pane_policy,
        }


@dataclass
class EdgeType:
    """Type definition for an edge.

    Parameters
    ----------
    type:
        Unique type name.
    label:
        Optional human-readable label.
    schema:
        Optional data schema (same formats as :class:`NodeType`).
    """

    type: str
    label: str | None = None
    schema: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "schema": _normalize_schema(self.schema),
        }


@dataclass
class NodeSpec:
    """Helper for constructing node dictionaries."""

    id: str
    position: dict[str, float] | dict[str, Any] = None
    type: str = "panel"
    label: str | None = None
    data: dict[str, Any] | None = None
    selected: bool = False
    draggable: bool = True
    connectable: bool = True
    deletable: bool = True
    style: dict[str, Any] | None = None
    className: str | None = None

    def __post_init__(self) -> None:
        if self.position is None:
            self.position = {"x": 0.0, "y": 0.0}
        if self.data is None:
            self.data = {}

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "position": self.position,
            "type": self.type,
            "label": self.label,
            "data": self.data,
            "selected": self.selected,
            "draggable": self.draggable,
            "connectable": self.connectable,
            "deletable": self.deletable,
        }
        if self.style is not None:
            payload["style"] = self.style
        if self.className is not None:
            payload["className"] = self.className
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NodeSpec":
        return cls(**payload)


@dataclass
class EdgeSpec:
    """Helper for constructing edge dictionaries."""

    id: str
    source: str
    target: str
    label: str | None = None
    type: str | None = None
    selected: bool = False
    data: dict[str, Any] | None = None
    style: dict[str, Any] | None = None
    markerEnd: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.data is None:
            self.data = {}

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "type": self.type,
            "selected": self.selected,
            "data": self.data,
        }
        if self.style is not None:
            payload["style"] = self.style
        if self.markerEnd is not None:
            payload["markerEnd"] = self.markerEnd
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EdgeSpec":
        return cls(**payload)


class Editor(Viewer):
    """Base class for node/edge editors.

    All editors follow the unified signature::

        editor(data, schema, *, id, type, on_patch) -> Viewable

    Parameters
    ----------
    data : dict
        Current node or edge data dictionary.
    schema : dict | None
        Normalized JSON Schema for the node/edge type, or ``None``.
    id : str
        Node or edge identifier.
    type : str
        Node or edge type name.
    on_patch : callable
        Callback ``on_patch(patch_dict)`` to report data changes
        back to the graph.
    """

    _data = param.Dict(default={}, doc="Node or edge data.")
    _schema = param.Dict(default=None, allow_None=True, doc="JSON Schema for data.")
    _node_id = param.String(default="", doc="Node or edge ID.")
    _node_type = param.String(default="", doc="Node or edge type.")
    _on_patch = param.Callable(default=None, allow_None=True, doc="Callback to report data changes.")

    def __init__(self, data=None, schema=None, *, id="", type="", on_patch=None, **kwargs):
        super().__init__(
            _data=data if data is not None else {},
            _schema=schema,
            _node_id=id,
            _node_type=type or "",
            _on_patch=on_patch,
            **kwargs,
        )


class JsonEditor(Editor):
    """Editor that always renders a raw JSON editor."""

    def __init__(self, data=None, schema=None, **kwargs):
        super().__init__(data, schema, **kwargs)
        self._editor = JSONEditor(value=self._data)
        self._editor.param.watch(self._on_json_change, "value")

    def _on_json_change(self, event: param.parameterized.Event) -> None:
        if self._on_patch is not None and event.new != self._data:
            self._data = event.new
            self._on_patch(event.new)

    def __panel__(self):
        return self._editor


class SchemaEditor(Editor):
    """Smart default editor.

    When a JSON Schema with ``properties`` is available, uses
    :class:`~panel_reactflow.schema.JSONSchema` to render a form of
    widgets derived from the schema.  Otherwise falls back to a raw
    :class:`~panel.widgets.JSONEditor`.
    """

    def __init__(self, data=None, schema=None, **kwargs):
        super().__init__(data, schema, **kwargs)
        if self._schema and self._schema.get("properties"):
            try:
                from .schema import JSONSchema

                self._form = JSONSchema(
                    self._data,
                    schema=self._schema["properties"],
                    multi=False,
                )
                for name, widget in self._form._widgets.items():
                    widget.param.watch(
                        lambda event, _n=name: self._on_widget_change(_n, event),
                        "value",
                    )
                self._panel = Paper(self._form, margin=0)
            except Exception:
                # Graceful fallback if JSONSchema rendering fails
                # (e.g. missing pandas dependency).
                self._init_json_fallback()
        else:
            self._init_json_fallback()

    def _init_json_fallback(self) -> None:
        self._json_editor = JSONEditor(value=self._data)
        self._json_editor.param.watch(self._on_json_change, "value")
        self._panel = self._json_editor

    def _on_widget_change(self, name: str, event: param.parameterized.Event) -> None:
        if self._on_patch is not None:
            self._on_patch({name: event.new})

    def _on_json_change(self, event: param.parameterized.Event) -> None:
        if self._on_patch is not None and event.new != self._data:
            self._data = event.new
            self._on_patch(event.new)

    def __panel__(self):
        return self._panel


class ReactFlow(ReactComponent):
    """React Flow component wrapper."""

    nodes = param.List(default=[], doc="Canonical list of node dictionaries.")
    edges = param.List(default=[], doc="Canonical list of edge dictionaries.")
    node_types = param.Dict(default={}, doc="Node type descriptors keyed by type name.")
    edge_types = param.Dict(default={}, doc="Edge type descriptors keyed by type name.")

    node_editors = param.Dict(default={}, doc="Node editor factories keyed by type name.", precedence=-1)
    edge_editors = param.Dict(default={}, doc="Edge editor factories keyed by type name.", precedence=-1)
    default_node_editor = param.Parameter(default=None, doc="Default node editor factory.", precedence=-1)
    default_edge_editor = param.Parameter(default=None, doc="Default edge editor factory.", precedence=-1)

    debounce_ms = param.Integer(default=150, bounds=(0, None), doc="Debounce delay in milliseconds when sync_mode='debounce'.")

    default_edge_options = param.Dict(default={}, doc="Default React Flow edge options.")

    editable = param.Boolean(default=True, doc="Enable interactive editing on the canvas.")

    editor_mode = param.ObjectSelector(
        default="toolbar",
        objects=["toolbar", "node", "side"],
        doc="Where to render node editors: toolbar, node, or side panel.",
    )

    enable_connect = param.Boolean(default=True, doc="Allow connecting nodes to create edges.")

    enable_delete = param.Boolean(default=True, doc="Allow deleting selected nodes or edges.")

    enable_multiselect = param.Boolean(default=True, doc="Allow multiselect with modifier key.")

    selection = param.Dict(default={"nodes": [], "edges": []}, doc="Derived selection state for node and edge ids.")

    show_minimap = param.Boolean(default=False, doc="Show the minimap overlay.")

    sync_mode = param.ObjectSelector(default="event", objects=["event", "debounce"], doc="Sync mode for JS->Python updates.")

    validate_on_add = param.Boolean(default=True, doc="Validate data against schema on add_node/add_edge.")
    validate_on_patch = param.Boolean(default=False, doc="Validate data against schema on patch_node_data/patch_edge_data.")

    viewport = param.Dict(default=None, allow_None=True, doc="Optional persisted viewport state.")

    top_panel = Children(default=[], doc="Children rendered in a top-center panel.")
    bottom_panel = Children(default=[], doc="Children rendered in a bottom-center panel.")
    left_panel = Children(default=[], doc="Children rendered in a center-left panel.")
    right_panel = Children(default=[], doc="Children rendered in a center-right panel.")

    # Internal view parameters
    _node_editors = param.Dict(default={}, doc="Per-node editors.", precedence=-1)
    _node_editor_views = Children(default=[], doc="Node editor views (one per node, same order).")
    _edge_editors = param.Dict(default={}, doc="Per-edge editors.", precedence=-1)
    _edge_editor_views = Children(default=[], doc="Edge editor views (one per edge, same order).")
    _views = Children(default=[], doc="Panel viewables rendered inside nodes via view_idx.")

    _bundle = DIST_PATH / "panel-reactflow.bundle.js"
    _esm = Path(__file__).parent / "models" / "reactflow.jsx"
    _importmap = {"imports": {"@xyflow/react": "https://esm.sh/@xyflow/react@12.8.3"}}
    _stylesheets = [DIST_PATH / "panel-reactflow.bundle.css", DIST_PATH / "css" / "reactflow.css"]

    def __init__(self, **params: Any):
        self._node_ids: list[str] = []
        self._edge_ids: list[str] = []
        # Normalize type specs before parent init so the frontend receives
        # JSON-serializable descriptors from the start.
        if "node_types" in params:
            params["node_types"] = _coerce_spec_map(params["node_types"])
        if "edge_types" in params:
            params["edge_types"] = _coerce_spec_map(params["edge_types"], edge=True)
        super().__init__(**params)
        self._event_handlers: dict[str, list[Callable]] = {"*": []}
        self.param.watch(self._update_selection_from_graph, ["nodes", "edges"])
        self.param.watch(self._normalize_specs, ["node_types", "edge_types"])
        self.param.watch(
            self._update_node_editors,
            ["nodes", "editor_mode", "selection", "node_editors", "default_node_editor"],
        )
        self.param.watch(
            self._update_edge_editors,
            ["edges", "selection", "edge_editors", "default_edge_editor"],
        )
        self._update_node_editors()
        self._update_edge_editors()

    @classmethod
    def _esm_path(cls, compiled: bool | Literal["compiling"] = True) -> os.PathLike | None:
        return super()._esm_path(compiled or True)

    @classmethod
    def _render_esm(cls, compiled: bool | Literal["compiling"] = True, server: bool = False):
        esm_path = cls._esm_path(compiled=compiled)
        if compiled != "compiling" and server:
            # Generate relative path to handle apps served on subpaths
            esm = ("" if state.rel_path else "./") + cls._component_resource_path(esm_path, compiled)
            if config.autoreload:
                modified = hashlib.sha256(str(esm_path.stat().st_mtime).encode("utf-8")).hexdigest()
                esm += f"?{modified}"
        else:
            esm = esm_path.read_text(encoding="utf-8")
        return esm

    @classproperty
    def _bundle_path(cls) -> os.PathLike | None:
        return cls._bundle

    def _get_node_schema(self, node_type: str) -> dict[str, Any] | None:
        """Return the normalized JSON Schema for *node_type*, or ``None``."""
        type_spec = self.node_types.get(node_type)
        if type_spec is None:
            return None
        return type_spec.get("schema")

    def _get_edge_schema(self, edge_type: str) -> dict[str, Any] | None:
        """Return the normalized JSON Schema for *edge_type*, or ``None``."""
        type_spec = self.edge_types.get(edge_type)
        if type_spec is None:
            return None
        return type_spec.get("schema")

    def _create_editor(
        self,
        factory: Any,
        item_id: str,
        data: dict,
        schema: dict | None,
        item_type: str,
        *,
        patch_fn: Callable[[str, dict], None],
    ) -> Any:
        """Instantiate an editor from *factory*.

        All editors (classes and plain callables) receive the unified
        signature ``(data, schema, *, id, type, on_patch)``.

        *patch_fn* is the method to call when data changes –
        ``patch_node_data`` for nodes, ``patch_edge_data`` for edges.
        """

        def on_patch(patch: dict) -> None:
            patch_fn(item_id, patch)

        return factory(data, schema, id=item_id, type=item_type, on_patch=on_patch)

    def _update_node_editors(self, *events: tuple[param.parameterized.Event]) -> None:
        node_ids = [node["id"] for node in self.nodes]
        config_changed = any(event.name in ("editor_mode", "node_editors", "default_node_editor") for event in events)
        if node_ids == self._node_ids and not config_changed:
            return
        self._node_ids = node_ids

        editors = {}
        for node in self.nodes:
            node_id = node.get("id")
            if node_id in self._node_editors and not config_changed:
                editors[node_id] = self._node_editors[node_id]
                continue
            node_type = node.get("type", "panel")
            editor_factory = self.node_editors.get(node_type) or self.default_node_editor or SchemaEditor
            schema = self._get_node_schema(node_type)
            data = node.get("data", {})
            editor = self._create_editor(
                editor_factory,
                node_id,
                data,
                schema,
                node_type,
                patch_fn=self.patch_node_data,
            )
            editors[node_id] = editor
        self._node_editors = editors
        self.param.trigger("_node_editor_views")

    def _update_edge_editors(self, *events: tuple[param.parameterized.Event]) -> None:
        edge_ids = [edge["id"] for edge in self.edges]
        config_changed = any(event.name in ("edge_editors", "default_edge_editor") for event in events)
        if edge_ids == self._edge_ids and not config_changed:
            return
        self._edge_ids = edge_ids

        editors = {}
        for edge in self.edges:
            edge_id = edge.get("id")
            if edge_id in self._edge_editors and not config_changed:
                editors[edge_id] = self._edge_editors[edge_id]
                continue
            edge_type = edge.get("type", "")
            editor_factory = self.edge_editors.get(edge_type) or self.default_edge_editor or SchemaEditor
            schema = self._get_edge_schema(edge_type) if edge_type else None
            data = edge.get("data", {})
            editor = self._create_editor(
                editor_factory,
                edge_id,
                data,
                schema,
                edge_type,
                patch_fn=self.patch_edge_data,
            )
            editors[edge_id] = editor
        self._edge_editors = editors
        self.param.trigger("_edge_editor_views")

    @staticmethod
    def _resolve_editor_view(editor: Any) -> Any:
        """Return a Panel viewable from an editor (class or plain object)."""
        if editor is None:
            return pn.pane.HTML("")
        if hasattr(editor, "__panel__"):
            return editor.__panel__()
        return pn.panel(editor)

    def _get_children(self, data_model, doc, root, parent, comm) -> tuple[dict[str, list[UIElement] | UIElement | None], list[UIElement]]:
        views = []
        node_editors = []
        for node in self.nodes:
            view = node.get("view", None)
            if view is not None:
                views.append(view)
            node_editors.append(self._resolve_editor_view(self._node_editors.get(node.get("id"))))
        edge_editors = [self._resolve_editor_view(self._edge_editors.get(edge.get("id"))) for edge in self.edges]

        children: dict[str, list[UIElement] | UIElement | None] = {}
        old_models: list[UIElement] = []
        if views:
            views, view_models = self._get_child_model(views, doc, root, parent, comm)
            children["_views"] = views
            old_models += view_models
        if node_editors:
            editor_models, editor_old = self._get_child_model(node_editors, doc, root, parent, comm)
            children["_node_editor_views"] = editor_models
            old_models += editor_old
        if edge_editors:
            edge_models, edge_old = self._get_child_model(edge_editors, doc, root, parent, comm)
            children["_edge_editor_views"] = edge_models
            old_models += edge_old
        for name in ("top_panel", "bottom_panel", "left_panel", "right_panel"):
            panels = list(getattr(self, name, []) or [])
            if panels:
                panel_models, panel_old = self._get_child_model(panels, doc, root, parent, comm)
                children[name] = panel_models
                old_models += panel_old
            else:
                children[name] = []
        return children, old_models

    def _process_param_change(self, params):
        params = super()._process_param_change(params)
        if "nodes" in params:
            nodes = []
            view_idx = 0
            for node in params["nodes"]:
                node = dict(node)
                view = node.pop("view", None)
                data = dict(node.get("data", {}))
                if view is not None:
                    data["view_idx"] = view_idx
                    view_idx += 1
                node["data"] = data
                nodes.append(node)
            params["nodes"] = nodes
        # node_types / edge_types are now JSON-serializable descriptors
        # and intentionally synced to the frontend.
        # Pop Python-only editor registries and internal state.
        params.pop("node_editors", None)
        params.pop("edge_editors", None)
        params.pop("default_node_editor", None)
        params.pop("default_edge_editor", None)
        params.pop("validate_on_add", None)
        params.pop("validate_on_patch", None)
        params.pop("_node_editors", None)
        params.pop("_edge_editors", None)
        return params

    def add_node(self, node: dict[str, Any] | NodeSpec, *, view: Any | None = None) -> None:
        """Add a node to the graph.

        Parameters
        ----------
        node:
            Node dictionary or ``NodeSpec`` instance to add.
        view:
            Optional Panel viewable rendered inside the node. If provided,
            ``view`` is attached to the node and transformed into ``view_idx``.
        """
        payload = self._coerce_node(node)
        payload.setdefault("type", "panel")
        payload.setdefault("data", {})
        payload.setdefault("position", {"x": 0.0, "y": 0.0})
        self._validate_graph_payload(payload, kind="node")
        if self.validate_on_add:
            schema = self._get_node_schema(payload.get("type", "panel"))
            _validate_data(payload.get("data", {}), schema)
        self.nodes = self.nodes + [dict(payload, view=view)]
        self._emit("node_added", {"type": "node_added", "node": payload})

    def _handle_msg(self, msg: dict[str, Any]) -> None:
        """Handle sync messages from the frontend."""
        if not isinstance(msg, dict):
            return
        match msg.get("type"):
            case "sync":
                nodes = msg.get("nodes")
                edges = msg.get("edges")
                if nodes is not None:
                    self.nodes = nodes
                if edges is not None:
                    self.edges = edges
                self._emit("sync", msg)
            case "node_moved":
                node_id = msg.get("node_id")
                position = msg.get("position")
                if node_id is None or position is None:
                    return
                for node in self.nodes:
                    if node.get("id") == node_id:
                        node["position"] = position
                self._emit("node_moved", msg)
            case "selection_changed":
                node_ids = msg.get("nodes") or []
                edge_ids = msg.get("edges") or []
                for node in self.nodes:
                    node["selected"] = node.get("id") in node_ids
                for edge in self.edges:
                    edge["selected"] = edge.get("id") in edge_ids
                self.selection = {"nodes": list(node_ids), "edges": list(edge_ids)}
                self._emit("selection_changed", msg)
            case "edge_added":
                edge = msg.get("edge")
                if edge is None:
                    return
                self.add_edge(edge)
                self._emit("edge_added", msg)
            case "node_deleted":
                node_ids = msg.get("node_ids") or []
                if msg.get("node_id"):
                    node_ids = list(set(node_ids) | {msg.get("node_id")})
                for node_id in node_ids:
                    self.remove_node(node_id)
                self._emit("node_deleted", msg)
            case "edge_deleted":
                edge_ids = msg.get("edge_ids") or []
                if msg.get("edge_id"):
                    edge_ids = list(set(edge_ids) | {msg.get("edge_id")})
                for edge_id in edge_ids:
                    self.remove_edge(edge_id)
                self._emit("edge_deleted", msg)
            case "node_clicked":
                node_id = msg.get("node_id")
                if node_id is None:
                    return
                self._emit("node_clicked", msg)
            case _:
                return

    def remove_node(self, node_id: str) -> None:
        """Remove a node and any connected edges.

        Parameters
        ----------
        node_id:
            Identifier of the node to remove.
        """
        nodes = [node for node in self.nodes if node.get("id") != node_id]
        removed_edges = [edge for edge in self.edges if edge.get("source") == node_id or edge.get("target") == node_id]
        self.nodes = nodes
        if removed_edges:
            remaining_edges = [edge for edge in self.edges if edge not in removed_edges]
            self.edges = remaining_edges
        self._emit(
            "node_deleted",
            {
                "type": "node_deleted",
                "node_id": node_id,
                "deleted_edges": [edge.get("id") for edge in removed_edges],
            },
        )

    def add_edge(self, edge: dict[str, Any] | EdgeSpec) -> None:
        """Add an edge to the graph.

        Parameters
        ----------
        edge:
            Edge dictionary or ``EdgeSpec`` instance to add.
        """
        payload = self._coerce_edge(edge)
        payload.setdefault("data", {})
        if not payload.get("id"):
            payload["id"] = self._generate_edge_id(payload["source"], payload["target"])
        self._validate_graph_payload(payload, kind="edge")
        if self.validate_on_add:
            edge_type = payload.get("type")
            schema = self._get_edge_schema(edge_type) if edge_type else None
            _validate_data(payload.get("data", {}), schema)
        self.edges = self.edges + [payload]
        self._emit("edge_added", {"type": "edge_added", "edge": payload})

    def remove_edge(self, edge_id: str) -> None:
        """Remove an edge by id.

        Parameters
        ----------
        edge_id:
            Identifier of the edge to remove.
        """
        removed = [edge for edge in self.edges if edge.get("id") == edge_id]
        self.edges = [edge for edge in self.edges if edge.get("id") != edge_id]
        if removed:
            self._emit("edge_deleted", {"type": "edge_deleted", "edge_id": edge_id})

    def patch_node_data(self, node_id: str, patch: dict[str, Any]) -> None:
        """Patch the ``data`` dict for a node.

        Parameters
        ----------
        node_id:
            Identifier of the node to update.
        patch:
            Dictionary of key/value pairs merged into ``node["data"]``.
        """
        for node in self.nodes:
            if node.get("id") == node_id:
                data = dict(node.get("data", {}))
                data.update(patch)
                if self.validate_on_patch:
                    schema = self._get_node_schema(node.get("type", "panel"))
                    _validate_data(data, schema)
                node["data"] = data
                break
        self._send_msg({"type": "patch_node_data", "node_id": node_id, "patch": patch})
        self._emit("node_data_changed", {"type": "node_data_changed", "node_id": node_id, "patch": patch})

    def patch_edge_data(self, edge_id: str, patch: dict[str, Any]) -> None:
        """Patch the ``data`` dict for an edge.

        Parameters
        ----------
        edge_id:
            Identifier of the edge to update.
        patch:
            Dictionary of key/value pairs merged into ``edge["data"]``.
        """
        for edge in self.edges:
            if edge.get("id") == edge_id:
                data = dict(edge.get("data", {}))
                data.update(patch)
                if self.validate_on_patch:
                    edge_type = edge.get("type")
                    schema = self._get_edge_schema(edge_type) if edge_type else None
                    _validate_data(data, schema)
                edge["data"] = data
                break
        self._send_msg({"type": "patch_edge_data", "edge_id": edge_id, "patch": patch})
        self._emit("edge_data_changed", {"type": "edge_data_changed", "edge_id": edge_id, "patch": patch})

    def to_networkx(self, *, multigraph: bool = False):
        """Convert the current graph state to a NetworkX graph.

        Parameters
        ----------
        multigraph:
            Whether to return a ``MultiDiGraph`` instead of a ``DiGraph``.

        Returns
        -------
        networkx.Graph
            NetworkX representation of the graph.
        """
        try:
            import networkx as nx  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            raise ImportError("networkx is required for to_networkx.") from exc

        graph = nx.MultiDiGraph() if multigraph else nx.DiGraph()
        for node in self.nodes:
            data = dict(node.get("data", {}))
            data.update({"position": node.get("position"), "type": node.get("type")})
            if node.get("label") is not None:
                data["label"] = node.get("label")
            graph.add_node(node["id"], **data)
        for edge in self.edges:
            data = dict(edge.get("data", {}))
            data.update({"label": edge.get("label"), "type": edge.get("type")})
            graph.add_edge(edge["source"], edge["target"], key=edge.get("id"), **data)
        return graph

    @classmethod
    def from_networkx(
        cls,
        graph,
        *,
        node_type: str = "panel",
        default_position: tuple[float, float] = (0.0, 0.0),
    ) -> "ReactFlow":
        """Create a ReactFlow instance from a NetworkX graph.

        Parameters
        ----------
        graph:
            A NetworkX graph instance.
        node_type:
            Default node type assigned to nodes.
        default_position:
            Default (x, y) position when none is provided in attributes.

        Returns
        -------
        ReactFlow
            ReactFlow instance populated with nodes and edges.
        """
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for node_id, attrs in graph.nodes(data=True):
            position = attrs.pop("position", {"x": default_position[0], "y": default_position[1]})
            if isinstance(position, (tuple, list)):
                position = {"x": position[0], "y": position[1]}
            label = attrs.pop("label", None)
            node_data = dict(attrs)
            node_data.pop("type", None)
            embedded_data = node_data.pop("data", None)
            if isinstance(embedded_data, dict):
                node_data = {**embedded_data, **node_data}
            node_payload = {"id": str(node_id), "position": position, "type": node_type, "data": node_data}
            if label is not None:
                node_payload["label"] = label
            nodes.append(node_payload)
        if graph.is_multigraph():
            edge_iter = graph.edges(keys=True, data=True)
        else:
            edge_iter = ((source, target, None, attrs) for source, target, attrs in graph.edges(data=True))
        for source, target, key, attrs in edge_iter:
            edge_data = dict(attrs)
            embedded_edge_data = edge_data.pop("data", None)
            if isinstance(embedded_edge_data, dict):
                edge_data = {**embedded_edge_data, **edge_data}
            label = edge_data.pop("label", None)
            edge_type = edge_data.pop("type", None)
            edge_id = key if key is not None else f"{source}->{target}"
            edge = {
                "id": str(edge_id),
                "source": str(source),
                "target": str(target),
                "data": edge_data,
            }
            if label is not None:
                edge["label"] = label
            if edge_type is not None:
                edge["type"] = edge_type
            edges.append(edge)
        return cls(nodes=nodes, edges=edges)

    def on(self, event_type: str, callback) -> None:
        """Register a Python callback for frontend events.

        Parameters
        ----------
        event_type:
            Event name to listen for (e.g. ``node_moved``). Use ``*`` for all events.
        callback:
            Callable invoked with the event payload.
        """
        self._event_handlers.setdefault(event_type, []).append(callback)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        for callback in self._event_handlers.get(event_type, []):
            callback(payload)
        for callback in self._event_handlers.get("*", []):
            callback(payload)

    def _update_selection_from_graph(self, *_: param.parameterized.Event) -> None:
        selection = {
            "nodes": [node["id"] for node in self.nodes if node.get("selected")],
            "edges": [edge["id"] for edge in self.edges if edge.get("selected")],
        }
        if selection != self.selection:
            self.selection = selection
            self._emit(
                "selection_changed",
                {"type": "selection_changed", "nodes": selection["nodes"], "edges": selection["edges"]},
            )

    def _normalize_specs(self, event: param.parameterized.Event) -> None:
        is_edge = event.name == "edge_types"
        normalized = _coerce_spec_map(event.new, edge=is_edge)
        if normalized != event.new:
            setattr(self, event.name, normalized)

    @staticmethod
    def _generate_edge_id(source: str, target: str) -> str:
        existing = f"{source}->{target}"
        return f"{existing}-{uuid4().hex[:8]}"

    @staticmethod
    def _coerce_node(node: dict[str, Any] | NodeSpec) -> dict[str, Any]:
        return node.to_dict() if hasattr(node, "to_dict") else dict(node)

    @staticmethod
    def _coerce_edge(edge: dict[str, Any] | EdgeSpec) -> dict[str, Any]:
        return edge.to_dict() if hasattr(edge, "to_dict") else dict(edge)

    def _validate_graph_payload(self, payload: dict[str, Any], *, kind: str) -> None:
        required = {"node": ["id", "position", "data"], "edge": ["id", "source", "target"]}[kind]
        for key in required:
            if key not in payload:
                raise ValueError(f"Missing '{key}' in {kind} payload.")
