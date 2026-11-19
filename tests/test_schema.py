"""Tests for the JSON Schema form generation and the schema-driven editors."""

import datetime as dt
import json
import logging
import subprocess
import sys
import textwrap

import panel as pn
import param
import pytest

from panel_reactflow import NodeType, ReactFlow
from panel_reactflow.base import JsonEditor, SchemaEditor
from panel_reactflow.schema import JSONSchema, to_json_value


def _form(properties, data=None, **params):
    return JSONSchema(data or {}, schema=properties, multi=False, **params)


def _kinds(form):
    return {name: type(widget).__name__ for name, widget in form._widgets.items()}


def _editor(flow, node_id):
    getter = getattr(flow, "_get_node_editor", None)
    return getter(node_id) if getter is not None else flow._node_editors[node_id]


def _run_isolated(code):
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1]


# Widget dependencies


def test_importing_schema_does_not_change_global_design():
    out = _run_isolated(
        """
        import panel as pn
        before = pn.config.design
        import panel_reactflow.schema
        print(pn.config.design is before)
        """
    )
    assert out == "True"


def test_schema_keeps_design_of_app_that_imports_material_ui():
    pytest.importorskip("panel_material_ui")
    out = _run_isolated(
        """
        import panel as pn
        import panel_material_ui
        design = pn.config.design
        import panel_reactflow.schema
        print(pn.config.design is design and design is not None)
        """
    )
    assert out == "True"


def test_schema_falls_back_to_panel_widgets_without_material_ui():
    out = _run_isolated(
        """
        import sys
        sys.modules["panel_material_ui"] = None
        import panel as pn
        from panel_reactflow.schema import JSONSchema
        form = JSONSchema({"x": 1}, schema={"x": {"type": "integer", "minimum": 0, "maximum": 5}}, multi=False)
        print(type(form._widgets["x"]) is pn.widgets.IntSlider, type(form.layout) is pn.Column)
        """
    )
    assert out == "True True"


# Schema resolution


@pytest.mark.parametrize(
    "schema, kind",
    [
        ({"type": "number", "minimum": 0, "maximum": 1}, "FloatSlider"),
        ({"type": "integer", "minimum": 0, "maximum": 10}, "IntSlider"),
        ({"type": "number", "inclusiveMinimum": 0, "inclusiveMaximum": 1}, "FloatSlider"),
        ({"type": "integer", "minimum": 0}, "IntInput"),
        ({"type": "number"}, "FloatInput"),
        ({"type": "string"}, "TextInput"),
        ({"type": "boolean"}, "Checkbox"),
        ({"title": "Only a title"}, "LiteralInput"),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, "IntInput"),
        ({"anyOf": [{"type": "integer", "minimum": 0, "maximum": 3}, {"type": "null"}]}, "IntSlider"),
        ({"anyOf": [{"type": "string"}, {"type": "string"}], "enum": ["a", "b"]}, "Select"),
        ({"anyOf": [{"type": "integer"}, {"type": "string"}]}, "LiteralInput"),
        ({"type": ["string", "null"]}, "TextInput"),
        ({"type": ["string", "integer"]}, "LiteralInput"),
        ({"type": "object", "properties": {"a": {"type": "integer"}}}, "LiteralInput"),
        ({"type": "array", "items": {"type": "string"}}, "LiteralInput"),
        ({"type": "array", "items": {"enum": ["a", "b"]}}, "MultiChoice"),
        ({"type": "array", "items": [{"type": "string"}, {"type": "string"}], "additionalItems": False}, "LiteralInput"),
        ({"type": "array", "minItems": 2, "maxItems": 2, "additionalItems": {"type": "number", "minimum": 0, "maximum": 5}}, "RangeSlider"),
        ({"type": "array", "prefixItems": [{"type": "integer", "minimum": 0, "maximum": 9}] * 2}, "IntRangeSlider"),
        ({"type": "string", "const": "only"}, "Select"),
        ({"oneOf": [{"const": "a"}, {"const": "b"}]}, "Select"),
        ({"type": "string", "format": "date"}, "DatePicker"),
        ({"type": "string", "format": "date-time"}, "DatetimePicker"),
    ],
)
def test_property_widget_types(schema, kind):
    assert _kinds(_form({"x": schema})) == {"x": kind}


def test_null_only_property_is_skipped():
    assert _kinds(_form({"x": {"type": "null"}, "y": {"type": "string"}})) == {"y": "TextInput"}


def test_bounds_become_slider_range():
    widget = _form({"x": {"type": "number", "minimum": -1, "maximum": 2}}, {"x": 0.5})._widgets["x"]
    assert (widget.start, widget.end, widget.value) == (-1.0, 2.0, 0.5)


def test_refs_resolve_against_definitions():
    definitions = {"Color": {"enum": ["red", "green"], "type": "string", "title": "Color"}}
    form = _form(
        {
            "color": {"$ref": "#/$defs/Color", "default": "red"},
            "maybe": {"anyOf": [{"$ref": "#/$defs/Color"}, {"type": "null"}], "title": "Maybe"},
        },
        {"color": "green"},
        definitions=definitions,
    )
    assert _kinds(form) == {"color": "Select", "maybe": "Select"}
    assert form._widgets["color"].options == ["green", "red"]
    assert form._widgets["color"].value == "green"
    maybe = form._widgets["maybe"]
    assert getattr(maybe, "label", maybe.name) == "Maybe"


def test_unresolvable_property_falls_back_to_literal_and_logs(caplog):
    with caplog.at_level(logging.WARNING, logger="panel.reactflow"):
        form = _form({"ok": {"type": "string"}, "bad": {"$ref": "#/$defs/Missing"}}, {"ok": "hi", "bad": 3})
    assert _kinds(form) == {"ok": "TextInput", "bad": "LiteralInput"}
    assert form._widgets["bad"].value == 3
    assert "'bad'" in caplog.text


def test_widget_overrides():
    instance = pn.widgets.TextInput()
    form = _form(
        {"a": {"type": "string"}, "b": {"type": "string"}, "c": {"type": "string"}, "d": {"type": "integer"}},
        {"a": "x", "d": 2},
        widgets={
            "a": {"placeholder": "type here"},
            "b": {"type": pn.widgets.TextAreaInput},
            "c": instance,
            "d": pn.widgets.IntSlider,
        },
    )
    assert form._widgets["a"].placeholder == "type here"
    assert form._widgets["a"].value == "x"
    assert isinstance(form._widgets["b"], pn.widgets.TextAreaInput)
    assert form._widgets["c"] is instance
    assert isinstance(form._widgets["d"], pn.widgets.IntSlider) and form._widgets["d"].value == 2


# Values


def test_values_are_coerced_into_widgets():
    form = _form(
        {"i": {"type": "integer"}, "s": {"type": "string"}, "r": {"type": "array", "prefixItems": [{"type": "number", "minimum": 0, "maximum": 5}] * 2}},
        {"i": 2.0, "s": None, "r": [1, 2]},
    )
    assert form._widgets["i"].value == 2 and type(form._widgets["i"].value) is int
    assert form._widgets["s"].value == ""
    assert form._widgets["r"].value == (1, 2)


def test_value_that_does_not_fit_is_shown_as_literal(caplog):
    with caplog.at_level(logging.WARNING, logger="panel.reactflow"):
        form = _form({"i": {"type": "integer"}}, {"i": 1.5})
    assert _kinds(form) == {"i": "LiteralInput"}
    assert form._widgets["i"].value == 1.5


def test_date_values_and_bounds_are_parsed():
    form = _form(
        {
            "d": {"type": "string", "format": "date", "formatMinimum": "2024-01-01", "formatMaximum": "2024-12-31"},
            "t": {"type": "string", "format": "date-time"},
        },
        {"d": "2024-03-04", "t": "2024-03-04T05:06:07Z"},
    )
    d, t = form._widgets["d"], form._widgets["t"]
    assert type(d).__name__ == "DatePicker"
    assert (d.start, d.end, d.value) == (dt.date(2024, 1, 1), dt.date(2024, 12, 31), dt.date(2024, 3, 4))
    assert t.value == dt.datetime(2024, 3, 4, 5, 6, 7)


def test_to_json_value():
    assert to_json_value(dt.date(2024, 1, 2)) == "2024-01-02"
    assert to_json_value((dt.date(2024, 1, 2), dt.date(2024, 1, 3))) == ["2024-01-02", "2024-01-03"]
    assert to_json_value((1, 2)) == (1, 2)


def test_object_update_ignores_keys_without_widgets():
    form = JSONSchema({"x": "a"}, schema={"x": {"type": "string"}})
    form.object = {"x": "b", "label": "extra", "view_idx": 0}
    assert form._widgets["x"].value == "b"


def test_nullable_property_shows_none():
    form = _form({"i": {"anyOf": [{"type": "integer"}, {"type": "null"}]}, "f": {"type": ["number", "null"]}}, {"i": None})
    assert type(form._widgets["i"]).__name__ == "IntInput"
    assert form._widgets["i"].value is None
    assert form._widgets["f"].value is None


def test_object_update_to_none_clears_nullable_widget():
    form = _form({"i": {"anyOf": [{"type": "integer"}, {"type": "null"}]}, "s": {"type": "string"}}, {"i": 4, "s": "x"})
    assert form._widgets["i"].value == 4
    form.object = {"i": None, "s": None}
    assert form._widgets["i"].value is None
    assert form._widgets["s"].value == "x"


def test_non_nullable_property_keeps_widget_default():
    form = _form({"i": {"type": "integer"}}, {"i": None})
    assert form._widgets["i"].value == 0


# Generated schemas


def test_param_class_schema_produces_widgets():
    class Params(param.Parameterized):
        number = param.Number(0.5, bounds=(0, 1))
        maybe_int = param.Integer(3, bounds=(0, 10), allow_None=True)
        choice = param.Selector(objects=["x", "y"])
        text = param.String(allow_None=True)
        flag = param.Boolean()
        span = param.Range((0, 1), bounds=(0, 5))
        tags = param.ListSelector(default=[], objects=["a", "b"])

    flow = ReactFlow(node_types={"p": NodeType(type="p", schema=Params)})
    flow.add_node({"id": "n", "type": "p", "position": {"x": 0, "y": 0}, "data": {"number": 0.25, "span": [1, 2]}})
    editor = _editor(flow, "n")
    assert _kinds(editor._form) == {
        "number": "FloatSlider",
        "maybe_int": "IntSlider",
        "choice": "Select",
        "text": "TextInput",
        "flag": "Checkbox",
        "span": "RangeSlider",
        "tags": "MultiChoice",
    }
    assert editor._form._widgets["span"].value == (1, 2)


def test_pydantic_model_schema_produces_widgets():
    pydantic = pytest.importorskip("pydantic")
    import enum
    from typing import Literal, Optional

    class Color(enum.Enum):
        red = "red"
        green = "green"

    class Sub(pydantic.BaseModel):
        z: int = 1

    class Model(pydantic.BaseModel):
        maybe: Optional[int] = None  # noqa: UP045
        color: Color = Color.red
        mode: Literal["x", "y"] = "x"
        ratio: float = pydantic.Field(0.5, ge=0, le=1)
        sub: Sub = Sub()
        day: dt.date = dt.date(2020, 1, 1)
        maybe_color: Optional[Color] = None  # noqa: UP045
        fixed: Literal["only"] = "only"

    editor = SchemaEditor({"color": "green", "day": "2020-01-01"}, Model.model_json_schema())
    assert _kinds(editor._form) == {
        "maybe": "IntInput",
        "color": "Select",
        "mode": "Select",
        "ratio": "FloatSlider",
        "sub": "LiteralInput",
        "day": "DatePicker",
        "maybe_color": "Select",
        "fixed": "Select",
    }
    assert editor._form._widgets["color"].value == "green"
    assert editor._form._widgets["maybe"].value is None


# Editors


SCHEMA = {
    "type": "object",
    "properties": {"n": {"type": "integer", "minimum": 0}, "d": {"type": "string", "format": "date"}, "s": {"type": "string"}},
}


def _schema_flow(**params):
    flow = ReactFlow(node_types={"t": NodeType(type="t", schema=SCHEMA)}, **params)
    msgs = []
    flow._send_msg = msgs.append
    flow.add_node({"id": "a", "type": "t", "position": {"x": 0, "y": 0}, "data": {"n": 1, "d": "2024-01-02", "s": "x"}})
    return flow, msgs


def test_schema_editor_writes_dates_as_iso_strings():
    flow, msgs = _schema_flow()
    editor = _editor(flow, "a")
    assert editor._form._widgets["d"].value == dt.date(2024, 1, 2)
    editor._form._widgets["d"].value = dt.date(2025, 3, 4)
    assert flow.nodes[0]["data"]["d"] == "2025-03-04"
    json.dumps(flow.nodes[0]["data"])
    assert msgs[-1] == {"type": "patch_node_data", "node_id": "a", "patch": {"d": "2025-03-04"}, "revision": flow._node_update_count}


def test_schema_editor_rejected_patch_reverts_widget(caplog):
    flow, _ = _schema_flow(validate_on_patch=True)
    widget = _editor(flow, "a")._form._widgets["n"]
    with caplog.at_level(logging.WARNING, logger="panel.reactflow"):
        widget.value = -3
    assert widget.value == 1
    assert flow.nodes[0]["data"]["n"] == 1
    assert "Rejected edit" in caplog.text


def test_schema_editor_follows_external_data_without_echo():
    patches = []
    editor = SchemaEditor({"n": 1, "s": "x"}, SCHEMA, on_patch=patches.append)
    editor._data = {"n": 7, "s": "y", "view_idx": 0}
    assert editor._form._widgets["n"].value == 7
    assert editor._form._widgets["s"].value == "y"
    assert patches == []
    editor._form._widgets["n"].value = 8
    assert patches == [{"n": 8}]
    assert editor._data["n"] == 8


def test_schema_editor_follows_external_reset_to_none():
    schema = {"type": "object", "properties": {"m": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}}
    patches = []
    editor = SchemaEditor({"m": 4}, schema, on_patch=patches.append)
    editor._data = {"m": None}
    assert editor._form._widgets["m"].value is None
    assert patches == []
    editor._form._widgets["m"].value = 2
    editor._form._widgets["m"].value = None
    assert patches == [{"m": 2}, {"m": None}]


def test_schema_editor_whole_form_failure_falls_back_to_json(monkeypatch, caplog):
    import panel_reactflow.schema as schema_module

    def broken(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(schema_module.JSONSchema, "_update_widgets_from_schema", broken)
    with caplog.at_level(logging.WARNING, logger="panel.reactflow"):
        editor = SchemaEditor({"n": 1}, SCHEMA, id="a", type="t")
    assert isinstance(editor._panel, pn.widgets.JSONEditor)
    assert "falling back to a JSON editor" in caplog.text


@pytest.mark.parametrize("editor_cls", [JsonEditor, SchemaEditor])
def test_json_editor_deleting_a_key_removes_it(editor_cls):
    flow = ReactFlow(default_node_editor=editor_cls)
    msgs = []
    flow._send_msg = msgs.append
    events = []
    flow.on("node_data_changed", events.append)
    flow.add_node({"id": "n", "position": {"x": 0, "y": 0}, "data": {"keep": 1, "drop": 2}})
    editor = _editor(flow, "n")
    json_widget = editor._editor if editor_cls is JsonEditor else editor._json_editor
    json_widget.value = {"keep": 1, "new": 3}
    assert flow.nodes[0]["data"] == {"keep": 1, "new": 3}
    assert msgs[-1] == {"type": "patch_node_data", "node_id": "n", "patch": {"new": 3}, "remove": ["drop"], "revision": flow._node_update_count}
    assert events[-1]["remove"] == ["drop"]


def test_json_editor_with_plain_on_patch_callback():
    patches = []
    editor = JsonEditor({"keep": 1, "drop": 2}, on_patch=patches.append)
    editor._editor.value = {"keep": 1}
    assert patches == [{}]
    assert editor._data == {"keep": 1}


def test_json_editor_rejects_non_object(caplog):
    patches = []
    editor = JsonEditor({"a": 1}, on_patch=patches.append)
    with caplog.at_level(logging.WARNING, logger="panel.reactflow"):
        editor._editor.value = [1, 2]
    assert patches == []
    assert editor._editor.value == {"a": 1}


def test_json_editor_follows_external_data_without_echo():
    patches = []
    editor = JsonEditor({"a": 1}, on_patch=patches.append)
    editor._data = {"a": 2}
    assert editor._editor.value == {"a": 2}
    assert patches == []


def test_patch_edge_data_remove():
    flow = ReactFlow(
        nodes=[{"id": "a", "position": {"x": 0, "y": 0}}, {"id": "b", "position": {"x": 1, "y": 0}}],
        edges=[{"id": "e", "source": "a", "target": "b", "data": {"w": 1, "x": 2}}],
    )
    msgs = []
    flow._send_msg = msgs.append
    flow.patch_edge_data("e", {"w": 5}, remove=["x"])
    assert flow.edges[0]["data"] == {"w": 5}
    assert msgs[0] == {"type": "patch_edge_data", "edge_id": "e", "patch": {"w": 5}, "remove": ["x"]}
