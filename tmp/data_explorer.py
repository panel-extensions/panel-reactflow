"""Example 2: Data Explorer Pipeline — DataFrames flowing between nodes with an hvPlot scatter chart."""

import hvplot.pandas  # noqa: F401
import numpy as np
import pandas as pd
import panel as pn
import param

from panel_reactflow import Pipeline

pn.extension("jsoneditor")

# Generate sample datasets (no external dependencies needed)
DATASETS = {
    "iris": pd.DataFrame(
        {
            "sepal_length": np.random.normal(5.8, 0.8, 150),
            "sepal_width": np.random.normal(3.0, 0.4, 150),
            "petal_length": np.random.normal(3.7, 1.8, 150),
            "petal_width": np.random.normal(1.2, 0.8, 150),
        }
    ),
    "random": pd.DataFrame(
        {
            "x": np.random.randn(200),
            "y": np.random.randn(200),
            "size": np.random.uniform(1, 10, 200),
            "value": np.random.uniform(0, 100, 200),
        }
    ),
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
