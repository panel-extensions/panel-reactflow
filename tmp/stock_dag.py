"""Example 3: Stock Analysis DAG — fan-out and fan-in with a diamond topology."""

import hvplot.pandas  # noqa: F401
import numpy as np
import pandas as pd
import panel as pn
import param

from panel_reactflow import Pipeline

pn.extension("jsoneditor", "tabulator")


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


class ChartsNode(param.Parameterized):
    ma_data = param.DataFrame()
    rsi_data = param.DataFrame()

    @param.output("price_plot", "rsi_plot")
    @param.depends("ma_data", "rsi_data")
    def plot(self):
        if self.ma_data is None or self.rsi_data is None:
            return pn.pane.Markdown("*Waiting for data from both branches...*")
        price_plot = self.ma_data.hvplot.line(
            y=["price", "ma"],
            ylabel="Price",
            title="Price & Moving Average",
            legend="top_left",
            width=600,
            height=400,
        )
        rsi_plot = self.rsi_data.hvplot.line(
            y="rsi",
            ylabel="RSI",
            title="RSI",
            color="orange",
            width=600,
            height=400,
        )
        return price_plot, rsi_plot


Pipeline(
    stages=[
        ("Stock Data", StockData),
        ("MA", MANode),
        ("RSI", RSINode),
        ("Charts", ChartsNode),
    ],
    graph={"Stock Data": ("MA", "RSI"), "MA": "Charts", "RSI": "Charts"},
    kwargs={"min_height": 600},
).servable()
