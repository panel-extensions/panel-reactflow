"""Example: Multi-Output Pipeline — one method producing three outputs, each in its own node."""

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
