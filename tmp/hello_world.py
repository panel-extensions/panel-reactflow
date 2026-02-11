"""Example 1: Text Processing Pipeline — single node with auto-inputs."""

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
