"""Example with multiple node types, each with its own schema.

Demonstrates how different node types can have different schemas and
the default :class:`SchemaEditor` auto-generates the right form
for each type.  A ``param.Parameterized`` class is used as a schema
source for the ``config`` type, while ``decision`` uses a raw JSON
Schema dict.
"""

import param
import panel as pn

from panel_reactflow import NodeType, ReactFlow

pn.extension('jsoneditor')

# -- Schema sources ----------------------------------------------------------

# 1) A Param class used as a schema source (auto-converted to JSON Schema).
class ServerConfig(param.Parameterized):
    host = param.String(default="localhost")
    port = param.Integer(default=8080)
    debug = param.Boolean(default=False)


# 2) A raw JSON Schema dict.
decision_schema = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "title": "Question"},
        "outcome": {
            "type": "string",
            "enum": ["yes", "no", "maybe"],
            "title": "Outcome",
        },
    },
}

# -- Node types --------------------------------------------------------------

node_types = {
    "config": NodeType(
        type="config",
        label="Config",
        schema=ServerConfig,                # Param class → auto JSON Schema
        inputs=["trigger"],
        outputs=["result"],
    ),
    "decision": NodeType(
        type="decision",
        label="Decision",
        schema=decision_schema,             # Raw JSON Schema dict
        inputs=["in"],
        outputs=["yes", "no"],
    ),
    "plain": NodeType(
        type="plain",
        label="Plain",
        # No schema → falls back to raw JSON editor
    ),
}

# -- Build graph -------------------------------------------------------------

nodes = [
    {
        "id": "cfg1",
        "type": "config",
        "position": {"x": 0, "y": 0},
        "label": "Config",
        "data": {"host": "10.0.0.1", "port": 3000, "debug": True},
    },
    {
        "id": "dec1",
        "type": "decision",
        "position": {"x": 300, "y": 0},
        "label": "Decision",
        "data": {"question": "Deploy?", "outcome": "yes"},
    },
    {
        "id": "plain1",
        "type": "plain",
        "position": {"x": 600, "y": 0},
        "label": "Plain",
        "data": {"notes": "free-form data"},
    },
]

edges = [
    {"id": "e1", "source": "cfg1", "target": "dec1"},
    {"id": "e2", "source": "dec1", "target": "plain1"},
]

ReactFlow(
    nodes=nodes,
    edges=edges,
    node_types=node_types,
    editor_mode="node",
    sizing_mode="stretch_both",
).servable()
