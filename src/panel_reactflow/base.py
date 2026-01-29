"""Core React Flow component and helpers."""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import param
from bokeh.embed.bundle import extension_dirs
from panel.config import config
from panel.custom import Children, ReactComponent
from panel.io.resources import EXTENSION_CDN
from panel.io.state import state
from panel.util import base_version, classproperty

from .__version import __version__  # noqa

IS_RELEASE = __version__ == base_version(__version__)
BASE_PATH = Path(__file__).parent
DIST_PATH = BASE_PATH / 'dist'
CDN_BASE = f"https://cdn.holoviz.org/panel-reactflow/v{base_version(__version__)}"
CDN_DIST = f"{CDN_BASE}/panel-reactflow.bundle.js"

extension_dirs['panel-reactflow'] = DIST_PATH
EXTENSION_CDN[DIST_PATH] = CDN_BASE


def _ensure_jsonable(value: Any, path: str) -> None:
    """Ensure value can be JSON-serialized for syncing to the frontend."""
    import json

    try:
        json.dumps(value)
    except Exception as exc:
        raise ValueError(f"Value at {path} is not JSON-serializable.") from exc


def _coerce_spec_map(specs: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not specs:
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, value in specs.items():
        if hasattr(value, "to_dict"):
            normalized[key] = value.to_dict()
        elif isinstance(value, dict):
            normalized[key] = value
        else:
            raise ValueError(f"Unsupported spec type for '{key}'.")
    return normalized


@dataclass
class PropertySpec:
    """Schema definition for a node or edge property."""

    name: str
    type: str = "str"
    default: Any = None
    label: str | None = None
    help: str | None = None
    choices: list[Any] | None = None
    format: str | None = None
    visible_in_node: bool = False
    editable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PropertySpec":
        return cls(**payload)


@dataclass
class NodeTypeSpec:
    """Schema definition for a node type."""

    type: str
    label: str | None = None
    properties: list[PropertySpec] | None = None
    inputs: list[str] | None = None
    outputs: list[str] | None = None
    pane_policy: str = "single"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["properties"] = [p.to_dict() for p in self.properties or []]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NodeTypeSpec":
        props = [PropertySpec.from_dict(p) for p in payload.get("properties", [])]
        return cls(**{**payload, "properties": props})


@dataclass
class EdgeTypeSpec:
    """Schema definition for an edge type."""

    type: str
    properties: list[PropertySpec] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["properties"] = [p.to_dict() for p in self.properties or []]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EdgeTypeSpec":
        props = [PropertySpec.from_dict(p) for p in payload.get("properties", [])]
        return cls(**{**payload, "properties": props})


@dataclass
class NodeSpec:
    """Helper for constructing node dictionaries."""

    id: str
    position: dict[str, float] | dict[str, Any] = None
    type: str = "panel"
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


class ReactFlow(ReactComponent):
    """React Flow component wrapper."""

    nodes = param.List(default=[], doc="Canonical list of node dictionaries.")
    edges = param.List(default=[], doc="Canonical list of edge dictionaries.")
    node_types = param.Dict(default={}, doc="Node type schema definitions keyed by type name.")
    edge_types = param.Dict(default={}, doc="Edge type schema definitions keyed by type name.")
    default_edge_options = param.Dict(default={}, doc="Default React Flow edge options.")
    selection = param.Dict(default={"nodes": [], "edges": []}, doc="Derived selection state for node and edge ids.")
    viewport = param.Dict(default=None, allow_None=True, doc="Optional persisted viewport state.")
    sync_mode = param.ObjectSelector(default="event", objects=["event", "debounce"], doc="Sync mode for JS->Python updates.")
    debounce_ms = param.Integer(default=150, bounds=(0, None), doc="Debounce delay in milliseconds when sync_mode='debounce'.")
    editable = param.Boolean(default=True, doc="Enable interactive editing on the canvas.")
    enable_connect = param.Boolean(default=True, doc="Allow connecting nodes to create edges.")
    enable_delete = param.Boolean(default=True, doc="Allow deleting selected nodes or edges.")
    enable_multiselect = param.Boolean(default=True, doc="Allow multiselect with modifier key.")
    show_minimap = param.Boolean(default=True, doc="Show the minimap overlay.")
    _views = Children(default=[], doc="Panel viewables rendered inside nodes via view_idx.")

    _bundle = DIST_PATH / "panel-reactflow.bundle.js"
    _esm = Path(__file__).parent / "models" / "reactflow.jsx"
    _importmap = {"imports": {"@xyflow/react": "https://esm.sh/@xyflow/react@12.8.3"}}
    _stylesheets = [DIST_PATH / "panel-reactflow.bundle.css", DIST_PATH / "css" / "reactflow.css"]

    def __init__(self, **params: Any):
        params["node_types"] = _coerce_spec_map(params.get("node_types"))
        params["edge_types"] = _coerce_spec_map(params.get("edge_types"))
        super().__init__(**params)
        self._event_handlers: dict[str, list[callable]] = {"*": []}
        self.param.watch(self._update_selection_from_graph, ["nodes", "edges"])
        self.param.watch(self._normalize_specs, ["node_types", "edge_types"])

    @classmethod
    def _esm_path(cls, compiled: bool | Literal['compiling'] = True) -> os.PathLike | None:
        return super()._esm_path(compiled or True)
        
    @classmethod
    def _render_esm(cls, compiled: bool | Literal['compiling'] = True, server: bool = False):
        esm_path = cls._esm_path(compiled=compiled)
        if compiled != "compiling" and server:
            # Generate relative path to handle apps served on subpaths
            esm = ('' if state.rel_path else './') + cls._component_resource_path(esm_path, compiled)
            if config.autoreload:
                modified = hashlib.sha256(str(esm_path.stat().st_mtime).encode('utf-8')).hexdigest()
                esm += f'?{modified}'
        else:
            esm = esm_path.read_text(encoding='utf-8')
        return esm

    @classproperty
    def _bundle_path(cls) -> os.PathLike | None:
        return cls._bundle

    def _get_children(self, data_model, doc, root, parent, comm) -> tuple[dict[str, list[UIElement] | UIElement | None], list[UIElement]]:
        # Look for Panel views in the "view" key of each node
        views = []
        for node in self.nodes:
            view = node.get("view", None)
            if view is not None:
                views.append(view)
        print(views)
        if views:
            views, old_models = self._get_child_model(views, doc, root, parent, comm)
        else:
            old_models = []
        return {"_views": views}, old_models

    def _process_param_change(self, params):
        params = super()._process_param_change(params)
        if "nodes" in params and any("view" in node for node in params["nodes"]):
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
        self.nodes = self.nodes + [dict(payload, view=view)]
        self._emit("node_added", {"type": "node_added", "node": payload})

    def _handle_msg(self, msg: dict[str, Any]) -> None:
        """Handle sync messages from the frontend."""
        if not isinstance(msg, dict):
            return
        msg_type = msg.get("type")
        if msg_type == "sync":
            nodes = msg.get("nodes")
            edges = msg.get("edges")
            if nodes is not None:
                self.nodes = nodes
            if edges is not None:
                self.edges = edges
            self._emit(msg_type, msg)
            return
        if msg_type == "node_moved":
            node_id = msg.get("node_id")
            position = msg.get("position")
            if node_id is None or position is None:
                return
            nodes = []
            for node in self.nodes:
                if node.get("id") == node_id:
                    node = {**node, "position": position}
                nodes.append(node)
            self.nodes = nodes
            self._emit(msg_type, msg)
            return
        if msg_type == "selection_changed":
            node_ids = msg.get("nodes") or []
            edge_ids = msg.get("edges") or []
            nodes = [{**node, "selected": node.get("id") in node_ids} for node in self.nodes]
            edges = [{**edge, "selected": edge.get("id") in edge_ids} for edge in self.edges]
            self.nodes = nodes
            self.edges = edges
            self.selection = {"nodes": list(node_ids), "edges": list(edge_ids)}
            self._emit(msg_type, msg)
            return
        if msg_type == "edge_added":
            edge = msg.get("edge")
            if edge is None:
                return
            self.add_edge(edge)
            self._emit(msg_type, msg)
            return
        if msg_type == "node_deleted":
            node_ids = msg.get("node_ids") or []
            if msg.get("node_id"):
                node_ids = list(set(node_ids) | {msg.get("node_id")})
            for node_id in node_ids:
                self.remove_node(node_id)
            self._emit(msg_type, msg)
            return
        if msg_type == "edge_deleted":
            edge_ids = msg.get("edge_ids") or []
            if msg.get("edge_id"):
                edge_ids = list(set(edge_ids) | {msg.get("edge_id")})
            for edge_id in edge_ids:
                self.remove_edge(edge_id)
            self._emit(msg_type, msg)
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
            }
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
        nodes = []
        for node in self.nodes:
            if node.get("id") == node_id:
                data = dict(node.get("data", {}))
                data.update(patch)
                node = {**node, "data": data}
            nodes.append(node)
        self.nodes = nodes
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
        edges = []
        for edge in self.edges:
            if edge.get("id") == edge_id:
                data = dict(edge.get("data", {}))
                data.update(patch)
                edge = {**edge, "data": data}
            edges.append(edge)
        self.edges = edges
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
            node_data = dict(attrs)
            node_data.pop("type", None)
            nodes.append({"id": str(node_id), "position": position, "type": node_type, "data": node_data})
        for source, target, key, attrs in graph.edges(keys=True, data=True):
            edge_data = dict(attrs)
            label = edge_data.pop("label", None)
            edge_type = edge_data.pop("type", None)
            edge_id = key if key is not None else f"{source}->{target}"
            edges.append(
                {
                    "id": str(edge_id),
                    "source": str(source),
                    "target": str(target),
                    "label": label,
                    "type": edge_type,
                    "data": edge_data,
                }
            )
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

    def _normalize_specs(self, event: param.parameterized.Event) -> None:
        normalized = _coerce_spec_map(event.new)
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
        self._validate_against_schema(payload, kind=kind)
        sanitized = dict(payload)
        sanitized.pop("view", None)
        _ensure_jsonable(sanitized, f"{kind}:{payload.get('id')}")

    def _validate_against_schema(self, payload: dict[str, Any], *, kind: str) -> None:
        if kind == "node":
            raw_schema = self.node_types.get(payload.get("type", "panel"))
        else:
            edge_type = payload.get("type")
            raw_schema = self.edge_types.get(edge_type) if edge_type else None
        if not raw_schema:
            return
        if kind == "node":
            schema = raw_schema if isinstance(raw_schema, NodeTypeSpec) else NodeTypeSpec.from_dict(raw_schema)
        else:
            schema = raw_schema if isinstance(raw_schema, EdgeTypeSpec) else EdgeTypeSpec.from_dict(raw_schema)
        properties = schema.properties or []
        data = payload.get("data", {})
        for prop in properties:
            name = prop.name
            if not name:
                continue
            value = data.get(name, prop.default)
            if value is None:
                continue
            expected = prop.type
            if expected == "str" and not isinstance(value, str):
                raise ValueError(f"{kind} data '{name}' must be a string.")
            if expected == "int" and not isinstance(value, int):
                raise ValueError(f"{kind} data '{name}' must be an int.")
            if expected == "float" and not isinstance(value, (int, float)):
                raise ValueError(f"{kind} data '{name}' must be a float.")
            if expected == "bool" and not isinstance(value, bool):
                raise ValueError(f"{kind} data '{name}' must be a bool.")
            if expected == "enum":
                choices = prop.choices or []
                if value not in choices:
                    raise ValueError(f"{kind} data '{name}' must be one of {choices}.")
            if expected == "json":
                _ensure_jsonable(value, f"{kind}:{payload.get('id')}:{name}")
