"""Core React Flow component and helpers."""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import logging
import os
import weakref
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, is_dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import panel as pn
import param
from bokeh.core.serialization import Serializer
from bokeh.embed.bundle import extension_dirs
from bokeh.plotting import figure
from panel.config import config
from panel.custom import Child, Children, ReactComponent
from panel.io.document import hold
from panel.io.resources import EXTENSION_CDN
from panel.io.state import state
from panel.util import base_version, classproperty
from panel.viewable import Viewer
from panel.widgets import JSONEditor

from .__version import __version__  # noqa

if TYPE_CHECKING:
    from bokeh.models import UIElement

_LOGGER = logging.getLogger("panel.reactflow")

IS_RELEASE = __version__ == base_version(__version__)
BASE_PATH = Path(__file__).parent
DIST_PATH = BASE_PATH / "dist"
CDN_BASE = f"https://cdn.holoviz.org/panel-reactflow/v{base_version(__version__)}"
CDN_DIST = f"{CDN_BASE}/panel-reactflow.bundle.js"

extension_dirs["panel-reactflow"] = DIST_PATH
EXTENSION_CDN[DIST_PATH] = CDN_BASE

BK_FIGURE_CSS = """
.bk-Canvas {
  transform: scale(var(--rf-inverse-zoom));
  transform-origin: top left;
  width: calc(var(--rf-zoom) * 100%);
  height: calc(var(--rf-zoom) * 100%);
}
"""


def _flatten_ids(ids: Sequence[str | Sequence[str]]) -> list[str]:
    """Flatten mixed id arguments, i.e. bare ids and sequences of ids."""
    flat: list[str] = []
    for id_or_ids in ids:
        if isinstance(id_or_ids, str):
            flat.append(id_or_ids)
        else:
            flat.extend(id_or_ids)
    return flat


def _ensure_jsonable(value: Any, path: str) -> None:
    """Ensure *value* can be synced to the browser.

    Everything Bokeh can serialize is accepted, e.g. NumPy scalars and arrays
    or datetimes, except sets and dataclasses, which the browser receives as
    a JS ``Set`` or cannot decode at all. Plain JSON takes a fast path.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError, OverflowError):
        _check_serializable(value, path, set())


def _check_serializable(value: Any, path: str, seen: set[int]) -> None:
    if isinstance(value, (dict, list, tuple)):
        if id(value) in seen:
            raise ValueError(f"Value at {path} contains a circular reference.")
        seen.add(id(value))
        if isinstance(value, dict):
            for key, item in value.items():
                _check_serializable(key, f"{path} (key {key!r})", seen)
                _check_serializable(item, f"{path}[{key!r}]", seen)
        else:
            for index, item in enumerate(value):
                _check_serializable(item, f"{path}[{index}]", seen)
        seen.discard(id(value))
        return
    if isinstance(value, (set, frozenset)) or (is_dataclass(value) and not isinstance(value, type)):
        raise ValueError(f"Value at {path} cannot be sent to the browser: {type(value).__name__} is not supported, convert it to a list or dict.")
    try:
        Serializer().encode(value)
    except Exception as exc:
        raise ValueError(f"Value at {path} cannot be sent to the browser: {type(value).__name__} is not serializable.") from exc


def _decode_dropped_files(files: Any) -> Any:
    """Turn the wire format of a ``"Files"`` drop into Python values.

    The browser sends files that decode as UTF-8 as text and other files
    base64 encoded; the latter are decoded to ``bytes`` here so handlers
    get ``str`` or ``bytes`` content without dealing with the encoding.
    """
    if not isinstance(files, list):
        return files
    decoded = []
    for entry in files:
        if isinstance(entry, dict) and "encoding" in entry:
            entry = dict(entry)
            if entry.pop("encoding") == "base64" and isinstance(entry.get("content"), str):
                entry["content"] = base64.b64decode(entry["content"])
        decoded.append(entry)
    return decoded


class _GraphConflictError(ValueError):
    """A node or edge conflicts with the graph: duplicate id or unknown endpoint."""


# Bound methods are keyed by their function, so no instance is kept alive,
# but in a separate cache since binding consumes the first parameter.
_ACCEPTS_FLOW: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_ACCEPTS_FLOW_BOUND: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _accepts_flow(callback: Callable) -> bool:
    """Whether *callback* can be called as ``callback(payload, flow)``.

    Binding against the signature (rather than counting parameters) handles
    defaults, ``*args`` and callable instances.
    """
    func = getattr(callback, "__func__", None)
    cache, key = (_ACCEPTS_FLOW, callback) if func is None else (_ACCEPTS_FLOW_BOUND, func)
    try:
        return cache[key]
    except (KeyError, TypeError):
        pass
    try:
        inspect.signature(callback).bind(None, None)
    except (TypeError, ValueError):
        accepts = False
    else:
        accepts = True
    try:
        cache[key] = accepts
    except TypeError:
        pass
    return accepts


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


def _parameterized_data_param_names(parameterized_cls: type[param.Parameterized], base_cls: type[param.Parameterized]) -> list[str]:
    """Return subclass-defined parameter names included in node/edge data.

    Only parameters with explicitly non-negative precedence are included.
    """
    base_params = set(base_cls.param)
    names: list[str] = []
    for name in parameterized_cls.param:
        if name in base_params or name.startswith("_"):
            continue
        precedence = parameterized_cls.param[name].precedence
        if precedence is not None and precedence >= 0:
            names.append(name)
    return names


def _parameterized_data_schema(parameterized_cls: type[param.Parameterized], base_cls: type[param.Parameterized]) -> dict[str, Any]:
    """Build a JSON Schema for subclass-defined data parameters."""
    names = _parameterized_data_param_names(parameterized_cls, base_cls)
    schema = _param_to_jsonschema(parameterized_cls)
    properties = schema.get("properties", {})
    return {"type": "object", "properties": {name: properties[name] for name in names if name in properties}}


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
    """Validate *data* against a JSON Schema."""
    if schema is None:
        return
    import jsonschema as _js

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
        spec = normalized[key]
        # A lone handle (``inputs="in"``) would otherwise be iterated per character.
        if any(isinstance(spec.get(port), (str, dict)) for port in ("inputs", "outputs")):
            spec = dict(spec)
            for port in ("inputs", "outputs"):
                if isinstance(spec.get(port), (str, dict)):
                    spec[port] = [spec[port]]
            normalized[key] = spec
    return normalized


@dataclass
class SchemaSource:
    """Explicit schema source wrapper for type definitions.

    Use this wrapper when you need to explicitly specify the schema format
    for node or edge types. This is useful when automatic detection might
    be ambiguous or when you want to be explicit about the schema source.

    Parameters
    ----------
    kind : {"jsonschema", "param", "pydantic"}
        The schema format type:

        - ``"jsonschema"``: A standard JSON Schema dictionary
        - ``"param"``: A ``param.Parameterized`` class
        - ``"pydantic"``: A Pydantic ``BaseModel`` class
    value : dict or type
        The schema value matching the specified ``kind``:

        - For ``"jsonschema"``: A JSON Schema dictionary
        - For ``"param"``: A ``param.Parameterized`` subclass
        - For ``"pydantic"``: A Pydantic ``BaseModel`` subclass

    Examples
    --------
    Using a JSON Schema:

    >>> from panel_reactflow import SchemaSource
    >>> schema = SchemaSource(
    ...     kind="jsonschema",
    ...     value={"type": "object", "properties": {"name": {"type": "string"}}}
    ... )

    Using a Param class:

    >>> import param
    >>> class MyParams(param.Parameterized):
    ...     label = param.String(default="")
    >>> schema = SchemaSource(kind="param", value=MyParams)

    Using a Pydantic model:

    >>> from pydantic import BaseModel
    >>> class MyModel(BaseModel):
    ...     name: str
    >>> schema = SchemaSource(kind="pydantic", value=MyModel)
    """

    kind: Literal["jsonschema", "param", "pydantic"]
    value: Any


@dataclass
class NodeType:
    """Define a custom node type with schema and port configuration.

    Node types allow you to define reusable node templates with specific data
    schemas, input/output ports, and display policies. When nodes are created
    with this type, they automatically get schema validation and appropriate
    editors.

    Parameters
    ----------
    type : str
        Unique identifier for this node type. Used to reference this type
        when creating nodes.
    label : str, optional
        Human-readable display name for this node type. If not provided,
        the ``type`` value is used.
    schema : dict or type, optional
        Data schema for node validation and editor generation. Accepts:

        - A JSON Schema dictionary
        - A ``param.Parameterized`` subclass
        - A Pydantic ``BaseModel`` subclass
        - A :class:`SchemaSource` wrapper for explicit schema types

        The schema is normalized to JSON Schema format internally.
    inputs : list of str or dict, optional
        List of input port definitions. Each entry can be a plain string
        (the handle ID) or a dict with ``"id"`` and optional ``"label"``
        and ``"type"`` keys. An optional ``"maxConnections"`` integer limits
        connections when ``connection_validation["capacity"]`` is enabled.
        Labels and types render as tooltips on hover.
    outputs : list of str or dict, optional
        List of output port definitions. Each entry can be a plain string
        (the handle ID) or a dict with ``"id"`` and optional ``"label"``
        and ``"type"`` keys. When a label and/or type is provided it renders
        as a tooltip on hover, e.g. ``"Raw data input (DataFrame)"``.
    input_connectable : bool, default True
        Whether input handles are connectable. When False, users cannot create
        connections to or from input handles.
    input_connectable_start : bool, default True
        Whether new edges can start from input handles. Set to False to prevent
        dragging out from input handles while still allowing edges to terminate there.
    input_connectable_end : bool, default True
        Whether new edges can end at input handles. Set to False to prevent
        edges from terminating at input handles while still allowing dragging from them.
    output_connectable : bool, default True
        Whether output handles are connectable. When False, users cannot create
        connections to or from output handles.
    output_connectable_start : bool, default True
        Whether new edges can start from output handles. Set to False to prevent
        dragging out from output handles while still allowing edges to terminate there.
    output_connectable_end : bool, default True
        Whether new edges can end at output handles. Set to False to prevent
        edges from terminating at output handles while still allowing dragging from them.
    pane_policy : str, default "single"
        Display policy for Panel viewables inside nodes.

    Methods
    -------
    to_dict()
        Convert this node type to a JSON-serializable dictionary.

    Examples
    --------
    Define a simple node type with a JSON Schema:

    >>> from panel_reactflow import NodeType
    >>> transform_type = NodeType(
    ...     type="transform",
    ...     label="Data Transform",
    ...     schema={
    ...         "type": "object",
    ...         "properties": {
    ...             "operation": {"type": "string", "enum": ["filter", "map", "reduce"]},
    ...             "parameter": {"type": "number"}
    ...         }
    ...     },
    ...     inputs=["input"],
    ...     outputs=["output"]
    ... )

    Define a node type with a Param class:

    >>> import param
    >>> class TransformParams(param.Parameterized):
    ...     operation = param.Selector(default="filter", objects=["filter", "map", "reduce"])
    ...     parameter = param.Number(default=1.0)
    >>> transform_type = NodeType(
    ...     type="transform",
    ...     label="Data Transform",
    ...     schema=TransformParams,
    ...     inputs=["input"],
    ...     outputs=["output"]
    ... )

    Define a sink node that accepts connections but cannot be a source:

    >>> sink_type = NodeType(
    ...     type="sink",
    ...     label="Data Sink",
    ...     inputs=["in"],
    ...     outputs=["status"],
    ...     input_connectable_start=False,  # Cannot drag from input handles
    ...     output_connectable_end=False,   # Cannot drag to output handles
    ... )

    Use the node type in a ReactFlow graph:

    >>> from panel_reactflow import ReactFlow, NodeSpec
    >>> flow = ReactFlow(node_types={"transform": transform_type})
    >>> flow.add_node(NodeSpec(
    ...     id="t1",
    ...     type="transform",
    ...     position={"x": 100, "y": 100},
    ...     data={"operation": "filter", "parameter": 2.5}
    ... ))
    """

    type: str
    label: str | None = None
    schema: Any = None
    inputs: list[str | dict[str, Any]] | None = None
    outputs: list[str | dict[str, Any]] | None = None
    input_connectable: bool = True
    input_connectable_start: bool = True
    input_connectable_end: bool = True
    output_connectable: bool = True
    output_connectable_start: bool = True
    output_connectable_end: bool = True
    pane_policy: str = "single"

    def to_dict(self) -> dict[str, Any]:
        """Convert the node type to a JSON-serializable dictionary.

        Returns
        -------
        dict
            Dictionary representation with normalized schema.
        """
        return {
            "type": self.type,
            "label": self.label,
            "schema": _normalize_schema(self.schema),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "inputConnectable": self.input_connectable,
            "inputConnectableStart": self.input_connectable_start,
            "inputConnectableEnd": self.input_connectable_end,
            "outputConnectable": self.output_connectable,
            "outputConnectableStart": self.output_connectable_start,
            "outputConnectableEnd": self.output_connectable_end,
            "pane_policy": self.pane_policy,
        }


@dataclass
class EdgeType:
    """Define a custom edge type with schema for edge properties.

    Edge types allow you to define reusable edge templates with specific data
    schemas for validation and editor generation. Use this when your edges
    have custom properties beyond the basic source/target relationship.

    Parameters
    ----------
    type : str
        Unique identifier for this edge type. Used to reference this type
        when creating edges.
    label : str, optional
        Human-readable display name for this edge type. If not provided,
        the ``type`` value is used.
    schema : dict or type, optional
        Data schema for edge validation and editor generation. Accepts the
        same formats as :class:`NodeType`:

        - A JSON Schema dictionary
        - A ``param.Parameterized`` subclass
        - A Pydantic ``BaseModel`` subclass
        - A :class:`SchemaSource` wrapper for explicit schema types

    Methods
    -------
    to_dict()
        Convert this edge type to a JSON-serializable dictionary.

    Examples
    --------
    Define an edge type with properties:

    >>> from panel_reactflow import EdgeType
    >>> weighted_edge = EdgeType(
    ...     type="weighted",
    ...     label="Weighted Connection",
    ...     schema={
    ...         "type": "object",
    ...         "properties": {
    ...             "weight": {"type": "number", "minimum": 0, "maximum": 1},
    ...             "label": {"type": "string"}
    ...         }
    ...     }
    ... )

    Use the edge type in a ReactFlow graph:

    >>> from panel_reactflow import ReactFlow, EdgeSpec
    >>> flow = ReactFlow(edge_types={"weighted": weighted_edge})
    >>> flow.add_edge(EdgeSpec(
    ...     id="e1",
    ...     source="n1",
    ...     target="n2",
    ...     type="weighted",
    ...     data={"weight": 0.75, "label": "strong"}
    ... ))
    """

    type: str
    label: str | None = None
    schema: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the edge type to a JSON-serializable dictionary.

        Returns
        -------
        dict
            Dictionary representation with normalized schema.
        """
        return {
            "type": self.type,
            "label": self.label,
            "schema": _normalize_schema(self.schema),
        }


@dataclass
class NodeSpec:
    """Builder for node dictionaries with validation and type safety.

    This helper class simplifies node creation by providing a structured
    interface with sensible defaults. It ensures all required fields are
    present and provides convenient conversion to/from dictionaries.

    Parameters
    ----------
    id : str
        Unique identifier for the node. Must be unique within the graph.
    position : dict, optional
        Node position with ``x`` and ``y`` coordinates. Defaults to
        ``{"x": 0.0, "y": 0.0}`` if not provided.
    type : str, default "panel"
        Node type identifier. Use ``"panel"`` for basic nodes or reference
        a custom type defined in ``ReactFlow.node_types``.
    label : str, optional
        Display label shown on the node. If ``None``, no label is displayed.
    data : dict, optional
        Custom data dictionary for the node. Defaults to ``{}`` if not provided.
        This is where you store node-specific properties that match the schema.
    selected : bool, default False
        Whether the node is currently selected in the UI.
    draggable : bool, default True
        Whether the node can be dragged by users.
    connectable : bool, default True
        Whether edges can be connected to/from this node.
    deletable : bool, default True
        Whether the node can be deleted by users.
    style : dict, optional
        CSS style dictionary applied to the node. Example:
        ``{"backgroundColor": "#ff0000", "border": "2px solid black"}``
    className : str, optional
        CSS class name applied to the node for custom styling.
    view : Panel viewable, optional
        Optional Panel viewable (widget, pane, layout) to render inside
        the node. The view will be displayed as the node's content.

    Methods
    -------
    to_dict()
        Convert to a dictionary for use with ReactFlow.
    from_dict(payload)
        Create a NodeSpec from a dictionary.

    Examples
    --------
    Create a basic node:

    >>> from panel_reactflow import NodeSpec
    >>> node = NodeSpec(
    ...     id="node1",
    ...     position={"x": 100, "y": 50},
    ...     label="Start Node"
    ... )
    >>> node_dict = node.to_dict()

    Create a node with custom styling:

    >>> node = NodeSpec(
    ...     id="node2",
    ...     position={"x": 200, "y": 100},
    ...     label="Process",
    ...     style={"backgroundColor": "#e3f2fd", "border": "2px solid #1976d2"},
    ...     className="custom-node"
    ... )

    Create a node with data:

    >>> node = NodeSpec(
    ...     id="transform1",
    ...     type="transform",
    ...     position={"x": 300, "y": 150},
    ...     label="Data Transform",
    ...     data={"operation": "filter", "threshold": 0.5}
    ... )

    Create a node with an embedded view:

    >>> import panel as pn
    >>> node = NodeSpec(
    ...     id="plot1",
    ...     position={"x": 400, "y": 200},
    ...     label="Data Plot",
    ...     view=pn.pane.Markdown("# Hello World")
    ... )

    Add to a ReactFlow graph:

    >>> from panel_reactflow import ReactFlow
    >>> flow = ReactFlow()
    >>> flow.add_node(node)
    """

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
    view: Any | None = None

    def __post_init__(self) -> None:
        if self.position is None:
            self.position = {"x": 0.0, "y": 0.0}
        if self.data is None:
            self.data = {}

    def to_dict(self) -> dict[str, Any]:
        """Convert the NodeSpec to a dictionary.

        Returns
        -------
        dict
            Dictionary representation suitable for ReactFlow.
        """
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
        if self.view is not None:
            payload["view"] = self.view
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NodeSpec":
        """Create a NodeSpec from a dictionary.

        Parameters
        ----------
        payload : dict
            Dictionary containing node properties.

        Returns
        -------
        NodeSpec
            A new NodeSpec instance.
        """
        return cls(**payload)


class Node(param.Parameterized):
    """Base class for object-oriented nodes.

    Subclass this class when you want node instances to keep Python-side state
    and react to graph events directly. Node instances can be passed anywhere
    a node dict/``NodeSpec`` is accepted.

    Subclasses can customize:

    - ``__panel__`` to render node content.
    - ``editor`` to provide a node-specific editor.
    - ``on_event`` (wildcard) and event-specific ``on_*`` hooks.

    Assigning to a parameter after the node is part of a graph updates the
    browser in place, without re-serializing the whole ``ReactFlow.nodes``
    list. Parameters declared on a subclass are synced into the node's
    ``data``; the presentational base parameters listed in ``_synced_props``
    are synced as top-level React Flow fields.
    """

    # Fields ``to_dict`` serializes outside ``data``, i.e. the ones
    # ``ReactFlow.patch_node_props`` may update in place.
    _props = (
        "position",
        "type",
        "label",
        "selected",
        "draggable",
        "connectable",
        "deletable",
        "style",
        "className",
    )

    # Subset of ``_props`` pushed to the browser on assignment. ``position``
    # and ``selected`` are driven by the frontend while the user drags or
    # selects, so echoing them back would fight the pointer mid-interaction;
    # use ``ReactFlow.patch_node_props`` to set them explicitly.
    _synced_props = (
        "type",
        "label",
        "draggable",
        "connectable",
        "deletable",
        "style",
        "className",
    )

    # ``data`` keys ``ReactFlow.patch_node_data`` mirrors onto a top-level
    # property. The node components render the label out of ``data._label``, so
    # a data patch that carries a label has to update the parameter too or the
    # next full serialization reverts it.
    _mirrored_data_keys = {"label": "label", "_label": "label"}

    id = param.String(default="", doc="Unique node identifier.")
    position = param.Dict(default={"x": 0.0, "y": 0.0}, doc="Node position.")
    type = param.String(default="panel", doc="Node type.")
    label = param.String(default=None, allow_None=True, doc="Display label.")
    data = param.Dict(default={}, doc="Custom node data.")
    selected = param.Boolean(default=False, doc="Selection state.")
    draggable = param.Boolean(default=True, doc="Whether node is draggable.")
    connectable = param.Boolean(default=True, doc="Whether node is connectable.")
    deletable = param.Boolean(default=True, doc="Whether node is deletable.")
    style = param.Dict(default=None, allow_None=True, doc="Optional node style.")
    className = param.String(default=None, allow_None=True, doc="Optional CSS class.")
    flow = param.Parameter(default=None, allow_None=True, precedence=-1, doc="Parent ReactFlow instance.")

    @classmethod
    def _data_param_names(cls) -> list[str]:
        return _parameterized_data_param_names(cls, Node)

    @classmethod
    def _data_schema(cls) -> dict[str, Any]:
        return _parameterized_data_schema(cls, Node)

    def to_dict(self) -> dict[str, Any]:
        """Convert this node instance to a ReactFlow-compatible dictionary."""
        data = dict(self.data or {})
        for name in self._data_param_names():
            data[name] = getattr(self, name)
        payload = {
            "id": self.id,
            "position": dict(self.position or {"x": 0.0, "y": 0.0}),
            "type": self.type or "panel",
            "label": self.label,
            "data": data,
            "selected": self.selected,
            "draggable": self.draggable,
            "connectable": self.connectable,
            "deletable": self.deletable,
        }
        if self.style is not None:
            payload["style"] = dict(self.style)
        if self.className is not None:
            payload["className"] = self.className
        view = self.__panel__()
        if view is not None:
            payload["view"] = view
        return payload

    def __panel__(self) -> Any | None:
        """Optional view rendered inside the node."""
        return None

    def editor(self, data, schema, *, id, type, on_patch):
        """Optional per-node editor factory.

        Return ``None`` to fall back to type/default editors.
        """
        return None

    def context_menu(self) -> Any | None:
        """Return a Panel component to render as a context menu on right-click.

        Override this method to provide a custom context menu for this node.
        Return ``None`` to disable the context menu for this node.
        """
        return None

    def on_event(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Wildcard event hook for node-related events."""

    def on_add(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node is added."""

    def on_delete(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node is deleted."""

    def on_move(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node moves."""

    def on_click(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node is clicked."""

    def on_data_change(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node's data changes."""

    def on_props_change(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node's top-level properties change."""

    def on_selection_changed(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this node participates in a selection update."""

    def on_sync(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when the graph receives a sync payload."""


@dataclass
class EdgeSpec:
    """Builder for edge dictionaries with validation and type safety.

    This helper class simplifies edge creation by providing a structured
    interface with sensible defaults. It ensures all required fields are
    present and provides convenient conversion to/from dictionaries.

    Parameters
    ----------
    id : str
        Unique identifier for the edge. Must be unique within the graph.
    source : str
        ID of the source node where the edge originates.
    target : str
        ID of the target node where the edge terminates.
    label : str, optional
        Display label shown on the edge. If ``None``, no label is displayed.
    type : str, optional
        Edge type identifier. Built-in types are:

        - ``'bezier'`` (default): Smooth bezier curve
        - ``'straight'``: Straight line between nodes
        - ``'step'``: Orthogonal step path (right angles)
        - ``'smoothstep'``: Step path with rounded corners
        - ``'smart_bezier'``: Bezier curve that routes around nodes
        - ``'smart_straight'``: Straight segments that route around nodes
        - ``'smart_step'``: Step path that routes around nodes

        You can also reference a custom type defined in
        ``ReactFlow.edge_types`` for schema validation and custom rendering.
    selected : bool, default False
        Whether the edge is currently selected in the UI.
    data : dict, optional
        Custom data dictionary for the edge. Defaults to ``{}`` if not provided.
        This is where you store edge-specific properties that match the schema.
    style : dict, optional
        CSS style dictionary applied to the edge line. Example:
        ``{"stroke": "#ff0000", "strokeWidth": 3}``
    markerEnd : dict, optional
        Arrow marker configuration for the edge end. Example:
        ``{"type": "arrow", "color": "#000000"}``
    sourceHandle : str, optional
        ID of the specific handle on the source node where the edge originates.
        Use this when the source node has multiple output handles defined.
    targetHandle : str, optional
        ID of the specific handle on the target node where the edge terminates.
        Use this when the target node has multiple input handles defined.

    Methods
    -------
    to_dict()
        Convert to a dictionary for use with ReactFlow.
    from_dict(payload)
        Create an EdgeSpec from a dictionary.

    Examples
    --------
    Create a basic edge:

    >>> from panel_reactflow import EdgeSpec
    >>> edge = EdgeSpec(
    ...     id="edge1",
    ...     source="node1",
    ...     target="node2"
    ... )
    >>> edge_dict = edge.to_dict()

    Create an edge with styling:

    >>> edge = EdgeSpec(
    ...     id="edge2",
    ...     source="node2",
    ...     target="node3",
    ...     label="Connection",
    ...     style={"stroke": "#1976d2", "strokeWidth": 2},
    ...     markerEnd={"type": "arrowclosed", "color": "#1976d2"}
    ... )

    Create a typed edge with data:

    >>> edge = EdgeSpec(
    ...     id="weighted_edge",
    ...     source="n1",
    ...     target="n2",
    ...     type="weighted",
    ...     label="0.75",
    ...     data={"weight": 0.75, "confidence": 0.9}
    ... )

    Create an edge with specific handles:

    >>> edge = EdgeSpec(
    ...     id="handle_edge",
    ...     source="producer",
    ...     target="consumer",
    ...     sourceHandle="result",
    ...     targetHandle="mode"
    ... )

    Add to a ReactFlow graph:

    >>> from panel_reactflow import ReactFlow
    >>> flow = ReactFlow()
    >>> flow.add_edge(edge)
    """

    id: str
    source: str
    target: str
    label: str | None = None
    type: str | None = None
    selected: bool = False
    data: dict[str, Any] | None = None
    style: dict[str, Any] | None = None
    markerEnd: dict[str, Any] | None = None
    sourceHandle: str | None = None
    targetHandle: str | None = None

    def __post_init__(self) -> None:
        if self.data is None:
            self.data = {}

    def to_dict(self) -> dict[str, Any]:
        """Convert the EdgeSpec to a dictionary.

        Returns
        -------
        dict
            Dictionary representation suitable for ReactFlow.
        """
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
        if self.sourceHandle is not None:
            payload["sourceHandle"] = self.sourceHandle
        if self.targetHandle is not None:
            payload["targetHandle"] = self.targetHandle
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EdgeSpec":
        """Create an EdgeSpec from a dictionary.

        Parameters
        ----------
        payload : dict
            Dictionary containing edge properties.

        Returns
        -------
        EdgeSpec
            A new EdgeSpec instance.
        """
        return cls(**payload)


class Edge(param.Parameterized):
    """Base class for object-oriented edges.

    Assigning to a parameter after the edge is part of a graph updates the
    browser in place, without re-serializing the whole ``ReactFlow.edges``
    list. Parameters declared on a subclass are synced into the edge's
    ``data``; the base parameters listed in ``_synced_props`` are synced as
    top-level React Flow fields.
    """

    # Fields ``to_dict`` serializes outside ``data``, i.e. the ones
    # ``ReactFlow.patch_edge_props`` may update in place.
    _props = (
        "source",
        "target",
        "label",
        "type",
        "selected",
        "style",
        "markerEnd",
        "sourceHandle",
        "targetHandle",
    )

    # Subset of ``_props`` pushed to the browser on assignment. ``selected``
    # is driven by the frontend while the user selects, so echoing it back
    # would fight the pointer; use ``ReactFlow.patch_edge_props`` to set it.
    _synced_props = (
        "source",
        "target",
        "label",
        "type",
        "style",
        "markerEnd",
        "sourceHandle",
        "targetHandle",
    )

    # ``data`` keys ``ReactFlow.patch_edge_data`` mirrors onto a top-level
    # property. The frontend has always promoted a patched label onto the edge
    # itself, so the parameter has to follow or the next full serialization
    # reverts it.
    _mirrored_data_keys = {"label": "label"}

    id = param.String(default="", doc="Unique edge identifier.")
    source = param.String(default="", doc="Source node id.")
    target = param.String(default="", doc="Target node id.")
    label = param.String(default=None, allow_None=True, doc="Display label.")
    type = param.String(default=None, allow_None=True, doc="Edge type.")
    selected = param.Boolean(default=False, doc="Selection state.")
    data = param.Dict(default={}, doc="Custom edge data.")
    style = param.Dict(default=None, allow_None=True, doc="Optional edge style.")
    markerEnd = param.Dict(default=None, allow_None=True, doc="Optional edge end marker.")
    sourceHandle = param.String(default=None, allow_None=True, doc="Optional source handle id.")
    targetHandle = param.String(default=None, allow_None=True, doc="Optional target handle id.")
    flow = param.Parameter(default=None, allow_None=True, precedence=-1, doc="Parent ReactFlow instance.")

    @classmethod
    def _data_param_names(cls) -> list[str]:
        return _parameterized_data_param_names(cls, Edge)

    @classmethod
    def _data_schema(cls) -> dict[str, Any]:
        return _parameterized_data_schema(cls, Edge)

    def to_dict(self) -> dict[str, Any]:
        """Convert this edge instance to a ReactFlow-compatible dictionary."""
        data = dict(self.data or {})
        for name in self._data_param_names():
            data[name] = getattr(self, name)
        payload = {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "type": self.type,
            "selected": self.selected,
            "data": data,
        }
        if self.style is not None:
            payload["style"] = dict(self.style)
        if self.markerEnd is not None:
            payload["markerEnd"] = dict(self.markerEnd)
        if self.sourceHandle is not None:
            payload["sourceHandle"] = self.sourceHandle
        if self.targetHandle is not None:
            payload["targetHandle"] = self.targetHandle
        return payload

    def editor(self, data, schema, *, id, type, on_patch):
        """Optional per-edge editor factory.

        Return ``None`` to fall back to type/default editors.
        """
        return None

    def on_event(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Wildcard event hook for edge-related events."""

    def on_add(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge is added."""

    def on_delete(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge is deleted."""

    def on_data_change(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge's data changes."""

    def on_props_change(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge's top-level properties change."""

    def on_selection_changed(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when this edge participates in a selection update."""

    def on_sync(self, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Hook called when the graph receives a sync payload."""


class Editor(Viewer):
    """Base class for custom node and edge editors.

    The Editor class provides a standardized interface for creating custom
    property editors for nodes and edges. All editors receive a unified
    signature and can report data changes back to the graph through a
    callback mechanism.

    All editor implementations (whether classes or functions) receive this
    unified signature::

        editor(data, schema, *, id, type, on_patch) -> Viewable

    Parameters
    ----------
    data : dict
        Current node or edge data dictionary. This contains all the custom
        properties stored in the node/edge.
    schema : dict or None
        Normalized JSON Schema for the node/edge type, or ``None`` if no
        schema is defined. Use this to drive form generation or validation.
    id : str
        Unique identifier of the node or edge being edited.
    type : str
        Type name of the node or edge being edited.
    on_patch : callable
        Callback function ``on_patch(patch_dict)`` to report data changes
        back to the graph. Call this with a dictionary of updated properties
        when the user modifies data.

    Examples
    --------
    Create a custom editor class:

    >>> import panel as pn
    >>> from panel_reactflow import Editor
    >>>
    >>> class ColorEditor(Editor):
    ...     def __init__(self, data=None, schema=None, **kwargs):
    ...         super().__init__(data, schema, **kwargs)
    ...         self.color_picker = pn.widgets.ColorPicker(
    ...             name="Node Color",
    ...             value=self._data.get("color", "#000000")
    ...         )
    ...         self.color_picker.param.watch(self._on_change, "value")
    ...
    ...     def _on_change(self, event):
    ...         if self._on_patch:
    ...             self._on_patch({"color": event.new})
    ...
    ...     def __panel__(self):
    ...         return self.color_picker

    Use the custom editor:

    >>> from panel_reactflow import ReactFlow
    >>> flow = ReactFlow(
    ...     node_editors={"panel": ColorEditor}
    ... )

    Create an editor as a simple function:

    >>> def simple_editor(data, schema, *, id, type, on_patch):
    ...     widget = pn.widgets.TextInput(
    ...         name="Label",
    ...         value=data.get("label", "")
    ...     )
    ...     widget.param.watch(
    ...         lambda e: on_patch({"label": e.new}),
    ...         "value"
    ...     )
    ...     return widget
    >>>
    >>> flow = ReactFlow(default_node_editor=simple_editor)
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
        self._is_updating = False

    def _report_patch(self, patch: dict[str, Any], remove: Sequence[str] = ()) -> bool:
        """Report an edit through ``on_patch``, returning ``False`` if it was rejected.

        A ``ValueError`` (e.g. a failed ``validate_on_patch`` check) is
        logged instead of propagating out of the widget watcher that made
        the edit, so the caller can revert the widget.
        """
        if self._on_patch is None:
            return True
        try:
            if remove and _accepts_keyword(self._on_patch, "remove"):
                self._on_patch(patch, remove=list(remove))
            else:
                self._on_patch(patch)
        except ValueError as exc:
            _LOGGER.warning("Rejected edit of %s %r: %s", self._node_type or "item", self._node_id, exc)
            return False
        return True

    def _handle_json_edit(self, new: Any, editor: JSONEditor) -> None:
        """Report an edit made in a JSON editor, reverting it if rejected.

        Keys deleted in the editor are reported as ``remove`` so they are
        deleted from the graph data instead of surviving the merge.
        """
        if self._is_updating or new == self._data:
            return
        if isinstance(new, dict):
            patch, removed = _json_data_patch(self._data, new)
            if self._report_patch(patch, removed):
                with self._updating():
                    self._data = dict(new)
                return
        else:
            _LOGGER.warning("Rejected edit of %s %r: data must be a JSON object.", self._node_type or "item", self._node_id)
        with self._updating():
            editor.value = dict(self._data)

    @contextmanager
    def _updating(self):
        """Apply a change without reporting it back through ``on_patch``."""
        self._is_updating = True
        try:
            yield
        finally:
            self._is_updating = False


def _accepts_keyword(fn: Callable, name: str) -> bool:
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any((p.name == name and p.kind is not p.POSITIONAL_ONLY) or p.kind is p.VAR_KEYWORD for p in parameters)


def _schema_definitions(schema: dict[str, Any]) -> dict[str, Any]:
    return {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}


def _json_data_patch(old: dict[str, Any], new: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return the changed keys and the removed keys going from *old* to *new*."""
    patch = {key: value for key, value in new.items() if key not in old or old[key] != value}
    return patch, [key for key in old if key not in new]


class JsonEditor(Editor):
    """Simple JSON editor for node and edge data.

    This editor provides a raw JSON editing interface using Panel's
    JSONEditor widget. It's useful for debugging or when you want full
    control over the data structure without schema-driven forms.

    The editor automatically syncs changes back to the graph when the
    user modifies the JSON content.

    Parameters
    ----------
    data : dict, optional
        Initial node or edge data dictionary. Defaults to ``{}``.
    schema : dict, optional
        JSON Schema (not used by this editor but part of the standard
        interface). This editor ignores the schema and allows free-form
        JSON editing.
    **kwargs
        Additional keyword arguments passed to the :class:`Editor` base class.

    Examples
    --------
    Use as default editor for all nodes:

    >>> from panel_reactflow import ReactFlow, JsonEditor
    >>> flow = ReactFlow(default_node_editor=JsonEditor)

    Use for specific node types:

    >>> flow = ReactFlow(
    ...     node_editors={"custom": JsonEditor}
    ... )

    The editor will display a JSON editor interface where users can
    directly edit the node's data dictionary.
    """

    def __init__(self, data=None, schema=None, **kwargs):
        super().__init__(data, schema, **kwargs)
        self._editor = JSONEditor(value=dict(self._data))
        self._editor.param.watch(self._on_json_change, "value")

    def _on_json_change(self, event: param.parameterized.Event) -> None:
        self._handle_json_edit(event.new, self._editor)

    @param.depends("_data", watch=True)
    def _sync_from_data(self) -> None:
        if self._is_updating:
            return
        with self._updating():
            self._editor.value = dict(self._data)

    def __panel__(self):
        return self._editor


class SchemaEditor(Editor):
    """Smart schema-driven editor with automatic form generation.

    This is the default editor used by ReactFlow. When a JSON Schema with
    properties is available, it automatically generates an appropriate form
    with widgets for each property based on the schema definition. If no
    schema is available or form generation fails, it gracefully falls back
    to a JSON editor.

    The editor uses the ``panel_reactflow.schema.JSONSchema`` component to
    render widgets based on JSON Schema property definitions, supporting
    various types including strings, numbers, booleans, enums, dates, and
    more.

    Parameters
    ----------
    data : dict, optional
        Initial node or edge data dictionary. Defaults to ``{}``.
    schema : dict, optional
        JSON Schema dictionary with a ``properties`` field defining the
        form fields. Each property's schema determines the widget type.
    **kwargs
        Additional keyword arguments passed to the :class:`Editor` base class.

    Examples
    --------
    The SchemaEditor is used automatically when you define node types:

    >>> from panel_reactflow import ReactFlow, NodeType
    >>> flow = ReactFlow(
    ...     node_types={
    ...         "transform": NodeType(
    ...             type="transform",
    ...             schema={
    ...                 "type": "object",
    ...                 "properties": {
    ...                     "operation": {
    ...                         "type": "string",
    ...                         "enum": ["filter", "map", "reduce"],
    ...                         "title": "Operation"
    ...                     },
    ...                     "threshold": {
    ...                         "type": "number",
    ...                         "minimum": 0,
    ...                         "maximum": 1,
    ...                         "title": "Threshold"
    ...                     }
    ...                 }
    ...             }
    ...         )
    ...     }
    ... )

    The editor will automatically generate:
    - A Select widget for the "operation" property (due to enum)
    - A Slider widget for the "threshold" property (due to min/max)

    Set as default editor explicitly:

    >>> from panel_reactflow import SchemaEditor
    >>> flow = ReactFlow(default_node_editor=SchemaEditor)

    Notes
    -----
    The editor falls back to :class:`JsonEditor` if:
    - No schema is provided
    - Schema has no ``properties`` field
    - Form generation fails, which is logged as a warning

    A property the form cannot represent with a dedicated widget is shown
    as a literal input rather than turning the whole form into JSON.
    Widgets are drawn from ``panel_material_ui`` when it is installed
    (``pip install panel-reactflow[material]``) and from ``panel.widgets``
    otherwise. Date and datetime values are written back as ISO 8601
    strings so the data stays JSON serializable.
    """

    def __init__(self, data=None, schema=None, **kwargs):
        super().__init__(data, schema, **kwargs)
        self._form = None
        properties = (self._schema or {}).get("properties")
        if properties:
            try:
                from .schema import JSONSchema

                self._form = JSONSchema(
                    dict(self._data),
                    schema=properties,
                    definitions=_schema_definitions(self._schema),
                    multi=False,
                )
            except Exception:
                _LOGGER.warning(
                    "Could not build a form from the schema of %s %r, falling back to a JSON editor.",
                    self._node_type or "item",
                    self._node_id,
                    exc_info=True,
                )
        if self._form is None:
            self._init_json_fallback()
            return
        for name, widget in self._form._widgets.items():
            widget.param.watch(
                lambda event, _n=name: self._on_widget_change(_n, event),
                "value",
            )
        self._panel = self._form.layout

    def _init_json_fallback(self) -> None:
        self._json_editor = JSONEditor(value=dict(self._data))
        self._json_editor.param.watch(self._on_json_change, "value")
        self._panel = self._json_editor

    def _on_widget_change(self, name: str, event: param.parameterized.Event) -> None:
        if self._is_updating:
            return
        from .schema import to_json_value

        value = to_json_value(event.new)
        if self._report_patch({name: value}):
            with self._updating():
                self._data = {**self._data, name: value}
        else:
            with self._updating():
                event.obj.value = event.old

    def _on_json_change(self, event: param.parameterized.Event) -> None:
        self._handle_json_edit(event.new, self._json_editor)

    @param.depends("_data", watch=True)
    def _sync_from_data(self) -> None:
        if self._is_updating:
            return
        with self._updating():
            if self._form is not None:
                self._form._set_widget_values(self._data)
            else:
                self._json_editor.value = dict(self._data)

    def __panel__(self):
        return self._panel


class ReactFlow(ReactComponent):
    """Interactive flow-based graph visualization and editing component.

    ReactFlow is a Panel wrapper around the React Flow library, providing
    a Python-first interface for creating interactive node-based graphs.
    It supports dragging, connecting, selecting, and deleting nodes and edges,
    with automatic synchronization between Python and JavaScript.

    The component is ideal for building workflow editors, data pipelines,
    state machines, mind maps, and other node-based interfaces.

    Parameters
    ----------
    nodes : list of dict or Node, default []
        List of node dictionaries or :class:`Node` instances defining the
        graph nodes. Each node should have at minimum ``id``, ``position``,
        and ``type`` fields. Use :class:`NodeSpec` or :class:`Node` for
        structured node creation.
    edges : list of dict or Edge, default []
        List of edge dictionaries or :class:`Edge` instances defining
        connections between nodes. Each edge should have ``id``, ``source``,
        and ``target`` fields. Use :class:`EdgeSpec` or :class:`Edge` for
        structured edge creation.
    node_types : dict, default {}
        Dictionary mapping type names to :class:`NodeType` definitions or dicts.
        Define custom node types with schemas, ports, and validation.
    edge_types : dict, default {}
        Dictionary mapping type names to :class:`EdgeType` definitions or dicts.
        Define custom edge types with schemas and validation.
    node_editors : dict, default {}
        Dictionary mapping node type names to custom editor classes or functions.
        Editors must follow the standard signature:
        ``editor(data, schema, *, id, type, on_patch)``.
    edge_editors : dict, default {}
        Dictionary mapping edge type names to custom editor classes or functions.
    default_node_editor : type or callable, optional
        Default editor factory used for nodes without a specific editor.
        Defaults to :class:`SchemaEditor`.
    default_edge_editor : type or callable, optional
        Default editor factory used for edges without a specific editor.
        When unset, a :class:`SchemaEditor` is shown only for edge types
        that declare a schema; other edges are selectable without opening
        an editor. Set to :class:`SchemaEditor` or :class:`JsonEditor` to
        give every edge an editor.
    debounce_ms : int, default 150
        Debounce delay in milliseconds when ``sync_mode='debounce'``.
        Controls how often updates are sent from JavaScript to Python.
    default_edge_options : dict, default {}
        Default React Flow edge options applied to all edges (e.g., ``animated``,
        ``type``). See React Flow documentation for available options.
    editable : bool, default True
        Enable interactive editing on the canvas (drag, connect, delete,
        drop and node/edge editors). Set to ``False`` for read-only
        visualization; nodes and edges stay selectable unless
        ``selectable`` is also ``False``. Context menus and popups supplied
        by the application are not affected and must respect this flag
        themselves.
    editor_mode : {"toolbar", "node", "side"}, default "toolbar"
        Where to render node editors:

        - ``"toolbar"``: Editors appear in a toolbar above the canvas
        - ``"node"``: Editors appear embedded within each node
        - ``"side"``: Editors appear in a side panel
    enable_connect : bool, default True
        Allow users to create new edges by connecting nodes.
    enable_delete : bool, default True
        Allow users to delete selected nodes or edges using keyboard shortcuts.
    enable_multiselect : bool, default True
        Allow selecting multiple nodes/edges with modifier keys (Shift/Ctrl).
    max_drop_size : int, default 10_000_000
        Maximum total size in bytes of the data in one drop. Larger files
        are not read and are reported in the drop event's ``rejected``
        list, so a big drop cannot exceed the websocket message limit and
        disconnect the session. Binary files are sent base64 encoded, which
        adds a third on the wire, so keep this well below Bokeh's
        ``websocket_max_message_size`` (20 MiB by default). ``0`` disables
        the limit.
    max_zoom : float, default 2
        Maximum zoom level the viewport can be zoomed in to.
    min_zoom : float, default 0.5
        Minimum zoom level the viewport can be zoomed out to.
    selectable : bool, default True
        Allow clicking nodes and edges to select them, independently of
        ``editable``. Setting it to ``False`` clears the current selection.
    selection : dict, default {"nodes": [], "edges": []}
        Current selection state with lists of selected node and edge IDs.
        Read-only; updated automatically when selection changes.
    show_minimap : bool, default False
        Show a minimap overlay in the corner for navigation in large graphs.
    sync_mode : {"event", "debounce"}, default "event"
        Synchronization mode for JavaScript to Python updates:

        - ``"event"``: Immediate sync on every change
        - ``"debounce"``: Batched sync with ``debounce_ms`` delay
    validate_on_add : bool, default True
        Validate node/edge data against schemas when adding via
        :meth:`add_node` or :meth:`add_edge`.
    validate_on_patch : bool, default False
        Validate node/edge data against schemas when patching via
        :meth:`patch_node_data` or :meth:`patch_edge_data`.
    viewport : dict, optional
        Persisted viewport state with ``x``, ``y`` (position) and ``zoom``.
        Set to restore a specific view on initialization.
        Use :meth:`fit_view` to fit the graph into view again.
    top_panel : list, default []
        Panel viewables rendered in a top-center overlay panel.
    bottom_panel : list, default []
        Panel viewables rendered in a bottom-center overlay panel.
    left_panel : list, default []
        Panel viewables rendered in a center-left overlay panel.
    right_panel : list, default []
        Panel viewables rendered in a center-right overlay panel.

    Examples
    --------
    Create a basic flow graph:

    >>> import panel as pn
    >>> from panel_reactflow import ReactFlow, NodeSpec, EdgeSpec
    >>>
    >>> pn.extension()
    >>>
    >>> nodes = [
    ...     NodeSpec(id="1", position={"x": 0, "y": 0}, label="Start").to_dict(),
    ...     NodeSpec(id="2", position={"x": 200, "y": 0}, label="Process").to_dict(),
    ...     NodeSpec(id="3", position={"x": 400, "y": 0}, label="End").to_dict(),
    ... ]
    >>> edges = [
    ...     EdgeSpec(id="e1", source="1", target="2").to_dict(),
    ...     EdgeSpec(id="e2", source="2", target="3").to_dict(),
    ... ]
    >>> flow = ReactFlow(nodes=nodes, edges=edges)
    >>> flow.servable()

    Define custom node types with schemas:

    >>> from panel_reactflow import NodeType
    >>> import param
    >>>
    >>> class FilterParams(param.Parameterized):
    ...     threshold = param.Number(default=0.5, bounds=(0, 1))
    ...     operation = param.Selector(default="gt", objects=["gt", "lt", "eq"])
    >>>
    >>> flow = ReactFlow(
    ...     node_types={
    ...         "filter": NodeType(
    ...             type="filter",
    ...             label="Filter Node",
    ...             schema=FilterParams,
    ...             inputs=["input"],
    ...             outputs=["output"]
    ...         )
    ...     }
    ... )
    >>> flow.add_node(NodeSpec(
    ...     id="f1",
    ...     type="filter",
    ...     position={"x": 100, "y": 100},
    ...     data={"threshold": 0.7, "operation": "gt"}
    ... ))

    Listen to events:

    >>> def on_node_moved(event):
    ...     print(f"Node {event['node_id']} moved to {event['position']}")
    >>>
    >>> flow.on("node_moved", on_node_moved)
    >>> flow.on("edge_added", lambda e: print(f"Edge added: {e['edge']}"))

    Embed Panel viewables in nodes:

    >>> nodes = [
    ...     {
    ...         "id": "plot1",
    ...         "position": {"x": 0, "y": 0},
    ...         "label": "Markdown View",
    ...         "view": pn.pane.Markdown("# Hello World"),
    ...         "data": {}
    ...     }
    ... ]
    >>> flow = ReactFlow(nodes=nodes)

    Convert from/to NetworkX:

    >>> import networkx as nx
    >>> G = nx.DiGraph()
    >>> G.add_edge("A", "B", weight=0.5)
    >>> G.add_edge("B", "C", weight=0.8)
    >>>
    >>> flow = ReactFlow.from_networkx(G)
    >>>
    >>> # Make modifications...
    >>>
    >>> G_modified = flow.to_networkx()

    See Also
    --------
    Node : Base class for object-oriented nodes
    NodeSpec : Builder for node dictionaries
    Edge : Base class for object-oriented edges
    EdgeSpec : Builder for edge dictionaries
    NodeType : Define custom node types with schemas
    EdgeType : Define custom edge types with schemas
    Editor : Base class for custom editors
    SchemaEditor : Smart schema-driven editor (default)
    JsonEditor : Simple JSON editor

    Notes
    -----
    The component requires the Panel extension to be loaded. Make sure to
    call ``pn.extension()`` before using ReactFlow.

    For optimal performance with large graphs (>100 nodes), consider:
    - Using ``sync_mode='debounce'`` with appropriate ``debounce_ms``
    - Setting ``validate_on_patch=False`` if validation is expensive
    - Disabling ``show_minimap`` if not needed

    ReactFlow is not thread-safe. Methods such as ``add_node`` read, modify
    and reassign ``nodes``/``edges``, so concurrent calls from several
    threads lose updates. Modify a flow only from the thread that owns its
    session, e.g. from Panel callbacks; work done in a background thread
    should hand its result back with ``pn.state.execute(callback)`` or
    ``doc.add_next_tick_callback(callback)``.
    """

    nodes = param.List(default=[], doc="Canonical list of node dictionaries or Node instances.")
    edges = param.List(default=[], doc="Canonical list of edge dictionaries or Edge instances.")
    node_types = param.Dict(default={}, doc="Node type descriptors keyed by type name.")
    edge_types = param.Dict(default={}, doc="Edge type descriptors keyed by type name.")
    connection_validation = param.Dict(default={}, doc="Opt-in frontend connection policies (direction, cycles, duplicates, types, capacity).")
    has_connection_validators = param.Boolean(default=False, doc="Whether Python connection validators are registered.")

    node_editors = param.Dict(default={}, doc="Node editor factories keyed by type name.", precedence=-1)
    edge_editors = param.Dict(default={}, doc="Edge editor factories keyed by type name.", precedence=-1)
    default_node_editor = param.Parameter(default=None, doc="Default node editor factory.", precedence=-1)
    default_edge_editor = param.Parameter(default=None, doc="Default edge editor factory.", precedence=-1)

    color_mode = param.ObjectSelector(default=None, objects=["light", "dark"], doc="Color mode for the graph.")

    allow_edge_loops = param.Boolean(default=False, doc="Allow to have edge loops in the graph (can lead to update infinite loops).")

    display_side_bar = param.Boolean(default=True, doc="Display the side bar to drag and drop new nodes.")

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

    error_recovery = param.ObjectSelector(
        default="auto",
        objects=["auto", "manual", "off"],
        doc=(
            "How to handle a rendering error in the graph view. 'auto' silently "
            "remounts the canvas, then retries in safe mode, before showing a "
            "recovery panel; 'manual' shows the recovery panel immediately; "
            "'off' disables the error boundary so exceptions propagate."
        ),
    )

    max_zoom = param.Number(default=2, bounds=(0, None), inclusive_bounds=(False, True), doc="Maximum zoom level of the viewport.")

    min_zoom = param.Number(default=0.5, bounds=(0, None), inclusive_bounds=(False, True), doc="Minimum zoom level of the viewport.")

    selectable = param.Boolean(default=True, doc="Allow selecting nodes and edges, even when not editable.")

    selection = param.Dict(default={"nodes": [], "edges": []}, doc="Derived selection state for node and edge ids.")

    show_minimap = param.Boolean(default=False, doc="Show the minimap overlay.")

    sync_mode = param.ObjectSelector(default="event", objects=["event", "debounce"], doc="Sync mode for JS->Python updates.")

    validate_on_add = param.Boolean(default=True, doc="Validate data against schema on add_node/add_edge.")
    validate_on_patch = param.Boolean(default=False, doc="Validate data against schema on patch_node_data/patch_edge_data.")

    viewport = param.Dict(default=None, allow_None=True, doc="Optional persisted viewport state.")

    drop_types = param.List(
        default=[],
        item_type=str,
        doc="""
        MIME types accepted when data is dropped onto the canvas, e.g. the
        ``drag_type`` of a draggable ``panel_material_ui.MenuList``. Include
        ``"Files"`` to accept files dragged in from the operating system.
        Each accepted drop emits a ``"drop"`` event; drops of other types are
        ignored, as are all drops while ``editable`` is False.""",
    )

    max_drop_size = param.Integer(
        default=10_000_000,
        bounds=(0, None),
        doc="""
        Maximum total size in bytes of one drop. Files beyond the limit are
        not read and are listed in the drop event's ``rejected`` entries.
        Binary files are sent base64 encoded (a third larger on the wire),
        so keep this below Bokeh's ``websocket_max_message_size``. ``0``
        disables the limit.""",
    )

    popup_trigger = param.ObjectSelector(
        default="click",
        objects=["click", "hover", "none"],
        doc="Whether port and edge inspection events are triggered on click, hover, or not automatically.",
    )

    popup_hover_delay = param.Integer(
        default=500,
        bounds=(0, None),
        doc="Delay in milliseconds before a hover inspection event is emitted.",
    )

    top_panel = Children(default=[], doc="Children rendered in a top-center panel.")
    bottom_panel = Children(default=[], doc="Children rendered in a bottom-center panel.")
    left_panel = Children(default=[], doc="Children rendered in a center-left panel.")
    right_panel = Children(default=[], doc="Children rendered in a center-right panel.")

    # Internal view parameters
    _node_editors = param.Dict(default={}, doc="Per-node editors.", precedence=-1)
    _node_editor_views = Children(default=[], doc="Node editor views, in the order of _node_editor_ids.")
    _node_editor_ids = param.List(default=[], doc="Ids of the nodes whose editors are in _node_editor_views.")
    _edge_editors = param.Dict(default={}, doc="Per-edge editors.", precedence=-1)
    _selected_editor = Child(doc="Active editor for the selected node/edge in side mode.")
    _context_menu = Child(doc="Context menu component rendered on node right-click.")
    _context_menu_position = param.Dict(default=None, allow_None=True, doc="Screen position for the context menu overlay.")
    _value_popup = Child(doc="Popup component rendered on handle/edge click, e.g. via show_popup().")
    _value_popup_position = param.Dict(default=None, allow_None=True, doc="Screen position for the value popup overlay.")
    _views = Children(default=[], doc="Panel viewables rendered inside nodes via view_idx.")
    _node_update_count = param.Integer(default=0, doc="Monotonic counter for normalized node updates.")

    _bundle = DIST_PATH / "panel-reactflow.bundle.js"
    _esm = Path(__file__).parent / "models" / "reactflow.jsx"
    _importmap = {
        "imports": {
            "@xyflow/react": "https://esm.sh/@xyflow/react@12.8.3",
            "@tisoap/react-flow-smart-edge": "https://esm.sh/@tisoap/react-flow-smart-edge",
        }
    }
    _stylesheets = [DIST_PATH / "panel-reactflow.bundle.css", DIST_PATH / "css" / "reactflow.css"]
    _render_policy = "manual"

    def __init__(self, **params: Any):
        if "color_mode" not in params:
            params["color_mode"] = "dark" if pn.config.theme == "dark" else "light"
        self._attached_node_instances: dict[int, Node] = {}
        self._attached_edge_instances: dict[int, Edge] = {}
        self._node_data_param_watchers: dict[str, tuple[Node, list[Any]]] = {}
        self._edge_data_param_watchers: dict[str, tuple[Edge, list[Any]]] = {}
        self._suppress_prop_sync = False
        self._node_view_cache: dict[str, tuple[int, Any]] = {}
        # id -> node/edge indexes, rebuilt once per structural change so that
        # lookups by id do not scan the graph.
        self._node_index: dict[str, dict[str, Any] | Node] = {}
        self._edge_index: dict[str, dict[str, Any] | Edge] = {}
        self._first_node_by_type: dict[str, Node] = {}
        self._first_edge_by_type: dict[str, Edge] = {}
        self._schema_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        # Editors are built lazily and cached under a key describing what
        # they were built from, so that a replaced node or a type change
        # builds a fresh editor.
        self._editor_keys: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._requested_node_editors: dict[str, None] = {}
        self._editor_data: dict[tuple[str, str], dict[str, Any]] = {}
        self._view_refs: list[int] = []
        self._editor_view_refs: list[int] = []
        self._patching_editor: tuple[str, str] | None = None
        self._selected_editor_source: Any = None
        # Normalize type specs before parent init so the frontend receives
        # JSON-serializable descriptors from the start.
        if "node_types" in params:
            params["node_types"] = _coerce_spec_map(params["node_types"])
        if "edge_types" in params:
            params["edge_types"] = _coerce_spec_map(params["edge_types"], edge=True)
        # Normalize nodes and edges to ensure NodeSpec/EdgeSpec are converted.
        if "nodes" in params:
            params["nodes"] = [ReactFlow._coerce_node(node) for node in params["nodes"]]
        if "edges" in params:
            params["edges"] = [ReactFlow._coerce_edge(edge) for edge in params["edges"]]
        self._check_graph(params.get("nodes", []), params.get("edges", []))
        super().__init__(**params)
        self._event_handlers: dict[str, list[Callable]] = {"*": []}
        self._connection_validators: list[Callable] = []
        with param.discard_events(self):
            self._node_editors = {}
            self._edge_editors = {}
        # A single watcher handles every structural change: param deep
        # compares old and new values once per watcher, which for large
        # graphs costs more than the bookkeeping itself. The handler does
        # its own (identity based) change detection instead.
        self.param.watch(self._on_graph_change, ["nodes", "edges"], onlychanged=False)
        self.param.watch(self._normalize_specs, ["node_types", "edge_types"])
        self.param.watch(
            self._on_editor_config_change,
            ["editor_mode", "node_types", "edge_types", "node_editors", "edge_editors", "default_node_editor", "default_edge_editor"],
        )
        self.param.watch(self._update_selected_editor, ["selection", "editable"])
        self.param.watch(self._clear_selection_if_unselectable, ["selectable"])
        self._on_graph_change()

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
                esm_hash = hashlib.sha256(str(esm_path.stat().st_mtime).encode("utf-8")).hexdigest()
            else:
                esm_hash = hashlib.md5(__version__.encode("utf-8")).hexdigest()
            esm += f"?{esm_hash}"
        else:
            esm = esm_path.read_text(encoding="utf-8")
        return esm

    @classproperty
    def _bundle_path(cls) -> os.PathLike | None:
        return cls._bundle

    def _get_node_schema(self, node_type: str) -> dict[str, Any] | None:
        """Return the normalized JSON Schema for *node_type*, or ``None``."""
        type_spec = self.node_types.get(node_type)
        if type_spec is not None and type_spec.get("schema") is not None:
            return type_spec.get("schema")
        key = ("node", node_type)
        if key not in self._schema_cache:
            node = self._first_node_by_type.get(node_type)
            schema = node._data_schema() if node is not None else None
            self._schema_cache[key] = schema if schema and schema.get("properties") else None
        return self._schema_cache[key]

    def _get_edge_schema(self, edge_type: str) -> dict[str, Any] | None:
        """Return the normalized JSON Schema for *edge_type*, or ``None``."""
        type_spec = self.edge_types.get(edge_type)
        if type_spec is not None and type_spec.get("schema") is not None:
            return type_spec.get("schema")
        key = ("edge", edge_type)
        if key not in self._schema_cache:
            edge = self._first_edge_by_type.get(edge_type)
            schema = edge._data_schema() if edge is not None else None
            self._schema_cache[key] = schema if schema and schema.get("properties") else None
        return self._schema_cache[key]

    def _create_editor(
        self,
        factory: Any,
        item_id: str,
        data: dict,
        schema: dict | None,
        item_type: str,
        *,
        kind: str,
    ) -> Any:
        """Instantiate an editor from *factory*.

        All editors (classes and plain callables) receive the unified
        signature ``(data, schema, *, id, type, on_patch)``.

        Patches reported by the editor are applied with ``patch_node_data``
        for nodes and ``patch_edge_data`` for edges, without echoing the
        resulting data back into the editor that produced them.
        """
        patch_fn = self.patch_node_data if kind == "node" else self.patch_edge_data

        def on_patch(patch: dict, *, remove: Sequence[str] | None = None) -> None:
            previous = self._patching_editor
            self._patching_editor = (kind, item_id)
            try:
                if remove:
                    patch_fn(item_id, patch, remove=remove)
                else:
                    patch_fn(item_id, patch)
            finally:
                self._patching_editor = previous

        return factory(data, schema, id=item_id, type=item_type, on_patch=on_patch)

    @staticmethod
    def _node_is_instance(node: Any) -> bool:
        return isinstance(node, Node)

    @staticmethod
    def _node_id(node: dict[str, Any] | Node) -> str | None:
        return node.id if isinstance(node, Node) else node.get("id")

    @staticmethod
    def _node_type(node: dict[str, Any] | Node) -> str:
        if isinstance(node, Node):
            return node.type or "panel"
        return node.get("type", "panel")

    @staticmethod
    def _node_data(node: dict[str, Any] | Node) -> dict[str, Any]:
        if isinstance(node, Node):
            return dict(node.data or {})
        return dict(node.get("data", {}))

    def _node_view(self, node: dict[str, Any] | Node) -> Any | None:
        if isinstance(node, Node):
            node_id = self._node_id(node)
            node_ref = id(node)
            if node_id is not None:
                cached = self._node_view_cache.get(node_id)
                if cached is not None and cached[0] == node_ref:
                    return cached[1]
            view = node.__panel__()
            if node_id is not None and view is not None:
                self._node_view_cache[node_id] = (node_ref, view)
            return view
        return node.get("view", None)

    @staticmethod
    def _node_set_position(node: dict[str, Any] | Node, position: dict[str, Any]) -> None:
        if isinstance(node, Node):
            node.position = dict(position)
        else:
            node["position"] = position

    @staticmethod
    def _node_set_data(node: dict[str, Any] | Node, data: dict[str, Any]) -> None:
        if isinstance(node, Node):
            node.data = dict(data)
        else:
            node["data"] = data

    @staticmethod
    def _node_payload(node: dict[str, Any] | NodeSpec | Node) -> dict[str, Any]:
        if isinstance(node, Node):
            return node.to_dict()
        if isinstance(node, NodeSpec):
            return node.to_dict()
        return dict(node)

    @staticmethod
    def _edge_id(edge: dict[str, Any] | Edge) -> str | None:
        return edge.id if isinstance(edge, Edge) else edge.get("id")

    @staticmethod
    def _edge_type(edge: dict[str, Any] | Edge) -> str:
        if isinstance(edge, Edge):
            return edge.type or ""
        return edge.get("type", "")

    @staticmethod
    def _edge_data(edge: dict[str, Any] | Edge) -> dict[str, Any]:
        if isinstance(edge, Edge):
            return dict(edge.data or {})
        return dict(edge.get("data", {}))

    @staticmethod
    def _edge_set_data(edge: dict[str, Any] | Edge, data: dict[str, Any]) -> None:
        if isinstance(edge, Edge):
            edge.data = dict(data)
        else:
            edge["data"] = data

    @staticmethod
    def _edge_payload(edge: dict[str, Any] | EdgeSpec | Edge) -> dict[str, Any]:
        if isinstance(edge, Edge):
            return edge.to_dict()
        if isinstance(edge, EdgeSpec):
            return edge.to_dict()
        return dict(edge)

    def _rebuild_node_index(self) -> None:
        index: dict[str, dict[str, Any] | Node] = {}
        first_by_type: dict[str, Node] = {}
        for node in self.nodes:
            node_id = self._node_id(node)
            if node_id is not None:
                index.setdefault(node_id, node)
            if isinstance(node, Node):
                first_by_type.setdefault(node.type, node)
        self._node_index = index
        self._first_node_by_type = first_by_type

    def _rebuild_edge_index(self) -> None:
        index: dict[str, dict[str, Any] | Edge] = {}
        first_by_type: dict[str, Edge] = {}
        for edge in self.edges:
            edge_id = self._edge_id(edge)
            if edge_id is not None:
                index.setdefault(edge_id, edge)
            if isinstance(edge, Edge):
                first_by_type.setdefault(edge.type or "", edge)
        self._edge_index = index
        self._first_edge_by_type = first_by_type

    def _get_node(self, node_id: str, *, scan: bool = True) -> dict[str, Any] | Node | None:
        node = self._node_index.get(node_id)
        if node is not None and self._node_id(node) == node_id:
            return node
        # The index misses nodes added or re-identified in place (e.g.
        # ``flow.nodes.append(...)`` or ``node["id"] = ...``), which do not
        # trigger the watcher, so fall back to a scan and reindex.
        if scan and any(self._node_id(item) == node_id for item in self.nodes):
            self._rebuild_node_index()
            return self._node_index.get(node_id)
        return None

    def _get_edge(self, edge_id: str, *, scan: bool = True) -> dict[str, Any] | Edge | None:
        edge = self._edge_index.get(edge_id)
        if edge is not None and self._edge_id(edge) == edge_id:
            return edge
        if scan and any(self._edge_id(item) == edge_id for item in self.edges):
            self._rebuild_edge_index()
            return self._edge_index.get(edge_id)
        return None

    def _get_node_instance(self, node_id: str) -> Node | None:
        node = self._get_node(node_id)
        return node if isinstance(node, Node) else None

    def _get_edge_instance(self, edge_id: str) -> Edge | None:
        edge = self._get_edge(edge_id)
        return edge if isinstance(edge, Edge) else None

    @staticmethod
    def _sync_node_data_params_from_data(node: Node) -> None:
        for name in node._data_param_names():
            if name in (node.data or {}):
                setattr(node, name, node.data[name])

    @staticmethod
    def _sync_edge_data_params_from_data(edge: Edge) -> None:
        for name in edge._data_param_names():
            if name in (edge.data or {}):
                setattr(edge, name, edge.data[name])

    @staticmethod
    def _teardown_param_watcher(watcher_store: dict[str, tuple[Any, list[Any]]], item_id: str) -> None:
        record = watcher_store.pop(item_id, None)
        if record is None:
            return
        item, watchers = record
        for watcher in watchers:
            try:
                item.param.unwatch(watcher)
            except Exception:
                pass

    def _sync_instance_flow_refs(self, *_: param.parameterized.Event) -> None:
        current_nodes = {id(node): node for node in self.nodes if isinstance(node, Node)}
        for node_ref, old_node in list(self._attached_node_instances.items()):
            if node_ref not in current_nodes:
                old_node.flow = None
        for node in current_nodes.values():
            if node.flow is not self:
                node.flow = self
        self._attached_node_instances = current_nodes

        current_edges = {id(edge): edge for edge in self.edges if isinstance(edge, Edge)}
        for edge_ref, old_edge in list(self._attached_edge_instances.items()):
            if edge_ref not in current_edges:
                old_edge.flow = None
        for edge in current_edges.values():
            if edge.flow is not self:
                edge.flow = self
        self._attached_edge_instances = current_edges

    def _update_node_data_param_watchers(self) -> None:
        current_nodes = {node.id: node for node in self.nodes if isinstance(node, Node) and node.id}
        self._update_param_watchers(
            current_items=current_nodes,
            watcher_store=self._node_data_param_watchers,
            get_instance=self._get_node_instance,
            data_patch_fn=self.patch_node_data,
            prop_patch_fn=self.patch_node_props,
        )

    def _update_edge_data_param_watchers(self) -> None:
        current_edges = {edge.id: edge for edge in self.edges if isinstance(edge, Edge) and edge.id}
        self._update_param_watchers(
            current_items=current_edges,
            watcher_store=self._edge_data_param_watchers,
            get_instance=self._get_edge_instance,
            data_patch_fn=self.patch_edge_data,
            prop_patch_fn=self.patch_edge_props,
        )

    def _update_param_watchers(
        self,
        *,
        current_items: dict[str, Node | Edge],
        watcher_store: dict[str, tuple[Node | Edge, list[Any]]],
        get_instance: Callable[[str], Node | Edge | None],
        data_patch_fn: Callable[[str, dict[str, Any]], None],
        prop_patch_fn: Callable[[str, dict[str, Any]], None],
    ) -> None:
        """Keep param watchers for a set of live ``Node``/``Edge`` instances in sync.

        Watches both the subclass-defined data params
        (``item._data_param_names()``), which are patched into ``data``, and the
        presentational base params (``item._synced_props``, e.g.
        ``label``/``style``), which are patched as top-level React Flow fields,
        so that assigning either on a live instance reaches the frontend.
        """
        for item_id, (watched_item, _) in list(watcher_store.items()):
            current = current_items.get(item_id)
            if current is None or current is not watched_item:
                self._teardown_param_watcher(watcher_store, item_id)
        for item_id, item in current_items.items():
            if item_id in watcher_store:
                continue
            watchers = [
                item.param.watch(
                    lambda event, _id=item_id, _name=name, _item=item: self._on_data_param_change(get_instance, data_patch_fn, _id, _name, _item, event),
                    name,
                )
                for name in item._data_param_names()
            ]
            # A single watcher for every property, so that a batched
            # ``param.update`` coalesces into one patch message.
            watchers.append(
                item.param.watch(
                    lambda *events, _id=item_id, _item=item: self._on_prop_param_change(get_instance, prop_patch_fn, _id, _item, *events),
                    list(item._synced_props),
                )
            )
            watcher_store[item_id] = (item, watchers)

    @staticmethod
    def _on_data_param_change(
        get_instance: Callable[[str], Node | Edge | None],
        patch_fn: Callable[[str, dict[str, Any]], None],
        item_id: str,
        param_name: str,
        item: Node | Edge,
        event: param.parameterized.Event,
    ) -> None:
        if get_instance(item_id) is not item:
            return
        # Guards against the redundant re-send ``_sync_*_data_params_from_data``
        # would otherwise cause.
        if (item.data or {}).get(param_name) == event.new:
            return
        patch_fn(item_id, {param_name: event.new})

    def _on_prop_param_change(
        self,
        get_instance: Callable[[str], Node | Edge | None],
        patch_fn: Callable[[str, dict[str, Any]], None],
        item_id: str,
        item: Node | Edge,
        *events: param.parameterized.Event,
    ) -> None:
        if self._suppress_prop_sync:
            return
        if get_instance(item_id) is not item:
            return
        patch_fn(item_id, {event.name: event.new for event in events})

    @contextmanager
    def _suppressed_prop_sync(self):
        """Apply values that came from (or are already in) the browser.

        Prevents the ``Node``/``Edge`` property watchers from echoing a patch
        back to the frontend for a value the frontend already holds.
        """
        previous = self._suppress_prop_sync
        self._suppress_prop_sync = True
        try:
            yield
        finally:
            self._suppress_prop_sync = previous

    @staticmethod
    def _invoke_node_callback(callback: Callable, payload: dict[str, Any], flow: "ReactFlow") -> None:
        """Run an event callback or hook, logging instead of raising.

        The graph change that triggered the event has already been applied,
        so letting one failing callback raise would skip every later callback
        and hook for the same event (and, for multi-id operations such as
        ``remove_node("a", "b")``, every later id) while the state stays
        committed.
        """
        if _accepts_flow(callback):
            cb = partial(callback, payload, flow)
        else:
            cb = partial(callback, payload)
        # schedule=False: the outer _process_event already forced this whole
        # message-handling pass onto the doc's own callback (schedule=False),
        # so 'auto' scheduling here would see the doc as still locked and
        # defer to the next tick. That breaks Child-param updates (e.g. via
        # show_popup) made from inside a hook, since the model reconciliation
        # that turns a Viewable into its Bokeh model expects to run in the
        # same pass as the triggering event.
        try:
            pn.state.execute(cb, schedule=False)
        except Exception:
            _LOGGER.exception(
                "panel-reactflow %r callback %r failed",
                payload.get("type") if isinstance(payload, dict) else None,
                getattr(callback, "__qualname__", callback),
            )

    def _invoke_node_hook(self, node: Node, hook_name: str, payload: dict[str, Any]) -> None:
        hook = getattr(node, hook_name, None)
        if callable(hook):
            self._invoke_node_callback(hook, payload, self)

    def _invoke_edge_hook(self, edge: Edge, hook_name: str, payload: dict[str, Any]) -> None:
        hook = getattr(edge, hook_name, None)
        if callable(hook):
            self._invoke_node_callback(hook, payload, self)

    def _dispatch_node_hooks(self, event_type: str, payload: dict[str, Any]) -> None:
        node_ids: list[str] = []
        if event_type == "node_added":
            node_payload = payload.get("node", {})
            if isinstance(node_payload, dict) and node_payload.get("id"):
                node_ids = [node_payload["id"]]
        elif event_type in ("node_moved", "node_clicked", "node_data_changed", "node_props_changed"):
            node_id = payload.get("node_id")
            if node_id:
                node_ids = [node_id]
        elif event_type == "selection_changed":
            node_ids = list(payload.get("nodes") or [])
        elif event_type == "sync":
            node_ids = [node.id for node in self.nodes if isinstance(node, Node)]

        hook_map = {
            "node_added": "on_add",
            "node_moved": "on_move",
            "node_clicked": "on_click",
            "node_data_changed": "on_data_change",
            "node_props_changed": "on_props_change",
            "selection_changed": "on_selection_changed",
            "sync": "on_sync",
        }
        hook_name = hook_map.get(event_type)
        for node_id in node_ids:
            node = self._get_node_instance(node_id)
            if node is None:
                continue
            if hook_name is not None:
                self._invoke_node_hook(node, hook_name, payload)
            self._invoke_node_hook(node, "on_event", payload)

    def _dispatch_edge_hooks(self, event_type: str, payload: dict[str, Any]) -> None:
        edge_ids: list[str] = []
        if event_type == "edge_added":
            edge_payload = payload.get("edge", {})
            if isinstance(edge_payload, dict) and edge_payload.get("id"):
                edge_ids = [edge_payload["id"]]
        elif event_type in ("edge_deleted", "edge_data_changed", "edge_props_changed"):
            edge_id = payload.get("edge_id")
            if edge_id:
                edge_ids = [edge_id]
        elif event_type == "selection_changed":
            edge_ids = list(payload.get("edges") or [])
        elif event_type == "sync":
            edge_ids = [edge.id for edge in self.edges if isinstance(edge, Edge)]

        hook_map = {
            "edge_added": "on_add",
            "edge_deleted": "on_delete",
            "edge_data_changed": "on_data_change",
            "edge_props_changed": "on_props_change",
            "selection_changed": "on_selection_changed",
            "sync": "on_sync",
        }
        hook_name = hook_map.get(event_type)
        # Deleted edges are gone from the graph (remove_edge runs their hooks
        # itself), so do not scan for them.
        scan = event_type != "edge_deleted"
        for edge_id in edge_ids:
            edge = self._get_edge(edge_id, scan=scan)
            if not isinstance(edge, Edge):
                continue
            if hook_name is not None:
                self._invoke_edge_hook(edge, hook_name, payload)
            self._invoke_edge_hook(edge, "on_event", payload)

    def _on_graph_change(self, *events: param.parameterized.Event) -> None:
        """Bring all derived state up to date after ``nodes``/``edges`` change.

        Everything derived from the graph (indexes, instance bookkeeping,
        selection, editors and views) is updated here, in one pass, instead
        of in a watcher per concern.
        """
        if events:
            names = {event.name for event in events if event.type == "triggered" or not self._same_items(event.old, event.new)}
            if not names:
                return
        else:
            names = {"nodes", "edges"}
        updates: dict[str, list[Any]] = {}
        if "nodes" in names:
            normalized_nodes = [self._coerce_node(node) for node in self.nodes]
            if any(n1 is not n2 for n1, n2 in zip(normalized_nodes, self.nodes, strict=False)):
                updates["nodes"] = normalized_nodes
        if "edges" in names:
            normalized_edges = [self._coerce_edge(edge) for edge in self.edges]
            if any(e1 is not e2 for e1, e2 in zip(normalized_edges, self.edges, strict=False)):
                updates["edges"] = normalized_edges
        if updates:
            # Re-enters this handler with the normalized lists.
            self.param.update(**updates)
            names -= set(updates)
            if not names:
                return
        self._schema_cache.clear()
        if "nodes" in names:
            self._rebuild_node_index()
        if "edges" in names:
            self._rebuild_edge_index()
        self._sync_instance_flow_refs()
        if "nodes" in names:
            self._update_node_data_param_watchers()
        if "edges" in names:
            self._update_edge_data_param_watchers()
        self._update_selection_from_graph()
        self._prune_editors()
        if "nodes" in names:
            self._update_views()
            self._refresh_node_editor_views()
        self._update_selected_editor()

    @staticmethod
    def _same_items(old: Any, new: Any) -> bool:
        return old is new or (isinstance(old, list) and isinstance(new, list) and len(old) == len(new) and all(o is n for o, n in zip(old, new, strict=True)))

    def _on_editor_config_change(self, *events: param.parameterized.Event) -> None:
        names = {event.name for event in events}
        self._schema_cache.clear()
        if names & {"editor_mode", "node_types", "node_editors", "default_node_editor"}:
            self._drop_editors("node")
        if names & {"edge_types", "edge_editors", "default_edge_editor"}:
            self._drop_editors("edge")
        if "editor_mode" in names:
            # Node components remount when the mode changes, which closes
            # every open toolbar.
            self._requested_node_editors.clear()
        self._refresh_node_editor_views()
        self._update_selected_editor()

    def _editor_cache(self, kind: str) -> dict[str, Any]:
        return self._node_editors if kind == "node" else self._edge_editors

    def _drop_editors(self, kind: str, item_ids: Sequence[str] | None = None) -> None:
        cache = self._editor_cache(kind)
        for item_id in list(cache) if item_ids is None else item_ids:
            cache.pop(item_id, None)
            self._editor_keys.pop((kind, item_id), None)
            self._editor_data.pop((kind, item_id), None)

    def _editor_key(self, kind: str, item: dict[str, Any] | Node | Edge) -> tuple[Any, ...]:
        """Describe what an editor is built from; a change requires a new editor."""
        if kind == "node":
            return (id(item) if isinstance(item, Node) else None, self._node_type(item))
        return (id(item) if isinstance(item, Edge) else None, self._edge_type(item))

    def _prune_editors(self) -> None:
        """Drop cached editors whose node/edge was removed, replaced or retyped."""
        for kind, index in (("node", self._node_index), ("edge", self._edge_index)):
            stale = []
            for item_id in self._editor_cache(kind):
                item = index.get(item_id)
                if item is None or self._editor_key(kind, item) != self._editor_keys.get((kind, item_id)):
                    stale.append(item_id)
                else:
                    self._sync_editor_data(kind, item_id, item)
            self._drop_editors(kind, stale)
        for node_id in [node_id for node_id in self._requested_node_editors if node_id not in self._node_index]:
            del self._requested_node_editors[node_id]

    def _sync_editor_data(self, kind: str, item_id: str, item: dict[str, Any] | Node | Edge) -> None:
        """Push data changed outside an editor into the cached editor.

        :class:`Editor` instances receive the new data on ``_data``; editors
        returned by other factories cannot be updated, so they are dropped and
        rebuilt the next time they are displayed.
        """
        key = (kind, item_id)
        if key not in self._editor_data:
            return
        data = self._node_data(item) if kind == "node" else self._edge_data(item)
        if data == self._editor_data[key]:
            return
        self._editor_data[key] = data
        if key == self._patching_editor:
            return
        editor = self._editor_cache(kind).get(item_id)
        if isinstance(editor, Editor):
            editor._data = dict(data)
        elif editor is not None:
            self._drop_editors(kind, [item_id])
            if kind == "node":
                self._refresh_node_editor_views()
            self._update_selected_editor()

    def _build_node_editor(self, node: dict[str, Any] | Node) -> Any:
        node_id = self._node_id(node)
        node_type = self._node_type(node)
        editor_factory = None
        if isinstance(node, Node) and type(node).editor is not Node.editor:
            editor_factory = node.editor
        if editor_factory is None:
            editor_factory = self.node_editors.get(node_type) or self.default_node_editor or SchemaEditor
        if not callable(editor_factory):
            return editor_factory
        schema = self._get_node_schema(node_type)
        return self._create_editor(editor_factory, node_id, self._node_data(node), schema, node_type, kind="node")

    def _build_edge_editor(self, edge: dict[str, Any] | Edge) -> Any:
        edge_id = self._edge_id(edge)
        edge_type = self._edge_type(edge)
        editor_factory = None
        if isinstance(edge, Edge) and type(edge).editor is not Edge.editor:
            editor_factory = edge.editor
        if editor_factory is None:
            editor_factory = self.edge_editors.get(edge_type) or self.default_edge_editor
        schema = self._get_edge_schema(edge_type) if edge_type else None
        if not (schema and schema.get("properties")) and isinstance(edge, Edge):
            instance_schema = edge._data_schema()
            if instance_schema.get("properties"):
                schema = instance_schema
        if editor_factory is None:
            # Edges are usually selected for their own sake, so unlike
            # nodes (whose editor sits behind a toolbar toggle) only pop
            # up an editor when there is a schema to build a form from.
            if not (schema and schema.get("properties")):
                return None
            editor_factory = SchemaEditor
        if not callable(editor_factory):
            return editor_factory
        return self._create_editor(editor_factory, edge_id, self._edge_data(edge), schema, edge_type, kind="edge")

    def _get_editor(self, kind: str, item_id: str) -> Any:
        """Return the (lazily built and cached) editor for a node or edge."""
        item = self._get_node(item_id) if kind == "node" else self._get_edge(item_id)
        if item is None:
            return None
        cache = self._editor_cache(kind)
        key = self._editor_key(kind, item)
        if item_id in cache and self._editor_keys.get((kind, item_id)) == key:
            return cache[item_id]
        editor = self._build_node_editor(item) if kind == "node" else self._build_edge_editor(item)
        cache[item_id] = editor
        self._editor_keys[(kind, item_id)] = key
        self._editor_data[(kind, item_id)] = self._node_data(item) if kind == "node" else self._edge_data(item)
        return editor

    def _get_node_editor(self, node_id: str) -> Any:
        return self._get_editor("node", node_id)

    def _get_edge_editor(self, edge_id: str) -> Any:
        return self._get_editor("edge", edge_id)

    def _refresh_node_editor_views(self) -> None:
        """Ship the node editors the frontend can currently display.

        In ``"node"`` mode every node shows its editor inline. In
        ``"toolbar"`` mode only editors whose toolbar was opened are built
        (the frontend sends ``editor_requested``), and in ``"side"`` mode the
        editor is rendered through ``_selected_editor`` instead.
        """
        if self.editor_mode == "node":
            node_ids = list(self._node_index)
        elif self.editor_mode == "toolbar":
            node_ids = [node_id for node_id in self._requested_node_editors if node_id in self._node_index]
        else:
            node_ids = []
        refs = [id(self._get_node_editor(node_id)) for node_id in node_ids]
        if node_ids == self._node_editor_ids and refs == self._editor_view_refs:
            return
        self._editor_view_refs = refs
        with param.discard_events(self):
            self._node_editor_ids = node_ids
        self.param.trigger("_node_editor_ids", "_node_editor_views")

    def _request_node_editor(self, node_id: str | None) -> None:
        if node_id is None or node_id in self._requested_node_editors or node_id not in self._node_index:
            return
        try:
            self._get_node_editor(node_id)
        except Exception:
            # A broken editor factory must not take down message handling.
            _LOGGER.exception("Failed to build the editor for node %r", node_id)
            return
        # Requested editors are kept (and shipped) until their node is removed
        # or the editor mode changes, so reopening a toolbar is instant. This
        # is bounded by the number of toolbars the user opened.
        self._requested_node_editors[node_id] = None
        self._refresh_node_editor_views()

    def _on_retyped(self, kind: str) -> None:
        """Rebuild what depends on node/edge types after a type patch."""
        self._schema_cache.clear()
        if kind == "node":
            self._rebuild_node_index()
        else:
            self._rebuild_edge_index()
        self._prune_editors()
        if kind == "node":
            self._refresh_node_editor_views()
        self._update_selected_editor()

    def _clear_selection_if_unselectable(self, *_: param.parameterized.Event) -> None:
        if self.selectable or self.selection == {"nodes": [], "edges": []}:
            return
        self._handle_msg({"type": "selection_changed", "nodes": [], "edges": []})

    def _update_selected_editor(self, *events: tuple[param.parameterized.Event]) -> None:
        selected_nodes = self.selection.get("nodes", [])
        selected_edges = self.selection.get("edges", [])
        editor = None
        if self.editable and selected_nodes and self.editor_mode == "side":
            editor = self._get_node_editor(selected_nodes[0])
        elif self.editable and selected_edges and not selected_nodes:
            editor = self._get_edge_editor(selected_edges[0])
        if editor is not None and editor is self._selected_editor_source:
            return
        self._selected_editor_source = editor
        self._selected_editor = None if editor is None else self._resolve_editor_view(editor)

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
        for node in self.nodes:
            view = self._node_view(node)
            if view is not None:
                views.append(self._resolve_editor_view(view))

        children: dict[str, list[UIElement] | UIElement | None] = {}
        old_models: list[UIElement] = []
        views, view_models = self._get_child_model(views, doc, root, parent, comm)
        self._patch_views(view_models)
        children["_views"] = views
        old_models += view_models

        if self.editor_mode == "side":
            children["_node_editor_views"] = []
        else:
            node_editors = [self._resolve_editor_view(self._get_node_editor(node_id)) for node_id in self._node_editor_ids]
            editor_models, editor_old = self._get_child_model(node_editors, doc, root, parent, comm)
            children["_node_editor_views"] = editor_models
            old_models += editor_old

        for name in ("top_panel", "bottom_panel", "left_panel", "right_panel", "_context_menu", "_value_popup", "_selected_editor"):
            panels = getattr(self, name, None)
            if panels is None:
                children[name] = None
            elif isinstance(panels, list):
                if panels:
                    panel_models, panel_old = self._get_child_model(panels, doc, root, parent, comm)
                    children[name] = panel_models
                    old_models += panel_old
                else:
                    children[name] = []
            else:
                panel_models, panel_old = self._get_child_model([panels], doc, root, parent, comm)
                children[name] = panel_models[0] if panel_models else None
                old_models += panel_old
        return children, old_models

    def _patch_views(self, view_models: list[UIElement]) -> None:
        for model in view_models:
            for fig in model.select({"type": figure}):
                if BK_FIGURE_CSS not in fig.stylesheets:
                    fig.stylesheets = fig.stylesheets + [BK_FIGURE_CSS]

    def _update_views(self) -> None:
        self._node_view_cache = {node_id: cached for node_id, cached in self._node_view_cache.items() if node_id in self._node_index}
        refs = [id(view) for view in map(self._node_view, self.nodes) if view is not None]
        # Rebuilding the children is expensive, so only do it when the views
        # themselves (or their order) changed.
        if refs != self._view_refs:
            self._view_refs = refs
            self.param.trigger("_views")
        self._node_update_count += 1

    def _process_param_change(self, params):
        params = super()._process_param_change(params)
        if "nodes" in params:
            nodes = []
            view_idx = 0
            for node in params["nodes"]:
                node = self._node_payload(node)
                view = node.pop("view", None)
                data = dict(node.get("data", {}))
                if view is not None:
                    data["view_idx"] = view_idx
                    view_idx += 1
                node["data"] = data
                nodes.append(node)
            params["nodes"] = nodes
        if "edges" in params:
            params["edges"] = [self._edge_payload(edge) for edge in params["edges"]]
        # Panel syncs a new value before `_normalize_specs` replaces it, so
        # schema classes must be coerced here to stay out of the payload.
        if "node_types" in params:
            params["node_types"] = _coerce_spec_map(params["node_types"])
        if "edge_types" in params:
            params["edge_types"] = _coerce_spec_map(params["edge_types"], edge=True)
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

    def add_node(self, node: dict[str, Any] | NodeSpec | Node) -> None:
        """Add a node to the graph.

        Adds a new node to the graph with optional validation. If a ``view``
        is included in the node dict or :class:`NodeSpec`, it will be embedded
        inside the node and rendered as Panel content.

        Parameters
        ----------
        node : dict or NodeSpec or Node
            Node dictionary, :class:`NodeSpec`, or :class:`Node` instance to add. The only
            required field is ``id``. Other fields have defaults:

            - ``id``: Unique node identifier (required)
            - ``position``: Dict with ``x`` and ``y`` coordinates
              (defaults to ``{"x": 0.0, "y": 0.0}``)
            - ``type``: Node type (defaults to ``"panel"``)
            - ``data``: Custom data dict (defaults to ``{}``)

        Raises
        ------
        ValueError
            If the node is missing the required ``id`` field, a node with
            the same ``id`` already exists, any field other than ``view`` is
            not JSON-serializable, or validation is enabled and the data
            doesn't match the schema.

        Examples
        --------
        Add a simple node:

        >>> flow = ReactFlow()
        >>> flow.add_node({
        ...     "id": "n1",
        ...     "position": {"x": 0, "y": 0},
        ...     "type": "panel",
        ...     "label": "My Node",
        ...     "data": {}
        ... })

        Add a node using NodeSpec:

        >>> from panel_reactflow import NodeSpec
        >>> flow.add_node(NodeSpec(
        ...     id="n2",
        ...     position={"x": 100, "y": 100},
        ...     label="Another Node"
        ... ))

        Add a node with embedded view via NodeSpec:

        >>> import panel as pn
        >>> flow.add_node(NodeSpec(
        ...     id="plot1",
        ...     position={"x": 200, "y": 0},
        ...     view=pn.pane.Markdown("# Hello World")
        ... ))

        Add a typed node with data:

        >>> flow.add_node(NodeSpec(
        ...     id="filter1",
        ...     type="filter",
        ...     position={"x": 300, "y": 100},
        ...     data={"threshold": 0.7, "operation": "gt"}
        ... ))

        See Also
        --------
        remove_node : Remove a node from the graph
        NodeSpec : Helper for constructing node dictionaries
        """
        self.add_nodes([node])

    def add_nodes(self, nodes: Sequence[dict[str, Any] | NodeSpec | Node]) -> None:
        """Add several nodes to the graph in a single update.

        Equivalent to calling :meth:`add_node` for each node, but ``nodes`` is
        assigned only once, so the graph is synced to the browser once
        instead of once per node. Use it to build or load large graphs.

        Every node is validated before any of them is added, so if one of
        them is invalid none are added. A ``node_added`` event is emitted per
        node, in order, after all of them were added.

        Parameters
        ----------
        nodes : sequence of dict or NodeSpec or Node
            The nodes to add, in any form accepted by :meth:`add_node`.

        Raises
        ------
        ValueError
            If any node is missing a required field or, with
            ``validate_on_add``, its data does not match its type's schema.

        Examples
        --------
        >>> flow = ReactFlow()
        >>> flow.add_nodes([
        ...     NodeSpec(id=f"n{i}", position={"x": 100 * i, "y": 0})
        ...     for i in range(100)
        ... ])

        See Also
        --------
        add_node : Add a single node
        add_edges : Add several edges in a single update
        remove_node : Remove one or more nodes in a single update
        """
        # One id set for the whole batch, grown as nodes are accepted, so that
        # duplicates within the batch are caught without rescanning the graph.
        node_ids = {self._node_id(node) for node in self.nodes}
        prepared = [self._prepare_node(node, node_ids) for node in nodes]
        if not prepared:
            return
        for raw_node, payload in prepared:
            if isinstance(raw_node, Node):
                raw_node.type = payload["type"]
                raw_node.data = dict(payload["data"])
                raw_node.position = dict(payload["position"])
                raw_node.selected = payload["selected"]
                raw_node.draggable = payload["draggable"]
                raw_node.connectable = payload["connectable"]
                raw_node.deletable = payload["deletable"]
                raw_node.flow = self
                self._sync_node_data_params_from_data(raw_node)
        self.nodes = self.nodes + [raw_node if isinstance(raw_node, Node) else payload for raw_node, payload in prepared]
        for _, payload in prepared:
            self._emit("node_added", {"type": "node_added", "node": payload})

    def _prepare_node(self, node: dict[str, Any] | NodeSpec | Node, node_ids: set[Any]) -> tuple[dict[str, Any] | Node, dict[str, Any]]:
        """Coerce and validate a node to add, returning it with its payload.

        *node_ids* holds the ids of the graph plus the nodes accepted earlier
        in the batch; the node's id is added to it once it passes.
        """
        raw_node = self._coerce_node(node)
        payload = self._node_payload(raw_node)
        payload.setdefault("type", "panel")
        payload.setdefault("data", {})
        payload.setdefault("position", {"x": 0.0, "y": 0.0})
        payload.setdefault("selected", False)
        payload.setdefault("draggable", True)
        payload.setdefault("connectable", True)
        payload.setdefault("deletable", True)
        self._check_node_payload(payload, node_ids)
        self._validate_graph_payload(payload, kind="node")
        if self.validate_on_add:
            schema = self._get_node_schema(payload.get("type", "panel"))
            _validate_data(payload.get("data", {}), schema)
        node_ids.add(payload["id"])
        return raw_node, payload

    def _apply_selection(self, node_ids: Sequence[str], edge_ids: Sequence[str]) -> None:
        """Set the ``selected`` flag of every node and edge from the browser selection.

        Only items whose flag actually changes are touched, and unselected
        dict items that never had the key are left alone, so that selecting
        one node in a large graph does not rewrite every node and edge.
        """
        node_set, edge_set = set(node_ids), set(edge_ids)
        with self._suppressed_prop_sync():
            for items, get_id, selected_ids in ((self.nodes, self._node_id, node_set), (self.edges, self._edge_id, edge_set)):
                for item in items:
                    selected = get_id(item) in selected_ids
                    if isinstance(item, (Node, Edge)):
                        if item.selected != selected:
                            item.selected = selected
                    elif item.get("selected", False) != selected:
                        item["selected"] = selected

    def _apply_sync(self, nodes: list[dict[str, Any]] | None, edges: list[dict[str, Any]] | None) -> None:
        """Apply a full graph payload coming from the frontend."""
        if nodes is not None:
            node_instances = {node.id: node for node in self.nodes if isinstance(node, Node)}
            synced_nodes: list[dict[str, Any] | Node] = []
            for payload in nodes:
                node = node_instances.get(payload.get("id"))
                if node is None:
                    synced_nodes.append(payload)
                    continue
                node.position = dict(payload.get("position", node.position))
                node.type = payload.get("type", node.type)
                node.label = payload.get("label", node.label)
                node.data = dict(payload.get("data", node.data))
                self._sync_node_data_params_from_data(node)
                node.selected = payload.get("selected", node.selected)
                node.draggable = payload.get("draggable", node.draggable)
                node.connectable = payload.get("connectable", node.connectable)
                node.deletable = payload.get("deletable", node.deletable)
                if "style" in payload:
                    node.style = payload.get("style")
                if "className" in payload:
                    node.className = payload.get("className")
                synced_nodes.append(node)
            self.nodes = synced_nodes
        if edges is not None:
            edge_instances = {edge.id: edge for edge in self.edges if isinstance(edge, Edge)}
            synced_edges: list[dict[str, Any] | Edge] = []
            for payload in edges:
                edge = edge_instances.get(payload.get("id"))
                if edge is None:
                    synced_edges.append(payload)
                    continue
                edge.source = payload.get("source", edge.source)
                edge.target = payload.get("target", edge.target)
                edge.label = payload.get("label", edge.label)
                edge.type = payload.get("type", edge.type)
                edge.selected = payload.get("selected", edge.selected)
                edge.data = dict(payload.get("data", edge.data))
                self._sync_edge_data_params_from_data(edge)
                if "style" in payload:
                    edge.style = payload.get("style")
                if "markerEnd" in payload:
                    edge.markerEnd = payload.get("markerEnd")
                if "sourceHandle" in payload:
                    edge.sourceHandle = payload.get("sourceHandle")
                if "targetHandle" in payload:
                    edge.targetHandle = payload.get("targetHandle")
                synced_edges.append(edge)
            self.edges = synced_edges

    def _handle_msg(self, msg: dict[str, Any]) -> None:
        """Handle sync messages from the frontend.

        Held so that every parameter update triggered by one frontend message
        is combined into a single patch. Without the hold each assignment syncs
        on its own and the browser renders every intermediate graph, e.g.
        deleting a multi-node selection shows the nodes disappearing one by one.
        """
        if not isinstance(msg, dict):
            return
        with hold():
            self._process_msg(msg)

    def _process_msg(self, msg: dict[str, Any]) -> None:
        match msg.get("type"):
            case "connection_validation_requested":
                self._validate_connection_request(msg)
            case "drop":
                self._emit(
                    "drop",
                    {
                        "type": "drop",
                        "drop_type": msg.get("drop_type"),
                        "data": (_decode_dropped_files(msg.get("data")) if msg.get("drop_type") == "Files" else msg.get("data")),
                        "position": msg.get("position"),
                        "target": msg.get("target"),
                        "rejected": msg.get("rejected") or [],
                    },
                )
            case "sync":
                nodes = msg.get("nodes")
                edges = msg.get("edges")
                with self._suppressed_prop_sync():
                    self._apply_sync(nodes, edges)
                self._emit("sync", msg)
            case "node_moved":
                node_id = msg.get("node_id")
                position = msg.get("position")
                if node_id is None or position is None:
                    return
                node = self._get_node(node_id)
                if node is not None:
                    self._node_set_position(node, position)
                self._emit("node_moved", msg)
            case "selection_changed":
                node_ids = msg.get("nodes") or []
                edge_ids = msg.get("edges") or []
                self._apply_selection(node_ids, edge_ids)
                self.selection = {"nodes": list(node_ids), "edges": list(edge_ids)}
                self._emit("selection_changed", msg)
            case "edge_added":
                self._add_edge_from_browser(msg.get("edge"))
            case "node_deleted":
                node_ids = list(msg.get("node_ids") or [])
                if msg.get("node_id") and msg["node_id"] not in node_ids:
                    node_ids.append(msg["node_id"])
                self.remove_node(node_ids)
            case "edge_deleted":
                edge_ids = list(msg.get("edge_ids") or [])
                if msg.get("edge_id") and msg["edge_id"] not in edge_ids:
                    edge_ids.append(msg["edge_id"])
                self.remove_edge(edge_ids)
            case "node_clicked":
                node_id = msg.get("node_id")
                if node_id is None:
                    return
                self._emit("node_clicked", msg)
            case "handle_clicked":
                node_id = msg.get("node_id")
                if node_id is None:
                    return
                if self.popup_trigger == "click":
                    self._emit("handle_clicked", msg)
            case "edge_clicked":
                edge_id = msg.get("edge_id")
                if edge_id is None:
                    return
                if self.popup_trigger == "click":
                    self._emit("edge_clicked", msg)
            case "handle_hovered":
                if self.popup_trigger == "hover" and msg.get("node_id") is not None:
                    self._emit("handle_hovered", msg)
            case "handle_unhovered":
                if self.popup_trigger == "hover" and msg.get("node_id") is not None:
                    self._emit("handle_unhovered", msg)
            case "edge_hovered":
                if self.popup_trigger == "hover" and msg.get("edge_id") is not None:
                    self._emit("edge_hovered", msg)
            case "edge_unhovered":
                if self.popup_trigger == "hover" and msg.get("edge_id") is not None:
                    self._emit("edge_unhovered", msg)
            case "node_context_menu":
                node_id = msg.get("node_id")
                position = msg.get("position")
                if node_id is None:
                    return
                node = self._get_node_instance(node_id)
                if node is None:
                    return
                menu = node.context_menu()
                if menu is None:
                    return
                self._context_menu = menu
                self._context_menu_position = position
                self._emit("node_context_menu", msg)
            case "editor_requested":
                self._request_node_editor(msg.get("node_id"))
            case "close_context_menu":
                self._context_menu = None
                self._context_menu_position = None
            case "close_value_popup":
                self._value_popup = None
                self._value_popup_position = None
            case "client_error":
                self._handle_client_error(msg)
            case _:
                return

    def _add_edge_from_browser(self, edge: Any) -> None:
        """Add an edge the user connected, or undo it in the browser.

        The browser draws a new connection before Python sees it. Validators
        are re-run on the final connection since the graph may have changed
        since the drag started, and a client is not trusted to have applied
        them. A rejected edge is removed from the browser again instead of
        lingering there without existing in ``edges``.
        """
        if not isinstance(edge, dict):
            return
        edge_id = edge.get("id")
        if edge_id:
            # Rejecting a colliding id would make the browser remove every edge
            # with that id, including the one Python already owns.
            edge_ids = {self._edge_id(e) for e in self.edges}
            new_id, suffix = edge_id, 1
            while new_id in edge_ids:
                new_id, suffix = f"{edge_id}:{suffix}", suffix + 1
            edge = {**edge, "id": new_id}
        reason = None
        if self._connection_validators:
            reason = self._connection_reason(
                {
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                    "sourceHandle": edge.get("sourceHandle"),
                    "targetHandle": edge.get("targetHandle"),
                }
            )
        if reason is None:
            try:
                self.add_edge(edge)
                return
            except ValueError as exc:
                reason = str(exc)
        _LOGGER.warning("panel-reactflow rejected edge %r from the browser: %s", edge.get("id"), reason)
        self._send_msg({"type": "edge_rejected", "edge_id": edge.get("id"), "reason": reason})

    def add_connection_validator(self, callback: Callable) -> None:
        """Register a validator returning None to allow or a reason to reject.

        Callbacks accept either a connection payload or (payload, flow).
        They run in registration order for each candidate when a drag starts,
        and again for the final connection when the browser reports the new
        edge; an edge rejected then is removed from the browser. Edges added
        from Python with :meth:`add_edge` or ``edges`` are not checked.
        """
        if not callable(callback):
            raise TypeError("Connection validator must be callable.")
        _accepts_flow(callback)
        self._connection_validators.append(callback)
        self.has_connection_validators = True

    def remove_connection_validator(self, callback: Callable) -> None:
        """Unregister a previously added connection validator."""
        self._connection_validators.remove(callback)
        self.has_connection_validators = bool(self._connection_validators)

    def _connection_reason(self, payload: dict[str, Any]) -> str | None:
        """Run the connection validators for one candidate connection."""
        for callback in self._connection_validators:
            try:
                if _accepts_flow(callback):
                    reason = callback(payload, self)
                else:
                    reason = callback(payload)
                if reason is not None and not isinstance(reason, str):
                    reason = "Connection validator must return None or a reason string."
                elif reason == "":
                    reason = "Connection rejected."
            except Exception as exc:
                _LOGGER.exception("Connection validator failed")
                reason = str(exc) or "Connection validator failed."
            if reason is not None:
                return reason
        return None

    @staticmethod
    def _handle_ids(handles: Any) -> list[Any]:
        if handles is None:
            return [None]
        if isinstance(handles, (str, dict)):
            handles = [handles]
        return [handle.get("id") if isinstance(handle, dict) else handle for handle in handles]

    def _validate_connection_request(self, msg: dict[str, Any]) -> None:
        # The browser blocks every connection until this reply arrives (or
        # times out after three seconds), so always answer, with an ``error``
        # the browser can show if the request could not be evaluated.
        reply: dict[str, Any] = {"type": "connection_validation_result", "request_id": msg.get("request_id"), "results": []}
        handle_type = msg.get("handle_type")
        if msg.get("node_id") is None or handle_type not in ("source", "target"):
            reply["error"] = "Malformed connection validation request."
            self._send_msg(reply)
            return
        try:
            reply["results"] = self._connection_candidates(msg["node_id"], msg.get("handle_id"), handle_type)
        except Exception:
            _LOGGER.exception("Connection validation failed")
            reply["results"] = []
            reply["error"] = "Connection validation failed."
        self._send_msg(reply)

    def _connection_candidates(self, node_id: str, handle_id: str | None, handle_type: str) -> list[dict[str, Any]]:
        opposite_type = "target" if handle_type == "source" else "source"
        port_key = "inputs" if opposite_type == "target" else "outputs"
        prefix = "input" if opposite_type == "target" else "output"
        results = []
        for node in self.nodes:
            connectable = node.connectable if isinstance(node, Node) else node.get("connectable", True)
            if connectable is False:
                continue
            candidate_id = self._node_id(node)
            node_type = self.node_types.get(self._node_type(node), {})
            # Handles the browser never lets a connection end on need no answer.
            if node_type.get(f"{prefix}Connectable") is False or node_type.get(f"{prefix}ConnectableEnd") is False:
                continue
            for candidate_handle in self._handle_ids(node_type.get(port_key)):
                if handle_type == "source":
                    payload = {"source": node_id, "target": candidate_id, "sourceHandle": handle_id, "targetHandle": candidate_handle}
                else:
                    payload = {"source": candidate_id, "target": node_id, "sourceHandle": candidate_handle, "targetHandle": handle_id}
                reason = self._connection_reason(payload)
                results.append({"node_id": candidate_id, "handle_id": candidate_handle, "handle_type": opposite_type, "reason": reason})
        return results

    def _handle_client_error(self, msg: dict[str, Any]) -> None:
        """Log a client-side error reported by the frontend and re-emit it.

        Rendering errors in the graph view previously died in the browser
        console, leaving the server with no record that the UI had broken. The
        frontend now reports them here so they land in the application log and
        can be handled via ``flow.on("client_error", ...)``.
        """
        source = msg.get("source", "unknown")
        message = msg.get("message", "Unknown error")
        if source == "safe_mode":
            _LOGGER.warning(
                "panel-reactflow %s Affected elements: %s",
                message,
                json.dumps(msg.get("issues", [])),
            )
        else:
            _LOGGER.error(
                "panel-reactflow client error (%s, attempt %s, mode %s): %s\n%s%s",
                source,
                msg.get("attempt", 0),
                msg.get("mode", "normal"),
                message,
                msg.get("stack") or "",
                msg.get("component_stack") or "",
            )
        self._emit("client_error", msg)

    def remove_node(self, *node_ids: str | Sequence[str]) -> None:
        """Remove one or more nodes and all connected edges from the graph.

        Removes the specified nodes and automatically removes any edges that
        are connected to them (either as source or target). This ensures the
        graph remains consistent.

        Ids may be passed as separate arguments or as a single sequence. All
        of them are removed in one update, i.e. ``nodes`` and ``edges`` are
        each assigned once. Removing nodes one at a time instead syncs an
        intermediate graph to the browser per node, which React Flow renders
        as the nodes disappearing one by one.

        Parameters
        ----------
        *node_ids : str or sequence of str
            Unique identifiers of the nodes to remove, either as separate
            arguments or as a single sequence. Ids that are not part of the
            graph are ignored.

        Examples
        --------
        Remove a node:

        >>> flow = ReactFlow()
        >>> flow.add_node(NodeSpec(id="n1", position={"x": 0, "y": 0}))
        >>> flow.add_node(NodeSpec(id="n2", position={"x": 100, "y": 0}))
        >>> flow.add_edge(EdgeSpec(id="e1", source="n1", target="n2"))
        >>>
        >>> flow.remove_node("n1")  # Also removes edge "e1"

        Remove several nodes in a single update:

        >>> flow.remove_node("n1", "n2")
        >>> flow.remove_node(["n1", "n2"])

        See Also
        --------
        add_node : Add a node to the graph
        remove_edge : Remove one or more edges from the graph
        """
        ids = list(dict.fromkeys(_flatten_ids(node_ids)))
        if not ids:
            return
        id_set = set(ids)
        removed_nodes = {}
        remaining_nodes = []
        for node in self.nodes:
            node_id = self._node_id(node)
            if node_id in id_set:
                removed_nodes.setdefault(node_id, node)
            else:
                remaining_nodes.append(node)
        removed_edges = [edge for edge in self.edges if self._edge_endpoints(edge) & id_set]
        updates: dict[str, Any] = {}
        if removed_nodes:
            updates["nodes"] = remaining_nodes
        if removed_edges:
            removed_refs = {id(edge) for edge in removed_edges}
            updates["edges"] = [edge for edge in self.edges if id(edge) not in removed_refs]
        if updates:
            self.param.update(**updates)
        # Report each edge under the first node that removed it, matching the
        # payloads emitted when removing the nodes sequentially.
        reported_edges: set[str | None] = set()
        for node_id in ids:
            deleted_edges = []
            for edge in removed_edges:
                edge_id = self._edge_id(edge)
                if edge_id in reported_edges or node_id not in self._edge_endpoints(edge):
                    continue
                reported_edges.add(edge_id)
                deleted_edges.append(edge_id)
            payload = {
                "type": "node_deleted",
                "node_id": node_id,
                "deleted_edges": deleted_edges,
            }
            self._emit("node_deleted", payload)
            removed_node = removed_nodes.get(node_id)
            if isinstance(removed_node, Node):
                removed_node.flow = None
                self._invoke_node_hook(removed_node, "on_delete", dict(payload))
                self._invoke_node_hook(removed_node, "on_event", dict(payload))

    @classmethod
    def _edge_endpoints(cls, edge: dict[str, Any] | Edge) -> set[str | None]:
        """Return the source and target node ids of *edge*."""
        if isinstance(edge, Edge):
            return {edge.source, edge.target}
        return {edge.get("source"), edge.get("target")}

    def add_edge(self, edge: dict[str, Any] | EdgeSpec | Edge) -> None:
        """Add an edge to the graph.

        Adds a new edge connecting two nodes with optional validation. If no
        ``id`` is provided, one will be automatically generated based on the
        source and target nodes.

        Parameters
        ----------
        edge : dict or EdgeSpec or Edge
            Edge dictionary, :class:`EdgeSpec`, or :class:`Edge` instance to add. Required
            fields are ``source`` and ``target``. Other fields have defaults:

            - ``source``: ID of the source node (required)
            - ``target``: ID of the target node (required)
            - ``id``: Unique edge identifier (auto-generated if not provided)
            - ``data``: Custom data dict (defaults to ``{}``)

        Raises
        ------
        ValueError
            If the edge is missing required fields (``source``, ``target``),
            either endpoint is not a node of the graph, an edge with the same
            ``id`` already exists, the edge is not JSON-serializable, or
            validation is enabled and the data doesn't match the schema.
            Self-loops are allowed.

        Notes
        -----
        Connection validators (:meth:`add_connection_validator`) only check
        connections made in the browser, not edges added from Python.

        Examples
        --------
        Add a simple edge:

        >>> flow = ReactFlow(nodes=[NodeSpec(id=f"n{i}") for i in range(1, 5)])
        >>> flow.add_edge({
        ...     "id": "e1",
        ...     "source": "n1",
        ...     "target": "n2"
        ... })

        Add an edge using EdgeSpec:

        >>> from panel_reactflow import EdgeSpec
        >>> flow.add_edge(EdgeSpec(
        ...     id="e2",
        ...     source="n2",
        ...     target="n3",
        ...     label="Connection"
        ... ))

        Add a typed edge with data:

        >>> flow.add_edge(EdgeSpec(
        ...     id="weighted1",
        ...     source="n1",
        ...     target="n3",
        ...     type="weighted",
        ...     data={"weight": 0.75}
        ... ))

        Add an edge with styling:

        >>> flow.add_edge(EdgeSpec(
        ...     id="e3",
        ...     source="n3",
        ...     target="n4",
        ...     style={"stroke": "#ff0000", "strokeWidth": 3},
        ...     markerEnd={"type": "arrowclosed", "color": "#ff0000"}
        ... ))

        See Also
        --------
        remove_edge : Remove an edge from the graph
        EdgeSpec : Helper for constructing edge dictionaries
        """
        self.add_edges([edge])

    def add_edges(self, edges: Sequence[dict[str, Any] | EdgeSpec | Edge]) -> None:
        """Add several edges to the graph in a single update.

        Equivalent to calling :meth:`add_edge` for each edge, but ``edges`` is
        assigned only once, so the graph is synced to the browser once
        instead of once per edge.

        Every edge is validated before any of them is added, so if one of
        them is invalid none are added. An ``edge_added`` event is emitted per
        edge, in order, after all of them were added.

        Parameters
        ----------
        edges : sequence of dict or EdgeSpec or Edge
            The edges to add, in any form accepted by :meth:`add_edge`.

        Raises
        ------
        ValueError
            If any edge is missing a required field or, with
            ``validate_on_add``, its data does not match its type's schema.

        Examples
        --------
        >>> flow.add_edges([
        ...     EdgeSpec(source=f"n{i}", target=f"n{i + 1}")
        ...     for i in range(99)
        ... ])

        See Also
        --------
        add_edge : Add a single edge
        add_nodes : Add several nodes in a single update
        remove_edge : Remove one or more edges in a single update
        """
        node_ids = {self._node_id(node) for node in self.nodes}
        edge_ids = {self._edge_id(edge) for edge in self.edges}
        prepared = [self._prepare_edge(edge, node_ids, edge_ids) for edge in edges]
        if not prepared:
            return
        for raw_edge, payload in prepared:
            if isinstance(raw_edge, Edge):
                raw_edge.id = payload["id"]
                raw_edge.data = dict(payload["data"])
                raw_edge.flow = self
                self._sync_edge_data_params_from_data(raw_edge)
        self.edges = self.edges + [raw_edge if isinstance(raw_edge, Edge) else payload for raw_edge, payload in prepared]
        for _, payload in prepared:
            self._emit("edge_added", {"type": "edge_added", "edge": payload})

    def _prepare_edge(self, edge: dict[str, Any] | EdgeSpec | Edge, node_ids: set[Any], edge_ids: set[Any]) -> tuple[dict[str, Any] | Edge, dict[str, Any]]:
        """Coerce and validate an edge to add, returning it with its payload.

        *node_ids* are the ids of the graph's nodes and *edge_ids* those of its
        edges plus the edges accepted earlier in the batch; the edge's id is
        added to *edge_ids* once it passes.
        """
        raw_edge = self._coerce_edge(edge)
        payload = self._edge_payload(raw_edge)
        payload.setdefault("data", {})
        self._check_edge_payload(payload, node_ids, edge_ids)
        if not payload.get("id"):
            payload["id"] = self._generate_edge_id(payload["source"], payload["target"])
        self._validate_graph_payload(payload, kind="edge")
        if self.validate_on_add:
            edge_type = payload.get("type")
            schema = self._get_edge_schema(edge_type) if edge_type else None
            _validate_data(payload.get("data", {}), schema)
        edge_ids.add(payload["id"])
        return raw_edge, payload

    def remove_edge(self, *edge_ids: str | Sequence[str]) -> None:
        """Remove one or more edges from the graph by their ID.

        Ids may be passed as separate arguments or as a single sequence. All
        of them are removed in one update, i.e. ``edges`` is only assigned
        once, so the browser renders a single update instead of one per edge.

        Parameters
        ----------
        *edge_ids : str or sequence of str
            Unique identifiers of the edges to remove, either as separate
            arguments or as a single sequence. Ids that are not part of the
            graph are ignored.

        Examples
        --------
        Remove an edge:

        >>> flow = ReactFlow(nodes=[NodeSpec(id="n1"), NodeSpec(id="n2")])
        >>> flow.add_edge(EdgeSpec(id="e1", source="n1", target="n2"))
        >>> flow.remove_edge("e1")

        Remove several edges in a single update:

        >>> flow.remove_edge("e1", "e2")
        >>> flow.remove_edge(["e1", "e2"])

        See Also
        --------
        add_edge : Add an edge to the graph
        remove_node : Remove one or more nodes from the graph
        """
        ids = list(dict.fromkeys(_flatten_ids(edge_ids)))
        if not ids:
            return
        id_set = set(ids)
        removed_edges = {}
        remaining_edges = []
        for edge in self.edges:
            edge_id = self._edge_id(edge)
            if edge_id in id_set:
                removed_edges.setdefault(edge_id, edge)
            else:
                remaining_edges.append(edge)
        if removed_edges:
            self.edges = remaining_edges
        for edge_id in ids:
            removed_edge = removed_edges.get(edge_id)
            if removed_edge is None:
                continue
            payload = {"type": "edge_deleted", "edge_id": edge_id}
            self._emit("edge_deleted", payload)
            if isinstance(removed_edge, Edge):
                removed_edge.flow = None
                self._invoke_edge_hook(removed_edge, "on_delete", dict(payload))
                self._invoke_edge_hook(removed_edge, "on_event", dict(payload))

    def _apply_data_patch(
        self,
        *,
        kind: str,
        item_id: str,
        patch: dict[str, Any],
        get_item: Callable[[str], Any],
        get_data: Callable[[Any], dict[str, Any]],
        set_data: Callable[[Any, dict[str, Any]], None],
        get_schema: Callable[[Any], dict[str, Any] | None],
        sync_from_data: Callable[[Any], None],
        instance_cls: type,
        remove: Sequence[str] = (),
    ) -> None:
        """Shared implementation for ``patch_node_data``/``patch_edge_data``.

        Merges ``patch`` into the matching item's ``data``, validating it
        against the item type's schema when ``validate_on_patch`` is enabled.
        Top-level React Flow fields are patched separately, by
        ``patch_node_props``/``patch_edge_props``.
        """
        _ensure_jsonable(patch, f"patch for {item_id!r}")
        item = get_item(item_id)
        if item is None:
            return
        data = get_data(item)
        for key in remove:
            data.pop(key, None)
        data.update(patch)
        if self.validate_on_patch:
            _validate_data(data, get_schema(item))
        set_data(item, data)
        if isinstance(item, instance_cls):
            sync_from_data(item)
        self._sync_editor_data(kind, item_id, item)

    def patch_node_data(self, node_id: str, patch: dict[str, Any], *, remove: Sequence[str] | None = None) -> None:
        """Update specific properties in a node's data dictionary.

        Merges the provided patch dictionary into the node's existing ``data``
        dict, allowing you to update individual properties without replacing
        the entire data object.

        Parameters
        ----------
        node_id : str
            Unique identifier of the node to update.
        patch : dict
            Dictionary of key-value pairs to merge into the node's ``data``.
            Existing keys will be updated, new keys will be added.
        remove : list of str, optional
            Keys to delete from the node's ``data`` before *patch* is
            merged in. Used by the JSON editors to apply deleted keys.

        Raises
        ------
        ValueError
            If validation is enabled (``validate_on_patch=True``) and the
            patched data doesn't match the node type's schema.

        Examples
        --------
        Update a single property:

        >>> flow = ReactFlow()
        >>> flow.add_node(NodeSpec(
        ...     id="n1",
        ...     position={"x": 0, "y": 0},
        ...     data={"threshold": 0.5, "name": "Filter"}
        ... ))
        >>>
        >>> # Update just the threshold
        >>> flow.patch_node_data("n1", {"threshold": 0.8})

        Update multiple properties:

        >>> flow.patch_node_data("n1", {
        ...     "threshold": 0.9,
        ...     "name": "Updated Filter"
        ... })

        Notes
        -----
        This method is typically called automatically by editors when users
        modify node properties in the UI. The patch is also sent to the
        frontend to keep the visualization in sync.

        A ``label`` (or ``_label``) key is kept in ``data`` *and* mirrored onto
        the node's top-level label, since that is where the node components
        read it from. Every other key is treated as opaque data; use
        :meth:`patch_node_props` for the remaining presentational fields.

        See Also
        --------
        patch_edge_data : Update edge data properties
        patch_node_props : Update top-level node properties
        add_node : Add a new node to the graph
        """
        self._apply_data_patch(
            kind="node",
            item_id=node_id,
            patch=patch,
            get_item=self._get_node,
            get_data=self._node_data,
            set_data=self._node_set_data,
            get_schema=lambda node: self._get_node_schema(self._node_type(node)),
            sync_from_data=self._sync_node_data_params_from_data,
            instance_cls=Node,
            remove=remove or (),
        )
        msg = {"type": "patch_node_data", "node_id": node_id, "patch": patch}
        event = {"type": "node_data_changed", "node_id": node_id, "patch": patch}
        if remove:
            msg["remove"] = event["remove"] = list(remove)
        self._send_msg(msg)
        self._emit("node_data_changed", event)
        mirrored = self._mirrored_props(patch, Node._mirrored_data_keys)
        if mirrored:
            self.patch_node_props(node_id, mirrored)

    def patch_edge_data(self, edge_id: str, patch: dict[str, Any], *, remove: Sequence[str] | None = None) -> None:
        """Update specific properties in an edge's data dictionary.

        Merges the provided patch dictionary into the edge's existing ``data``
        dict, allowing you to update individual properties without replacing
        the entire data object.

        Parameters
        ----------
        edge_id : str
            Unique identifier of the edge to update.
        patch : dict
            Dictionary of key-value pairs to merge into the edge's ``data``.
            Existing keys will be updated, new keys will be added.
        remove : list of str, optional
            Keys to delete from the edge's ``data`` before *patch* is
            merged in. Used by the JSON editors to apply deleted keys.

        Raises
        ------
        ValueError
            If validation is enabled (``validate_on_patch=True``) and the
            patched data doesn't match the edge type's schema.

        Examples
        --------
        Update edge properties:

        >>> flow = ReactFlow()
        >>> flow.add_edge(EdgeSpec(
        ...     id="e1",
        ...     source="n1",
        ...     target="n2",
        ...     data={"weight": 0.5, "label": "weak"}
        ... ))
        >>>
        >>> # Update just the weight
        >>> flow.patch_edge_data("e1", {"weight": 0.9, "label": "strong"})

        Notes
        -----
        This method is typically called automatically by editors when users
        modify edge properties in the UI. The patch is also sent to the
        frontend to keep the visualization in sync.

        A ``label`` key is kept in ``data`` *and* mirrored onto the edge's
        top-level label, which is what React Flow renders. Every other key is
        treated as opaque data; use :meth:`patch_edge_props` for the remaining
        presentational fields.

        See Also
        --------
        patch_node_data : Update node data properties
        patch_edge_props : Update top-level edge properties
        add_edge : Add a new edge to the graph
        """
        self._apply_data_patch(
            kind="edge",
            item_id=edge_id,
            patch=patch,
            get_item=self._get_edge,
            get_data=self._edge_data,
            set_data=self._edge_set_data,
            get_schema=lambda edge: self._get_edge_schema(self._edge_type(edge)) if self._edge_type(edge) else None,
            sync_from_data=self._sync_edge_data_params_from_data,
            instance_cls=Edge,
            remove=remove or (),
        )
        msg = {"type": "patch_edge_data", "edge_id": edge_id, "patch": patch}
        event = {"type": "edge_data_changed", "edge_id": edge_id, "patch": patch}
        if remove:
            msg["remove"] = event["remove"] = list(remove)
        self._send_msg(msg)
        self._emit("edge_data_changed", event)
        mirrored = self._mirrored_props(patch, Edge._mirrored_data_keys)
        if mirrored:
            self.patch_edge_props(edge_id, mirrored)

    @staticmethod
    def _mirrored_props(patch: dict[str, Any], mirrored: dict[str, str]) -> dict[str, Any]:
        return {prop: patch[key] for key, prop in mirrored.items() if key in patch}

    def _validate_prop_patch(self, patch: dict[str, Any], *, kind: str, allowed: tuple[str, ...]) -> None:
        unknown = [key for key in patch if key not in allowed]
        if not unknown:
            _ensure_jsonable(patch, f"{kind} property patch")
            return
        hint = f"Use patch_{kind}_data to update the {kind}'s data dictionary."
        prop_name = "property" if len(unknown) == 1 else "properties"
        raise ValueError(f"Cannot patch {kind} {prop_name} {unknown!r}; expected one of {list(allowed)!r}. {hint}")

    def _apply_prop_patch(self, obj: dict[str, Any] | Node | Edge, patch: dict[str, Any]) -> None:
        """Apply a top-level property patch to the Python-side node/edge."""
        if isinstance(obj, (Node, Edge)):
            # The watchers already sent (or are about to send) the patch, so
            # applying it here must not trigger a second message.
            with self._suppressed_prop_sync():
                for key, value in patch.items():
                    setattr(obj, key, value)
        else:
            for key, value in patch.items():
                if value is None:
                    obj.pop(key, None)
                else:
                    obj[key] = value

    def patch_node_props(self, node_id: str, patch: dict[str, Any]) -> None:
        """Update top-level properties of a node.

        Sends an incremental patch to the frontend, updating fields that live
        outside the node's ``data`` dictionary (``label``, ``style``, ``type``,
        ``position``, ...) without re-serializing the whole ``nodes`` list.

        Assigning to the corresponding parameter of a :class:`Node` instance
        calls this method for you, so this is mainly useful for nodes defined
        as plain dictionaries and for the frontend-driven ``position`` and
        ``selected`` fields.

        Parameters
        ----------
        node_id : str
            Unique identifier of the node to update.
        patch : dict
            Mapping of property names to new values. Supported keys are
            ``position``, ``type``, ``label``, ``selected``, ``draggable``,
            ``connectable``, ``deletable``, ``style`` and ``className``.
            Passing ``None`` clears the property so the node falls back to
            the CSS/theme default.

        Raises
        ------
        ValueError
            If *patch* contains a key that is not a top-level node property.

        Examples
        --------
        Restyle a node and rename it:

        >>> flow.patch_node_props("n1", {"style": {"border": "2px solid red"}, "label": "Failed"})

        Clear a style so the theme default applies again:

        >>> flow.patch_node_props("n1", {"style": None})

        Move a node programmatically:

        >>> flow.patch_node_props("n1", {"position": {"x": 120, "y": 40}})

        See Also
        --------
        patch_node_data : Update the node's ``data`` dictionary
        patch_edge_props : Update top-level edge properties
        """
        self._validate_prop_patch(patch, kind="node", allowed=Node._props)
        node = self._get_node(node_id)
        if node is not None:
            self._apply_prop_patch(node, patch)
            if "type" in patch:
                self._on_retyped("node")
        self._send_msg({"type": "patch_node_props", "node_id": node_id, "patch": patch})
        self._emit("node_props_changed", {"type": "node_props_changed", "node_id": node_id, "patch": patch})

    def patch_edge_props(self, edge_id: str, patch: dict[str, Any]) -> None:
        """Update top-level properties of an edge.

        Sends an incremental patch to the frontend, updating fields that live
        outside the edge's ``data`` dictionary (``label``, ``style``, ``type``,
        ``markerEnd``, ...) without re-serializing the whole ``edges`` list.

        Assigning to the corresponding parameter of an :class:`Edge` instance
        calls this method for you, so this is mainly useful for edges defined
        as plain dictionaries and for the frontend-driven ``selected`` field.

        Parameters
        ----------
        edge_id : str
            Unique identifier of the edge to update.
        patch : dict
            Mapping of property names to new values. Supported keys are
            ``source``, ``target``, ``label``, ``type``, ``selected``,
            ``style``, ``markerEnd``, ``sourceHandle`` and ``targetHandle``.
            Passing ``None`` clears the property so the edge falls back to
            the CSS/theme default.

        Raises
        ------
        ValueError
            If *patch* contains a key that is not a top-level edge property.

        Examples
        --------
        Highlight an edge and switch it to a step router:

        >>> flow.patch_edge_props("e1", {"style": {"stroke": "#ef4444", "strokeWidth": 6}, "type": "step"})

        Clear the style so the theme default applies again:

        >>> flow.patch_edge_props("e1", {"style": None})

        See Also
        --------
        patch_edge_data : Update the edge's ``data`` dictionary
        patch_node_props : Update top-level node properties
        """
        self._validate_prop_patch(patch, kind="edge", allowed=Edge._props)
        edge = self._get_edge(edge_id)
        if edge is not None:
            self._apply_prop_patch(edge, patch)
            if "type" in patch:
                self._on_retyped("edge")
        self._send_msg({"type": "patch_edge_props", "edge_id": edge_id, "patch": patch})
        self._emit("edge_props_changed", {"type": "edge_props_changed", "edge_id": edge_id, "patch": patch})

    def fit_view(
        self,
        *node_ids: str | Sequence[str],
        padding: float | None = None,
        duration: int | None = None,
        min_zoom: float | None = None,
        max_zoom: float | None = None,
        include_hidden_nodes: bool = False,
    ) -> None:
        """Pan and zoom the viewport so that the graph fits into view.

        The canvas fits the view once when it is first rendered. Call this
        method to fit it again, e.g. after adding nodes or loading a new graph.
        The resulting viewport is synced back to the ``viewport`` parameter.

        Parameters
        ----------
        *node_ids : str or sequence of str
            Only fit these nodes into view, either as separate arguments or as
            a single sequence. Fits every node when omitted.
        padding : float, optional
            Space around the fitted nodes as a fraction of the viewport size.
            Defaults to React Flow's ``0.1``.
        duration : int, optional
            Length of the animated transition in milliseconds. Jumps to the
            new viewport when omitted.
        min_zoom : float, optional
            Lower zoom bound for this fit, defaults to ``min_zoom``.
        max_zoom : float, optional
            Upper zoom bound for this fit, defaults to ``max_zoom``.
        include_hidden_nodes : bool, default False
            Whether hidden nodes are included in the fitted bounds.

        Raises
        ------
        ValueError
            If a node id is not part of the graph or an option is out of range.

        Examples
        --------
        Load a new graph and fit it into view:

        >>> flow.nodes = new_nodes
        >>> flow.fit_view()

        Zoom to two nodes with an animated transition:

        >>> flow.fit_view("n1", "n2", padding=0.2, duration=500)

        Notes
        -----
        A fit requested right after ``nodes`` changed waits until the browser
        has rendered and measured those nodes, so it fits the new graph rather
        than the previous one, even when the change is held back by
        ``pn.io.hold``.

        Calling this before the component is displayed does nothing, there is
        no canvas to fit yet and the first render fits the view anyway.
        """
        ids = _flatten_ids(node_ids)
        known = {self._node_id(node) for node in self.nodes}
        unknown = [node_id for node_id in ids if node_id not in known]
        if unknown:
            raise ValueError(f"Cannot fit view to unknown node ids: {unknown}")
        if padding is not None and padding < 0:
            raise ValueError("padding must be non-negative.")
        if duration is not None and duration < 0:
            raise ValueError("duration must be non-negative.")
        for name, zoom in (("min_zoom", min_zoom), ("max_zoom", max_zoom)):
            if zoom is not None and zoom <= 0:
                raise ValueError(f"{name} must be positive.")
        options: dict[str, Any] = {}
        if ids:
            options["nodes"] = [{"id": node_id} for node_id in ids]
        if padding is not None:
            options["padding"] = padding
        if duration is not None:
            options["duration"] = duration
        if min_zoom is not None:
            options["minZoom"] = min_zoom
        if max_zoom is not None:
            options["maxZoom"] = max_zoom
        if include_hidden_nodes:
            options["includeHiddenNodes"] = True
        # The fit may reach the browser before a held ``nodes`` change does, so
        # it names the node revision it has to wait for.
        self._send_msg({"type": "fit_view", "node_revision": self._node_update_count, "options": options})

    def to_networkx(self, *, multigraph: bool = False):
        """Convert the current graph to NetworkX format.

        Converts the ReactFlow graph state into a NetworkX graph object,
        preserving node positions, types, labels, and data. Useful for
        graph analysis, algorithms, and integration with the NetworkX
        ecosystem.

        Parameters
        ----------
        multigraph : bool, default False
            If ``True``, returns a ``MultiDiGraph`` allowing multiple edges
            between the same node pair. If ``False``, returns a ``DiGraph``
            with single edges between nodes.

        Returns
        -------
        networkx.DiGraph or networkx.MultiDiGraph
            NetworkX graph representation with node and edge data preserved.

            Node attributes include:
            - All properties from ``node["data"]``
            - ``position``: Node position dict
            - ``type``: Node type string
            - ``label``: Node label (if present)

            Edge attributes include:
            - All properties from ``edge["data"]``
            - ``label``: Edge label (if present)
            - ``type``: Edge type string (if present)
            - For MultiDiGraphs, ``key`` is the edge ID

        Raises
        ------
        ImportError
            If NetworkX is not installed.

        Examples
        --------
        Convert to NetworkX and run graph algorithms:

        >>> import networkx as nx
        >>> from panel_reactflow import ReactFlow, NodeSpec, EdgeSpec
        >>>
        >>> flow = ReactFlow()
        >>> flow.add_node(NodeSpec(id="A", position={"x": 0, "y": 0}))
        >>> flow.add_node(NodeSpec(id="B", position={"x": 100, "y": 0}))
        >>> flow.add_node(NodeSpec(id="C", position={"x": 50, "y": 100}))
        >>> flow.add_edge(EdgeSpec(id="e1", source="A", target="B"))
        >>> flow.add_edge(EdgeSpec(id="e2", source="B", target="C"))
        >>> flow.add_edge(EdgeSpec(id="e3", source="A", target="C"))
        >>>
        >>> G = flow.to_networkx()
        >>>
        >>> # Run NetworkX algorithms
        >>> shortest = nx.shortest_path(G, "A", "C")
        >>> print(shortest)  # ['A', 'C']
        >>>
        >>> centrality = nx.betweenness_centrality(G)
        >>> print(centrality)

        Convert with edge data:

        >>> flow = ReactFlow()
        >>> flow.add_node(NodeSpec(id="1", position={"x": 0, "y": 0}))
        >>> flow.add_node(NodeSpec(id="2", position={"x": 100, "y": 0}))
        >>> flow.add_edge(EdgeSpec(
        ...     id="e1",
        ...     source="1",
        ...     target="2",
        ...     data={"weight": 0.75, "distance": 10}
        ... ))
        >>>
        >>> G = flow.to_networkx()
        >>> print(G["1"]["2"]["weight"])  # 0.75

        See Also
        --------
        from_networkx : Create ReactFlow from NetworkX graph
        """
        try:
            import networkx as nx  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover
            raise ImportError("networkx is required for to_networkx.") from exc

        graph = nx.MultiDiGraph() if multigraph else nx.DiGraph()
        for node in self.nodes:
            payload = self._node_payload(node)
            data = dict(payload.get("data", {}))
            data.update({"position": payload.get("position"), "type": payload.get("type")})
            if payload.get("label") is not None:
                data["label"] = payload.get("label")
            graph.add_node(payload["id"], **data)
        for edge in self.edges:
            payload = self._edge_payload(edge)
            data = dict(payload.get("data", {}))
            if payload.get("label") is not None:
                data["label"] = payload["label"]
            if payload.get("type") is not None:
                data["type"] = payload["type"]
            if payload.get("sourceHandle") is not None:
                data["sourceHandle"] = payload["sourceHandle"]
            if payload.get("targetHandle") is not None:
                data["targetHandle"] = payload["targetHandle"]
            if multigraph:
                graph.add_edge(payload["source"], payload["target"], key=payload.get("id"), **data)
            else:
                graph.add_edge(payload["source"], payload["target"], **data)
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

        Converts a NetworkX graph into a ReactFlow component, extracting
        node positions, labels, and data from node/edge attributes. This
        enables seamless integration with NetworkX's graph creation and
        analysis capabilities.

        Parameters
        ----------
        graph : networkx.Graph
            A NetworkX graph instance (directed or undirected). Node attributes
            are converted to node data, and edge attributes to edge data.
        node_type : str, default "panel"
            Default node type assigned to all nodes. Use a custom type if you
            want schema validation or custom rendering.
        default_position : tuple of float, default (0.0, 0.0)
            Default (x, y) position used when a node doesn't have a ``position``
            attribute. Nodes can specify position via a ``position`` attribute
            as either a dict ``{"x": ..., "y": ...}`` or tuple/list ``[x, y]``.

        Returns
        -------
        ReactFlow
            A new ReactFlow instance populated with nodes and edges from the
            NetworkX graph.

        Examples
        --------
        Create from a simple NetworkX graph:

        >>> import networkx as nx
        >>> from panel_reactflow import ReactFlow
        >>>
        >>> G = nx.DiGraph()
        >>> G.add_node("A", position={"x": 0, "y": 0}, label="Start")
        >>> G.add_node("B", position={"x": 100, "y": 0}, label="Process")
        >>> G.add_node("C", position={"x": 200, "y": 0}, label="End")
        >>> G.add_edge("A", "B", weight=0.5)
        >>> G.add_edge("B", "C", weight=0.8)
        >>>
        >>> flow = ReactFlow.from_networkx(G)

        Use with NetworkX graph generators:

        >>> G = nx.karate_club_graph()
        >>> # Add positions using a layout algorithm
        >>> pos = nx.spring_layout(G, scale=500)
        >>> for node_id, (x, y) in pos.items():
        ...     G.nodes[node_id]["position"] = {"x": x, "y": y}
        >>>
        >>> flow = ReactFlow.from_networkx(G)

        Custom node type and attributes:

        >>> G = nx.DiGraph()
        >>> G.add_node("filter1", position=[0, 0], threshold=0.5, op="gt")
        >>> G.add_node("filter2", position=[100, 0], threshold=0.7, op="lt")
        >>> G.add_edge("filter1", "filter2", label="pipe")
        >>>
        >>> flow = ReactFlow.from_networkx(G, node_type="filter")

        Notes
        -----
        Node attributes are converted as follows:
        - ``position``: Used directly (converted to dict if tuple/list)
        - ``label``: Used as node label
        - ``type``: Overrides the default ``node_type`` parameter
        - ``data``: Merged with other attributes as node data
        - All other attributes become node data properties

        Edge attributes are converted as follows:
        - ``label``: Used as edge label
        - ``type``: Used as edge type
        - ``data``: Merged with other attributes as edge data
        - All other attributes become edge data properties
        - For MultiDiGraphs, the edge key becomes the edge ID

        See Also
        --------
        to_networkx : Convert ReactFlow to NetworkX graph
        """
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        original_ids: dict[str, Any] = {}
        for node_id, attrs in graph.nodes(data=True):
            # Ids are stringified for the browser, so e.g. 1 and "1" would
            # silently become one node.
            if str(node_id) in original_ids:
                raise ValueError(
                    f"Nodes {original_ids[str(node_id)]!r} and {node_id!r} both map to the id {str(node_id)!r}; relabel the graph so node ids are unique as strings."
                )
            original_ids[str(node_id)] = node_id
            attrs = dict(attrs)  # avoids mutating the original graph
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
        edge_ids: set[str] = set()
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
            source_handle = edge_data.pop("sourceHandle", None)
            target_handle = edge_data.pop("targetHandle", None)
            edge_id = str(key if key is not None else f"{source}->{target}")
            if edge_id in edge_ids:
                # Multigraph keys are only unique per node pair (networkx
                # numbers parallel edges 0, 1, ...).
                edge_id = f"{source}->{target}:{key}"
            edge_ids.add(edge_id)
            edge = {
                "id": edge_id,
                "source": str(source),
                "target": str(target),
                "data": edge_data,
            }
            if label is not None:
                edge["label"] = label
            if edge_type is not None:
                edge["type"] = edge_type
            if source_handle is not None:
                edge["sourceHandle"] = source_handle
            if target_handle is not None:
                edge["targetHandle"] = target_handle
            edges.append(edge)
        return cls(nodes=nodes, edges=edges)

    def show_popup(self, content: Any, position: dict[str, float]) -> None:
        """Show a Panel viewable in a floating overlay at a screen position.

        Intended for use from ``"handle_clicked"`` and ``"edge_clicked"``
        callbacks to display, e.g., the current value flowing through a port
        or connection, but can be called for any reason.

        Parameters
        ----------
        content
            A Panel viewable (or anything ``pn.panel()`` accepts) to render
            inside the popup.
        position
            Screen position with ``x``/``y`` keys, typically taken straight
            from the ``"position"`` field of the triggering event payload.

        Examples
        --------
        >>> def on_handle_clicked(payload, flow):
        ...     value = compute_value(payload["node_id"], payload["handle_id"])
        ...     flow.show_popup(pn.pane.Markdown(f"Value: {value}"), payload["position"])
        >>>
        >>> flow.on("handle_clicked", on_handle_clicked)

        See Also
        --------
        close_popup : Dismiss the popup programmatically.

        Notes
        -----
        Set ``popup_trigger="hover"`` to use the configured dwell delay
        and movement distance before and after opening a value popup. Set it to
        ``"none"`` to disable automatic inspection events.
        """
        self._value_popup = content
        self._value_popup_position = position

    def close_popup(self) -> None:
        """Dismiss the popup shown by :meth:`show_popup`, if any."""
        self._value_popup = None
        self._value_popup_position = None

    def on(self, event_type: str, callback) -> None:
        """Register a callback for graph events.

        Subscribe to events emitted by the ReactFlow component. Events are
        triggered by user interactions (node moves, selections, etc.) and
        programmatic changes (adding/removing nodes/edges).

        Parameters
        ----------
        event_type : str
            Type of event to listen for. Use ``"*"`` to listen to all events.
            Available event types:

            - ``"node_added"``: Node was added to the graph
            - ``"node_deleted"``: Node was removed from the graph
            - ``"node_moved"``: Node was dragged to a new position
            - ``"node_clicked"``: Node was clicked. Payload has ``node_id``
              and a screen ``position`` suitable for :meth:`show_popup`.
              Clicks on the node's embedded view or editor are not reported,
              and a double click emits two events. Also fires when
              ``editable=False``.
            - ``"handle_clicked"``: A node's input/output handle was clicked.
              Payload has ``node_id``, ``handle_id`` (``None`` for a node's
              unnamed default handle), ``direction`` (``"input"`` or
              ``"output"``) and a screen ``position`` suitable for
              :meth:`show_popup`.
            - ``"edge_clicked"``: An edge was clicked. Payload has
              ``edge_id`` and a screen ``position`` suitable for
              :meth:`show_popup`.
            - ``"handle_hovered"`` / ``"edge_hovered"``: A port or edge was
              entered while ``popup_trigger="hover"``.
            - ``"handle_unhovered"`` / ``"edge_unhovered"``: The pointer left
              the inspected port or edge.
            - ``"node_data_changed"``: Node data was modified
            - ``"node_props_changed"``: Top-level node properties (``label``,
              ``style``, ``type``, ...) were modified
            - ``"edge_added"``: Edge was added to the graph
            - ``"edge_deleted"``: Edge was removed from the graph
            - ``"edge_data_changed"``: Edge data was modified
            - ``"edge_props_changed"``: Top-level edge properties (``label``,
              ``style``, ``type``, ...) were modified
            - ``"selection_changed"``: Selection changed
            - ``"sync"``: Full graph sync from frontend
            - ``"drop"``: Data of one of the ``drop_types`` was dropped onto
              the canvas. Payload has the matched ``drop_type``, the dropped
              ``data`` (parsed from JSON when possible, otherwise the raw
              string; for ``"Files"`` a list of ``{"name", "type", "size",
              "content"}``, where ``content`` is a ``str`` for a file that
              decodes as UTF-8 text, whatever its reported MIME type, and
              ``bytes`` for any other file), the
              drop ``position`` in flow coordinates, a ``target`` of
              ``{"node_id", "handle_id", "direction"}`` when dropped on a
              node or handle (``handle_id`` and ``direction`` are ``None``
              for a node), else ``None``, and a ``rejected`` list of
              ``{"name", "type", "size", "reason"}`` for files that were
              not sent because they exceed ``max_drop_size`` or could not
              be read. A non-file drop larger than ``max_drop_size`` is
              sent with ``data=None`` and one ``rejected`` entry.
            - ``"client_error"``: The graph view hit a rendering error in the
              browser. The payload carries ``source``, ``message``, ``stack``,
              ``component_stack``, ``attempt`` and ``mode``, or for
              ``source="safe_mode"`` the list of hidden ``issues``.
            - ``"*"``: All events (wildcard)
        callback : callable
            Function called when the event occurs. Receives the event payload
            dictionary as the first argument. The callback may optionally
            accept the ``ReactFlow`` instance as a second argument, e.g.
            ``callback(payload, flow)``. An exception raised by a callback
            is logged and does not stop the remaining callbacks or hooks;
            the change that triggered the event is applied either way.

        Examples
        --------
        Listen for node movements:

        >>> from panel_reactflow import ReactFlow
        >>>
        >>> flow = ReactFlow()
        >>>
        >>> def on_move(payload, flow):
        ...     node_id = payload["node_id"]
        ...     position = payload["position"]
        ...     print(f"Node {node_id} moved to ({position['x']}, {position['y']})")
        ...     print(f"Graph currently has {len(flow.nodes)} nodes")
        >>>
        >>> flow.on("node_moved", on_move)

        Track all graph changes:

        >>> def on_any_event(payload):
        ...     event_type = payload["type"]
        ...     print(f"Event: {event_type}")
        >>>
        >>> flow.on("*", on_any_event)

        Build a workflow tracker:

        >>> nodes_added = []
        >>> edges_added = []
        >>>
        >>> def track_node(event):
        ...     nodes_added.append(event["node"])
        >>>
        >>> def track_edge(event):
        ...     edges_added.append(event["edge"])
        >>>
        >>> flow.on("node_added", track_node)
        >>> flow.on("edge_added", track_edge)

        React to selection changes:

        >>> def on_selection(event):
        ...     selected_nodes = event["nodes"]
        ...     selected_edges = event["edges"]
        ...     print(f"Selected {len(selected_nodes)} nodes and {len(selected_edges)} edges")
        >>>
        >>> flow.on("selection_changed", on_selection)

        Update analytics on data changes:

        >>> def on_data_change(event):
        ...     node_id = event["node_id"]
        ...     patch = event["patch"]
        ...     print(f"Node {node_id} updated: {patch}")
        ...     # Update database, trigger recalculation, etc.
        >>>
        >>> flow.on("node_data_changed", on_data_change)

        Show the current value of a port when its handle is clicked:

        >>> import panel as pn
        >>> def on_handle_clicked(payload, flow):
        ...     value = my_values[payload["node_id"]][payload["handle_id"]]
        ...     flow.show_popup(pn.pane.Markdown(f"**Value:** {value}"), payload["position"])
        >>>
        >>> flow.on("handle_clicked", on_handle_clicked)

        Notes
        -----
        Multiple callbacks can be registered for the same event type.
        They will be called in the order they were registered.

        The ``"*"`` wildcard receives all events, making it useful for
        logging, debugging, or implementing undo/redo functionality.

        Event payloads always include a ``"type"`` field indicating the
        event type, plus event-specific fields.
        """
        _accepts_flow(callback)
        self._event_handlers.setdefault(event_type, []).append(callback)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        # schedule=False: see the matching comment on _invoke_node_callback.
        # This runs the message handling that triggered the event, so
        # deferring to the next tick would break Child-param updates (e.g.
        # via show_popup) made from inside a registered callback.
        # Callbacks are isolated from each other: a raising callback is
        # logged and the remaining callbacks and hooks still run.
        for callback in list(self._event_handlers.get(event_type, [])):
            self._invoke_node_callback(callback, payload, self)
        for callback in list(self._event_handlers.get("*", [])):
            self._invoke_node_callback(callback, payload, self)
        self._dispatch_node_hooks(event_type, payload)
        self._dispatch_edge_hooks(event_type, payload)

    def _update_selection_from_graph(self, *_: param.parameterized.Event) -> None:
        selected_node_ids: list[str] = []
        for node in self.nodes:
            node_id = self._node_id(node)
            if node_id is None:
                continue
            is_selected = node.selected if isinstance(node, Node) else node.get("selected")
            if is_selected:
                selected_node_ids.append(node_id)
        selected_edge_ids: list[str] = []
        for edge in self.edges:
            edge_id = self._edge_id(edge)
            if edge_id is None:
                continue
            is_selected = edge.selected if isinstance(edge, Edge) else edge.get("selected")
            if is_selected:
                selected_edge_ids.append(edge_id)
        selection = {
            "nodes": selected_node_ids,
            "edges": selected_edge_ids,
        }
        if selection != self.selection:
            self.selection = selection
            self._emit(
                "selection_changed",
                {"type": "selection_changed", "nodes": selection["nodes"], "edges": selection["edges"]},
            )

    def _normalize_specs(self, *events: param.parameterized.Event) -> None:
        for event in events:
            normalized = _coerce_spec_map(event.new, edge=event.name == "edge_types")
            if normalized != event.new:
                setattr(self, event.name, normalized)

    @staticmethod
    def _generate_edge_id(source: str, target: str) -> str:
        existing = f"{source}->{target}"
        return f"{existing}-{uuid4().hex[:8]}"

    @staticmethod
    def _coerce_node(node: dict[str, Any] | NodeSpec | Node):
        if isinstance(node, Node):
            return node
        if isinstance(node, NodeSpec):
            return node.to_dict()
        return node.to_dict() if hasattr(node, "to_dict") else node

    @staticmethod
    def _coerce_edge(edge: dict[str, Any] | EdgeSpec | Edge):
        if isinstance(edge, Edge):
            return edge
        if isinstance(edge, EdgeSpec):
            return edge.to_dict()
        return edge.to_dict() if hasattr(edge, "to_dict") else edge

    def _check_node_payload(self, payload: dict[str, Any], node_ids: set[Any] | None = None) -> None:
        """Raise ValueError if *payload* cannot be added as a new node.

        Rejects a missing or duplicate id and data that cannot be synced to
        the browser. *node_ids* are the ids already in the graph; pass a set
        when checking many nodes to avoid rebuilding it per node.
        """
        node_id = payload.get("id")
        if node_id is None or node_id == "":
            raise ValueError("Missing 'id' in node payload.")
        # The view is rendered as a Bokeh model, everything else is sent as JSON.
        _ensure_jsonable({key: value for key, value in payload.items() if key != "view"}, f"node {node_id!r}")
        if node_ids is None:
            node_ids = {self._node_id(node) for node in self.nodes}
        if node_id in node_ids:
            raise _GraphConflictError(f"A node with id {node_id!r} already exists.")

    def _check_edge_payload(
        self,
        payload: dict[str, Any],
        node_ids: set[Any] | None = None,
        edge_ids: set[Any] | None = None,
    ) -> None:
        """Raise ValueError if *payload* cannot be added as a new edge.

        Rejects a missing ``source``/``target``, endpoints that are not nodes
        of the graph, a duplicate id and data that cannot be synced to the
        browser. Self-loops are allowed. *node_ids* and *edge_ids* are the ids
        already in the graph; pass sets when checking many edges.
        """
        for key in ("source", "target"):
            if payload.get(key) is None:
                raise ValueError(f"Missing '{key}' in edge payload.")
        edge_id = payload.get("id")
        _ensure_jsonable(payload, f"edge {edge_id!r}")
        if node_ids is None:
            node_ids = {self._node_id(node) for node in self.nodes}
        for key in ("source", "target"):
            if payload[key] not in node_ids:
                raise _GraphConflictError(f"Edge {payload.get('id')!r} references unknown {key} node {payload[key]!r}.")
        if edge_id:
            if edge_ids is None:
                edge_ids = {self._edge_id(edge) for edge in self.edges}
            if edge_id in edge_ids:
                raise _GraphConflictError(f"An edge with id {edge_id!r} already exists.")

    def _check_graph(self, nodes: list[Any], edges: list[Any]) -> None:
        """Validate a complete graph passed to the constructor.

        Malformed or non-JSON items raise, since they break every sync.
        Duplicate ids and edges pointing at missing nodes only warn, to keep
        accepting graphs that rendered before these checks existed.
        """
        node_ids: set[Any] = set()
        for node in nodes:
            if isinstance(node, Node):
                # Node.to_dict() would build the node's view just for the check.
                data = dict(node.data or {})
                data.update({name: getattr(node, name) for name in node._data_param_names()})
                payload = {"id": node.id, "position": node.position, "label": node.label, "style": node.style, "data": data}
            else:
                payload = self._node_payload(node)
            try:
                self._check_node_payload(payload, node_ids)
            except _GraphConflictError as exc:
                _LOGGER.warning("panel-reactflow %s", exc)
            node_ids.add(payload["id"])
        edge_ids: set[Any] = set()
        for edge in edges:
            payload = self._edge_payload(edge)
            try:
                self._check_edge_payload(payload, node_ids, edge_ids)
            except _GraphConflictError as exc:
                _LOGGER.warning("panel-reactflow %s", exc)
            edge_ids.add(payload.get("id"))

    def _validate_graph_payload(self, payload: dict[str, Any], *, kind: str) -> None:
        required = {"node": ["id", "position", "data"], "edge": ["id", "source", "target"]}[kind]
        for key in required:
            if key not in payload:
                raise ValueError(f"Missing '{key}' in {kind} payload.")
