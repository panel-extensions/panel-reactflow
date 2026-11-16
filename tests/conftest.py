"""Holds fixtures and configuration for the test suite."""

import logging

import pytest
from panel.config import config
from panel.tests.conftest import comm, document  # noqa: F401

optional_markers = {
    "ui": {
        "help": "Runs UI related tests",
        "marker-descr": "UI test marker",
        "skip-reason": "Test only runs with the --ui option.",
    },
}


@pytest.fixture(autouse=True)
def reset_design():
    """Isolate tests from a global design or theme set by an earlier test or import."""
    design, theme = config.design, config.theme
    config.design = None
    yield
    config.design = design
    config.theme = theme


@pytest.fixture(autouse=True)
def propagate_panel_logs():
    """Let caplog see ``panel.reactflow`` records.

    Some Panel versions configure the ``panel`` logger with
    ``propagate=False``, which hides its records from the root handler
    that caplog installs.
    """
    logger = logging.getLogger("panel")
    propagate = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = propagate


@pytest.fixture
def flow_model(document, comm):  # noqa: F811
    """Return the ReactFlow model, which a design may nest inside a wrapper root."""

    def get_model(flow):
        root = flow.get_root(document, comm=comm)
        return flow._models[root.ref["id"]][0]

    return get_model


def pytest_addoption(parser):
    """Add extra command line options."""
    for marker, info in optional_markers.items():
        parser.addoption(f"--{marker}", action="store_true", default=False, help=info["help"])
    parser.addoption("--repeat", action="store", help="Number of times to repeat each test")


def pytest_configure(config):
    """Add extra markers."""
    for marker, info in optional_markers.items():
        config.addinivalue_line("markers", "{}: {}".format(marker, info["marker-descr"]))

    config.addinivalue_line("markers", "internet: mark test as requiring an internet connection")


def pytest_collection_modifyitems(config, items):
    skipped, selected = [], []
    markers = [m for m in optional_markers if config.getoption(f"--{m}")]
    empty = not markers
    for item in items:
        if empty and any(m in item.keywords for m in optional_markers):
            skipped.append(item)
        elif empty:
            selected.append(item)
        elif not empty and any(m in item.keywords for m in markers):
            selected.append(item)
        else:
            skipped.append(item)

    config.hook.pytest_deselected(items=skipped)
    items[:] = selected
