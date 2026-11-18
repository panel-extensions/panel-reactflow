# Panel Pipelines: Visual Data Flow with Parameterized Nodes

*A next-generation replacement for `panel.pipeline.Pipeline`, built on ReactFlow.*

## Why Replace Panel Pipeline?

[Panel Pipeline](https://panel.holoviz.org/how_to/pipeline/simple_pipeline.html)
was a pioneering attempt at visual workflows, but its limitations are well-known:
linear-first design, no interactive canvas (no drag/zoom/pan), navigation-centric
rather than data-flow-centric, no embedded previews inside nodes, and
[long-standing unresolved bugs](https://github.com/holoviz/panel/issues?q=is%3Aissue+pipeline+is%3Aopen).

Tools like [Daggr](https://github.com/gradio-app/daggr),
[ComfyUI](https://github.com/comfyanonymous/ComfyUI),
[LangFlow](https://github.com/langflow-ai/langflow), and
[n8n](https://github.com/n8n-io/n8n) have proven that visual node editors
are the natural interface for composable workflows. Panel deserves the same.

---

## Core Concept: A Parameterized Class IS the Node

A pipeline stage is any `param.Parameterized` subclass. No special base class needed.

| Parameterized Concept | Pipeline Role |
|---|---|
| `param.Parameter` declarations | **Inputs** (data the node consumes) |
| `@param.output` decorated methods | **Outputs** (data the node produces) |
| `@param.depends` view methods | **Display** (live preview inside the node) |
| Parameter types & constraints | Auto-wiring and auto-input widgets |

```python
import param

class TransformNode(param.Parameterized):
    # Inputs
    text = param.String(default="hello world")
    mode = param.Selector(default="upper", objects=["upper", "lower", "title", "swapcase"])

    # Output: Should we continue to use param.output. Or just param.depends methods?
    @param.output(param.String)
    @param.depends("text", "mode")
    def result(self):
        if not self.text:
            return ""
        return getattr(self.text, self.mode)()

    # Display (view method — rendered inside the node)
    # Should param.depends methods be recognized as outputs?
    @param.depends("text", "mode")
    def preview(self):
        result = getattr(self.text, self.mode)()
        return pn.pane.Markdown(f"**{result}**")
```

The `Pipeline` class introspects this and automatically:

1. Creates auto-input widget nodes for `text` and `mode` (unconnected params)
2. Wires the `result` output to any downstream stage with a matching `result` parameter
3. Renders the `preview()` view method inside the node body
4. Sets up reactive updates so changes propagate through the graph

---

## Pipeline API

```python
from panel_reactflow import Pipeline

Pipeline(
    stages=[("Name", ClassOrInstance), ...],
    graph=None,           # None = auto-infer edges; dict = explicit topology
    layout_spacing=(350, 150),  # (horizontal, vertical) pixels between nodes
    auto_inputs=True,     # auto-generate widget nodes for unconnected params
    kwargs={},            # extra kwargs forwarded to ReactFlow
).servable()
```

Question: Should this be a `Pipeline` class or just a `def create_pipeline(...)->ReactFlow` function?

### Parameters

- **`stages`** — List of `(name, class_or_instance)` tuples. Classes are instantiated automatically.
- **`graph`** — Explicit topology: `{source_name: target_name | (t1, t2, ...)}`. When `None`, edges are inferred by matching `@param.output` names to downstream parameter names.
- **`layout_spacing`** — `(horizontal, vertical)` spacing in pixels.
- **`auto_inputs`** — When `True`, unconnected parameters get auto-generated widget nodes with an "INPUT" pill badge.
- **`kwargs`** — Extra keyword arguments forwarded to `ReactFlow` (e.g., `min_height`, `show_minimap`).

### View resolution

Pipeline resolves what to display inside each stage node:

1. **View methods** (preferred) — Public `@param.depends` methods that are *not* `@param.output` methods. If a stage has `preview()` or `view()` decorated with `@param.depends`, it's rendered in the node.
2. **Output fallback** — If no view methods exist, `@param.output` methods are rendered. A single output is shown directly; multiple outputs are displayed in a `pn.Accordion` with named sections (all expanded).

### Node styling

Pipeline nodes are visually distinguished:

- **Auto-input nodes** — Indigo border + gradient + "INPUT" pill badge (CSS class `rf-auto-input`)
- **Stage nodes** — Emerald border + gradient + "OUTPUT" pill badge (CSS class `rf-stage`)

Question: What would the right styling here be?

---

## Examples

### 1. Hello World — Single Stage

*`tmp/hello_world.py` — The simplest pipeline: one stage, auto-input widgets.*

```python
import panel as pn
import param

from panel_reactflow import Pipeline

pn.extension("jsoneditor")


class StrTransformNode(param.Parameterized):
    text = param.String("Hello World")
    mode = param.Selector(default="upper", objects=["upper", "lower", "title", "swapcase"])

    @param.output(param.String)
    @param.depends("text", "mode")
    def result(self):
        if not self.text:
            return ""
        return getattr(self.text, self.mode)()


Pipeline(stages=[("Transform", StrTransformNode)]).servable()
```

`text` and `mode` have no upstream connection, so Pipeline auto-generates input widget nodes for them. The `result()` output is rendered as the node's fallback view.

### 2. Text Pipeline — Two Stages with View Methods

*`tmp/text_pipeline.py` — Auto-inferred edges, view methods in both stages.*

```python
import panel as pn
import param

from panel_reactflow import Pipeline

pn.extension("jsoneditor")


class TransformNode(param.Parameterized):
    text = param.String(default="hello world")
    mode = param.Selector(default="upper", objects=["upper", "lower", "title", "swapcase"])

    @param.output(param.String)
    @param.depends("text", "mode")
    def result(self):
        if not self.text:
            return ""
        return getattr(self.text, self.mode)()

    @param.depends("text", "mode")
    def preview(self):
        if not self.text:
            return pn.pane.Markdown("*No input yet*")
        result = getattr(self.text, self.mode)()
        return pn.pane.Markdown(f"**{result}**")


class DisplayNode(param.Parameterized):
    result = param.String()

    @param.depends("result")
    def view(self):
        return pn.pane.Alert(self.result or "Waiting...", alert_type="success")


Pipeline(
    stages=[("Transform", TransformNode), ("Display", DisplayNode)],
).servable()
```

Pipeline auto-infers the edge `Transform.result -> Display.result` by name matching. Transform shows `preview()`, Display shows `view()`. Auto-input widgets are created for `text` and `mode`.

### 3. Data Explorer — DataFrames with Dynamic Selectors

*`tmp/data_explorer.py` — DataFrames flowing between nodes, dynamic column selectors, hvPlot chart.*

```python
import hvplot.pandas  # noqa: F401
import numpy as np
import pandas as pd
import panel as pn
import param

from panel_reactflow import Pipeline

pn.extension("jsoneditor")

DATASETS = {
    "iris": pd.DataFrame({
        "sepal_length": np.random.normal(5.8, 0.8, 150),
        "sepal_width": np.random.normal(3.0, 0.4, 150),
        "petal_length": np.random.normal(3.7, 1.8, 150),
        "petal_width": np.random.normal(1.2, 0.8, 150),
    }),
    "random": pd.DataFrame({
        "x": np.random.randn(200),
        "y": np.random.randn(200),
        "size": np.random.uniform(1, 10, 200),
        "value": np.random.uniform(0, 100, 200),
    }),
}


class DataLoaderNode(param.Parameterized):
    dataset = param.Selector(default="iris", objects=list(DATASETS.keys()))

    @param.output(param.DataFrame)
    @param.depends("dataset")
    def data(self):
        return DATASETS[self.dataset]

    @param.depends("dataset")
    def table(self):
        return pn.pane.DataFrame(DATASETS[self.dataset], max_height=300)


class ChartNode(param.Parameterized):
    data = param.DataFrame()
    x_col = param.Selector(default="", objects=[""])
    y_col = param.Selector(default="", objects=[""])

    def __init__(self, **params):
        super().__init__(**params)
        self._update_col_options()

    @param.depends("data", watch=True)
    def _update_col_options(self):
        if self.data is not None and len(self.data.columns):
            cols = list(self.data.columns)
            self.param.x_col.objects = cols
            self.param.y_col.objects = cols
            if self.x_col not in cols:
                self.x_col = cols[0]
            if self.y_col not in cols:
                self.y_col = cols[1] if len(cols) > 1 else cols[0]
        else:
            self.param.x_col.objects = [""]
            self.param.y_col.objects = [""]
            self.x_col = ""
            self.y_col = ""

    @param.output()
    @param.depends("data", "x_col", "y_col")
    def plot(self):
        if self.data is None or not self.x_col or not self.y_col:
            return pn.pane.Markdown("*Waiting for data...*")
        return self.data.hvplot.scatter(x=self.x_col, y=self.y_col, height=500, width=500)


Pipeline(
    stages=[("Data", DataLoaderNode), ("Chart", ChartNode)],
).servable()
```

`DataLoaderNode.data` auto-connects to `ChartNode.data`. ChartNode dynamically updates its `x_col`/`y_col` selector options when data arrives. `table()` is DataLoader's view method; `plot()` is Chart's output fallback.

### 4. Stock Analysis DAG — Diamond Topology

*`tmp/stock_dag.py` — Fan-out and fan-in with explicit graph.*

```python
import hvplot.pandas  # noqa: F401
import numpy as np
import pandas as pd
import panel as pn
import param

from panel_reactflow import Pipeline

pn.extension("jsoneditor")


class StockData(param.Parameterized):
    symbol = param.String(default="AAPL")
    days = param.Integer(default=252, bounds=(30, 1000))

    @param.output(param.DataFrame)
    @param.depends("symbol", "days")
    def prices(self):
        np.random.seed(hash(self.symbol) % 2**32)
        dates = pd.date_range(end=pd.Timestamp.now(), periods=self.days)
        price = 100 + np.cumsum(np.random.randn(self.days) * 1.5)
        return pd.DataFrame({"date": dates, "price": price}).set_index("date")


class MANode(param.Parameterized):
    prices = param.DataFrame()
    window = param.Integer(default=20, bounds=(5, 100))

    @param.output(param.DataFrame)
    @param.depends("prices", "window")
    def ma_data(self):
        if self.prices is None:
            return None
        df = self.prices.copy()
        df["ma"] = df["price"].rolling(self.window).mean()
        return df


class RSINode(param.Parameterized):
    prices = param.DataFrame()
    period = param.Integer(default=14, bounds=(2, 50))

    @param.output(param.DataFrame)
    @param.depends("prices", "period")
    def rsi_data(self):
        if self.prices is None:
            return None
        delta = self.prices["price"].diff()
        gain = delta.clip(lower=0).rolling(self.period).mean()
        loss = (-delta.clip(upper=0)).rolling(self.period).mean()
        rs = gain / loss
        df = self.prices.copy()
        df["rsi"] = 100 - (100 / (1 + rs))
        return df


class ChartNode(param.Parameterized):
    ma_data = param.DataFrame()
    rsi_data = param.DataFrame()

    @param.output()
    @param.depends("ma_data", "rsi_data")
    def plot(self):
        if self.ma_data is None or self.rsi_data is None:
            return pn.pane.Markdown("*Waiting for data from both branches...*")
        price_plot = self.ma_data.hvplot.line(
            y=["price", "ma"], ylabel="Price", title="Price & Moving Average",
            legend="top_left", height=200,
        )
        rsi_plot = self.rsi_data.hvplot.line(
            y="rsi", ylabel="RSI", title="RSI", color="orange", height=150,
        )
        return pn.Column(price_plot, rsi_plot, sizing_mode="stretch_width")


Pipeline(
    stages=[
        ("Stock Data", StockData),
        ("MA", MANode),
        ("RSI", RSINode),
        ("Chart", ChartNode),
    ],
    graph={"Stock Data": ("MA", "RSI"), "MA": "Chart", "RSI": "Chart"},
    kwargs={"min_height": 600},
).servable()
```

The explicit `graph` defines the diamond: Stock Data fans out to MA and RSI, which fan in to Chart. Auto-inputs are created for `symbol`, `days`, `window`, and `period`.

### 5. Multi-Output — One Method, Three Outputs

*`tmp/multi_output.py` — A single `@param.output` method producing three named outputs, each flowing to its own downstream node.*

```python
import panel as pn
import param

from panel_reactflow import Pipeline

pn.extension("jsoneditor")


class Source(param.Parameterized):
    text = param.String(default="Hello World")

    @param.output(upper=param.String(), lower=param.String(), length=param.Integer())
    @param.depends("text")
    def split(self):
        return self.text.upper(), self.text.lower(), len(self.text)


class UpperDisplay(param.Parameterized):
    upper = param.String()

    @param.depends("upper")
    def view(self):
        return pn.pane.Markdown(f"**{self.upper}**")


class LowerDisplay(param.Parameterized):
    lower = param.String()

    @param.depends("lower")
    def view(self):
        return pn.pane.Markdown(f"*{self.lower}*")


class LengthDisplay(param.Parameterized):
    length = param.Integer()

    @param.depends("length")
    def view(self):
        return pn.pane.Alert(f"Length: {self.length}", alert_type="info")


Pipeline(
    stages=[
        ("Source", Source),
        ("Upper", UpperDisplay),
        ("Lower", LowerDisplay),
        ("Length", LengthDisplay),
    ],
).servable()
```

`Source.split()` returns a tuple of 3 values. Pipeline auto-infers edges: `upper -> Upper.upper`, `lower -> Lower.lower`, `length -> Length.length`. The Source node displays all 3 outputs in an Accordion (expanded). Each downstream node shows its view method.

---

## Implemented Features

- [x] `Pipeline` class (`src/panel_reactflow/pipeline.py`)
- [x] Auto-inferred edges by matching `@param.output` names to downstream parameter names
- [x] Explicit `graph` dict for non-linear topologies (fan-out, fan-in, diamond)
- [x] Auto-input widget nodes for unconnected parameters
- [x] Reactive wiring via `param.watch` (upstream output changes propagate downstream)
- [x] View method resolution (public `@param.depends` methods rendered in nodes)
- [x] Output fallback rendering (single output direct, multi-output in Accordion)
- [x] Multi-output support (tuple-indexed extraction from `@param.output(a=..., b=..., c=...)`)
- [x] Topological layout algorithm (BFS depth assignment, vertical stacking for fan-out)
- [x] Configurable layout spacing
- [x] Visual pill badges: "INPUT" (indigo) on auto-input nodes, "OUTPUT" (emerald) on stage nodes
- [x] Forward `kwargs` to ReactFlow (min_height, show_minimap, etc.)
- [x] Accepts both classes and instances as stages
- [x] Unit tests (`tests/test_pipeline.py` — 34 tests)

---

## Open Issues

- https://github.com/panel-extensions/panel-reactflow/issues/27
- https://github.com/panel-extensions/panel-reactflow/issues/26
- https://github.com/panel-extensions/panel-reactflow/issues/25
- https://github.com/panel-extensions/panel-reactflow/issues/24
- https://github.com/panel-extensions/panel-reactflow/issues/23
- https://github.com/panel-extensions/panel-reactflow/issues/22
- https://github.com/panel-extensions/panel-reactflow/issues/21
- https://github.com/panel-extensions/panel-reactflow/issues/20
- https://github.com/panel-extensions/panel-reactflow/issues/19
- https://github.com/panel-extensions/panel-reactflow/issues/18
- https://github.com/panel-extensions/panel-reactflow/issues/17
- https://github.com/panel-extensions/panel-reactflow/issues/16
- https://github.com/panel-extensions/panel-reactflow/issues/15
- https://github.com/panel-extensions/panel-reactflow/issues/14
- https://github.com/panel-extensions/panel-reactflow/issues/13
- https://github.com/panel-extensions/panel-reactflow/issues/12

## Requirements

### Customization

- **Custom input widgets** &mdash; Developers must be able to customize auto-input widgets in the same way `pn.Param` allows (e.g., specifying widget types, formatting, bounds overrides per parameter).
- **Custom output views** &mdash; Developers must be able to customize how individual outputs are rendered in stage nodes, by providing panes, custom functions, or `Viewer` subclasses (analogous to `pn.Param`'s `widgets` dict).
- **Customizable node styling** &mdash; The default input/output pill badges and colors should look polished out of the box, but developers must be able to override them (custom CSS classes, colors, or disable badges entirely).

### Execution control

- **Output caching** &mdash; Expensive output computations should be cacheable. The recommended pattern is `@pn.cache` on the output method; this should be documented with examples.
- **Manual vs. automatic execution** &mdash; Developers must be able to choose between automatic reactive updates (current default: outputs recompute on any input change) and manual trigger mode (outputs recompute only on button click).
- **Startup computation** &mdash; Developers must be able to control whether outputs are computed on initialization (similar to `on_init=True` in `param.depends`).
- **Background execution** &mdash; Long-running output computations should run in the background (e.g., via `pn.state.execute` or threading) to keep the UI responsive. When possible, independent branches should compute in parallel.

### Visual feedback

- **Stale/invalidated state** &mdash; When an input changes but the downstream output has not yet recomputed, the affected nodes should be visually marked as stale (e.g., dimmed border, "stale" badge).
- **Computing indicator** &mdash; While an output is being recomputed, the node should show a spinner or loading overlay so the user knows work is in progress.

### Documentation and examples

- **Convert legacy Panel Pipeline examples** &mdash; All examples from the [Panel Pipeline How-To guides](https://panel.holoviz.org/how_to/pipeline/index.html) should be ported to this API and tested.
- **Convert Gradio Daggr examples** &mdash; All examples from [Daggr](https://github.com/gradio-app/daggr) should be ported to this API and tested.
- **ML and GenAI examples** &mdash; Create showcase examples for machine learning workflows (training pipelines, inference chains) and generative AI (LLM chains, RAG pipelines).
- **Function-to-node guide** &mdash; Document how to wrap an existing function as a Pipeline stage with minimal boilerplate (input params from function signature, output from return value).
- **Testing guide** &mdash; Document how to unit-test individual stages in isolation and how to integration-test a full pipeline.

### API design

- **Helper node classes** &mdash; Evaluate whether to provide ready-made base classes like `FnNode` (wraps a plain function), `PanelNode` (wraps a Panel viewable), and `InferenceNode` (wraps an ML model), similar to [Daggr's node types](https://github.com/gradio-app/daggr).
- **LLM-friendly error messages** &mdash; All Pipeline errors (missing outputs, unresolved edges, type mismatches) should produce clear, actionable messages that an LLM coding assistant can interpret and fix without ambiguity.
- **Resolve open issues above** &mdash; Content-aware layout, Viewer resolution, node overflow, and type-based wiring should all be addressed.

---

## References

### Panel Pipeline

- [Simple Pipeline How-To](https://panel.holoviz.org/how_to/pipeline/simple_pipeline.html)
- [Complex (Non-Linear) Pipeline How-To](https://panel.holoviz.org/how_to/pipeline/complex_pipeline.html)

### param.output

- [Outputs User Guide](https://param.holoviz.org/user_guide/Outputs.html)

### Prior Art

- [Daggr](https://github.com/gradio-app/daggr) &mdash; Visual DAG builder for Gradio apps
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) &mdash; Node-based Stable Diffusion workflow editor
- [LangFlow](https://github.com/langflow-ai/langflow) &mdash; Visual LLM agent workflow builder
- [Flowise](https://github.com/FlowiseAI/Flowise) &mdash; Drag-and-drop LLM flow builder
- [n8n](https://github.com/n8n-io/n8n) &mdash; Workflow automation platform

### panel-reactflow

- [Repository](https://github.com/panel-extensions/panel-reactflow)
- [Documentation](https://panel-extensions.github.io/panel-reactflow/)
- [React Flow (xyflow)](https://xyflow.com/)
