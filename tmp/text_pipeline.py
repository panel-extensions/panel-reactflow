"""Example 1: Text Processing Pipeline — two stages with auto-inputs."""

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
