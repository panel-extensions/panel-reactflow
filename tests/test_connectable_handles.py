"""Unit tests for NodeType connectable handle configuration."""

from panel_reactflow import NodeType


def test_node_type_default_connectable_flags():
    """Test that all connectable flags default to True."""
    node_type = NodeType(type="test", label="Test Node", inputs=["in"], outputs=["out"])

    result = node_type.to_dict()

    assert result["inputConnectable"] is True
    assert result["inputConnectableStart"] is True
    assert result["inputConnectableEnd"] is True
    assert result["outputConnectable"] is True
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is True


def test_node_type_custom_input_connectable():
    """Test setting custom input connectable flags."""
    node_type = NodeType(
        type="sink",
        label="Sink Node",
        inputs=["in"],
        outputs=["status"],
        input_connectable_start=False,
        input_connectable_end=True,
    )

    result = node_type.to_dict()

    assert result["inputConnectable"] is True
    assert result["inputConnectableStart"] is False
    assert result["inputConnectableEnd"] is True
    assert result["outputConnectable"] is True
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is True


def test_node_type_custom_output_connectable():
    """Test setting custom output connectable flags."""
    node_type = NodeType(
        type="source",
        label="Source Node",
        inputs=["config"],
        outputs=["out"],
        output_connectable_start=True,
        output_connectable_end=False,
    )

    result = node_type.to_dict()

    assert result["inputConnectable"] is True
    assert result["inputConnectableStart"] is True
    assert result["inputConnectableEnd"] is True
    assert result["outputConnectable"] is True
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is False


def test_node_type_fully_non_connectable():
    """Test disabling all connectable flags."""
    node_type = NodeType(
        type="readonly",
        label="Read-Only Node",
        inputs=["in"],
        outputs=["out"],
        input_connectable=False,
        output_connectable=False,
    )

    result = node_type.to_dict()

    assert result["inputConnectable"] is False
    assert result["outputConnectable"] is False


def test_node_type_sink_pattern():
    """Test a typical sink node pattern (accepts but doesn't emit)."""
    node_type = NodeType(
        type="sink",
        label="Sink",
        inputs=["in"],
        outputs=["status"],
        input_connectable_start=False,
        output_connectable_end=False,
    )

    result = node_type.to_dict()

    # Input can receive (end) but not emit (start)
    assert result["inputConnectableStart"] is False
    assert result["inputConnectableEnd"] is True

    # Output can emit (start) but not receive (end)
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is False


def test_node_type_source_pattern():
    """Test a typical source node pattern (emits but doesn't accept)."""
    node_type = NodeType(
        type="source",
        label="Source",
        inputs=["config"],
        outputs=["out"],
        input_connectable_end=False,
        output_connectable_start=True,
    )

    result = node_type.to_dict()

    # Input should not accept incoming edges
    assert result["inputConnectableEnd"] is False

    # Output should be able to start edges
    assert result["outputConnectableStart"] is True


def test_node_type_preserves_other_fields():
    """Test that connectable flags don't interfere with other fields."""
    node_type = NodeType(
        type="custom",
        label="Custom Node",
        schema={"type": "object", "properties": {"value": {"type": "number"}}},
        inputs=["in1", "in2"],
        outputs=["out1", "out2"],
        input_connectable_start=False,
        pane_policy="multiple",
    )

    result = node_type.to_dict()

    assert result["type"] == "custom"
    assert result["label"] == "Custom Node"
    assert result["schema"] is not None
    assert result["inputs"] == ["in1", "in2"]
    assert result["outputs"] == ["out1", "out2"]
    assert result["pane_policy"] == "multiple"
    assert result["inputConnectableStart"] is False


def test_node_type_boolean_coercion():
    """Test that boolean values are properly handled."""
    # Explicitly False
    node_type_false = NodeType(
        type="test",
        input_connectable=False,
    )
    assert node_type_false.to_dict()["inputConnectable"] is False

    # Explicitly True
    node_type_true = NodeType(
        type="test",
        input_connectable=True,
    )
    assert node_type_true.to_dict()["inputConnectable"] is True

    # Default (should be True)
    node_type_default = NodeType(type="test")
    assert node_type_default.to_dict()["inputConnectable"] is True


def test_node_type_data_source_pattern():
    """Test a data source node (outputs only, no inputs accepted)."""
    node_type = NodeType(
        type="data_source",
        label="Data Source",
        outputs=["data"],
        output_connectable_start=True,
        output_connectable_end=False,
    )

    result = node_type.to_dict()

    # Output can start edges but not end them
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is False


def test_node_type_data_sink_pattern():
    """Test a data sink node (inputs only, no outputs generated)."""
    node_type = NodeType(
        type="data_sink",
        label="Data Sink",
        inputs=["data"],
        input_connectable_start=False,
        input_connectable_end=True,
    )

    result = node_type.to_dict()

    # Input can end edges but not start them
    assert result["inputConnectableStart"] is False
    assert result["inputConnectableEnd"] is True


def test_node_type_monitor_pattern():
    """Test a monitor node (input only, status output but no incoming to output)."""
    node_type = NodeType(
        type="monitor",
        label="Monitor",
        inputs=["in"],
        outputs=["status"],
        input_connectable_start=False,
        output_connectable_end=False,
        output_connectable_start=True,
    )

    result = node_type.to_dict()

    # Input cannot start edges
    assert result["inputConnectableStart"] is False
    assert result["inputConnectableEnd"] is True

    # Output can start edges but not end them
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is False


def test_node_type_mixed_connectable_settings():
    """Test a node with mixed connectable settings on both sides."""
    node_type = NodeType(
        type="mixed",
        label="Mixed",
        inputs=["in1", "in2"],
        outputs=["out1", "out2"],
        input_connectable=True,
        input_connectable_start=True,
        input_connectable_end=False,
        output_connectable=True,
        output_connectable_start=False,
        output_connectable_end=True,
    )

    result = node_type.to_dict()

    # Input can start but not end
    assert result["inputConnectable"] is True
    assert result["inputConnectableStart"] is True
    assert result["inputConnectableEnd"] is False

    # Output can end but not start
    assert result["outputConnectable"] is True
    assert result["outputConnectableStart"] is False
    assert result["outputConnectableEnd"] is True


def test_node_type_to_dict_camelcase_conversion():
    """Test that to_dict properly converts snake_case to camelCase."""
    node_type = NodeType(
        type="test",
        input_connectable=False,
        input_connectable_start=False,
        input_connectable_end=False,
        output_connectable=False,
        output_connectable_start=False,
        output_connectable_end=False,
    )

    result = node_type.to_dict()

    # Verify all keys are in camelCase
    assert "inputConnectable" in result
    assert "inputConnectableStart" in result
    assert "inputConnectableEnd" in result
    assert "outputConnectable" in result
    assert "outputConnectableStart" in result
    assert "outputConnectableEnd" in result

    # Verify snake_case keys don't exist
    assert "input_connectable" not in result
    assert "input_connectable_start" not in result
    assert "output_connectable" not in result


def test_node_type_no_handles():
    """Test node type with no inputs or outputs still has connectable flags."""
    node_type = NodeType(
        type="standalone",
        label="Standalone",
    )

    result = node_type.to_dict()

    # Even without handles, connectable flags should be present and default to True
    assert result["inputConnectable"] is True
    assert result["inputConnectableStart"] is True
    assert result["inputConnectableEnd"] is True
    assert result["outputConnectable"] is True
    assert result["outputConnectableStart"] is True
    assert result["outputConnectableEnd"] is True
