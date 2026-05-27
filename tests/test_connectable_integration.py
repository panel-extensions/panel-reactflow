"""Integration tests for connectable handles feature.

Tests the full stack from Python NodeType definition through
to the expected JavaScript Handle component props.
"""

import pytest

from panel_reactflow import NodeType


def test_connectable_flags_serialization():
    """Test that all connectable flags are properly serialized to dict."""
    node_type = NodeType(
        type="test",
        label="Test",
        inputs=["in"],
        outputs=["out"],
        input_connectable=False,
        input_connectable_start=True,
        input_connectable_end=False,
        output_connectable=True,
        output_connectable_start=False,
        output_connectable_end=True,
    )

    result = node_type.to_dict()

    # Verify all flags are serialized
    assert "inputConnectable" in result
    assert "inputConnectableStart" in result
    assert "inputConnectableEnd" in result
    assert "outputConnectable" in result
    assert "outputConnectableStart" in result
    assert "outputConnectableEnd" in result

    # Verify correct values
    assert result["inputConnectable"] is False
    assert result["inputConnectableStart"] is True
    assert result["inputConnectableEnd"] is False
    assert result["outputConnectable"] is True
    assert result["outputConnectableStart"] is False
    assert result["outputConnectableEnd"] is True


def test_connectable_flags_are_booleans():
    """Test that all connectable flags are boolean type."""
    node_type = NodeType(type="test")
    result = node_type.to_dict()

    assert isinstance(result["inputConnectable"], bool)
    assert isinstance(result["inputConnectableStart"], bool)
    assert isinstance(result["inputConnectableEnd"], bool)
    assert isinstance(result["outputConnectable"], bool)
    assert isinstance(result["outputConnectableStart"], bool)
    assert isinstance(result["outputConnectableEnd"], bool)


def test_data_pipeline_node_types():
    """Test a complete data pipeline with various node types."""
    # Source node
    source = NodeType(
        type="source",
        label="Data Source",
        outputs=["data"],
        output_connectable_start=True,
        output_connectable_end=False,
    )

    # Transform node (default - all connectable)
    transform = NodeType(
        type="transform",
        label="Transform",
        inputs=["in"],
        outputs=["out"],
    )

    # Sink node
    sink = NodeType(
        type="sink",
        label="Data Sink",
        inputs=["data"],
        input_connectable_start=False,
        input_connectable_end=True,
    )

    source_dict = source.to_dict()
    transform_dict = transform.to_dict()
    sink_dict = sink.to_dict()

    # Source: can output but not accept input to output
    assert source_dict["outputConnectableStart"] is True
    assert source_dict["outputConnectableEnd"] is False

    # Transform: all flags should be True (default)
    assert all(
        transform_dict[k]
        for k in ["inputConnectable", "inputConnectableStart", "inputConnectableEnd", "outputConnectable", "outputConnectableStart", "outputConnectableEnd"]
    )

    # Sink: can accept input but not output from input
    assert sink_dict["inputConnectableStart"] is False
    assert sink_dict["inputConnectableEnd"] is True


def test_connectable_flags_independent():
    """Test that setting one connectable flag doesn't affect others."""
    node_type = NodeType(
        type="test",
        input_connectable_start=False,
    )

    result = node_type.to_dict()

    # Only the specified flag should be False, others default to True
    assert result["inputConnectableStart"] is False
    assert result["inputConnectable"] is True
    assert result["inputConnectableEnd"] is True
    assert result["outputConnectable"] is True
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is True


def test_connectable_with_multiple_handles():
    """Test connectable flags work with multiple input/output handles."""
    node_type = NodeType(
        type="multi",
        label="Multi-IO Node",
        inputs=["in1", "in2", "in3"],
        outputs=["out1", "out2"],
        input_connectable_start=False,
        output_connectable_end=False,
    )

    result = node_type.to_dict()

    # Verify handles are present
    assert result["inputs"] == ["in1", "in2", "in3"]
    assert result["outputs"] == ["out1", "out2"]

    # Verify connectable flags apply to all handles
    assert result["inputConnectableStart"] is False
    assert result["outputConnectableEnd"] is False


def test_connectable_flags_with_schema():
    """Test that connectable flags work alongside schema definitions."""
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "value": {"type": "number"},
        },
    }

    node_type = NodeType(
        type="config",
        label="Config Node",
        schema=schema,
        inputs=["trigger"],
        outputs=["config"],
        input_connectable_start=False,
    )

    result = node_type.to_dict()

    # Schema should be preserved
    assert result["schema"] is not None
    assert "properties" in result["schema"]

    # Connectable flags should be present
    assert result["inputConnectableStart"] is False


def test_all_node_patterns():
    """Test all common node patterns in a single test."""
    patterns = {
        "source": {
            "outputs": ["data"],
            "output_connectable_start": True,
            "output_connectable_end": False,
        },
        "sink": {
            "inputs": ["data"],
            "input_connectable_start": False,
            "input_connectable_end": True,
        },
        "transform": {
            "inputs": ["in"],
            "outputs": ["out"],
            # All default to True
        },
        "monitor": {
            "inputs": ["in"],
            "outputs": ["status"],
            "input_connectable_start": False,
            "output_connectable_end": False,
        },
        "readonly": {
            "inputs": ["in"],
            "outputs": ["out"],
            "input_connectable": False,
            "output_connectable": False,
        },
    }

    for pattern_name, kwargs in patterns.items():
        node_type = NodeType(type=pattern_name, label=pattern_name.title(), **kwargs)
        result = node_type.to_dict()

        # All patterns should serialize successfully
        assert result["type"] == pattern_name
        assert result["label"] == pattern_name.title()

        # All should have connectable flags
        assert "inputConnectable" in result
        assert "outputConnectable" in result


@pytest.mark.parametrize(
    "flag_name,camel_name",
    [
        ("input_connectable", "inputConnectable"),
        ("input_connectable_start", "inputConnectableStart"),
        ("input_connectable_end", "inputConnectableEnd"),
        ("output_connectable", "outputConnectable"),
        ("output_connectable_start", "outputConnectableStart"),
        ("output_connectable_end", "outputConnectableEnd"),
    ],
)
def test_snake_to_camel_conversion(flag_name, camel_name):
    """Test that each snake_case flag is converted to camelCase."""
    node_type = NodeType(type="test", **{flag_name: False})
    result = node_type.to_dict()

    assert camel_name in result
    assert result[camel_name] is False
    assert flag_name not in result  # snake_case should not be in output


def test_backwards_compatibility():
    """Test that nodes without connectable flags still work."""
    # Old-style node definition without connectable flags
    node_type = NodeType(
        type="legacy",
        label="Legacy Node",
        inputs=["in"],
        outputs=["out"],
    )

    result = node_type.to_dict()

    # Should default to fully connectable
    assert result["inputConnectable"] is True
    assert result["inputConnectableStart"] is True
    assert result["inputConnectableEnd"] is True
    assert result["outputConnectable"] is True
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is True
