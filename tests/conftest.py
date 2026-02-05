"""Holds fixtures and configuration for the test suite."""

optional_markers = {
    "ui": {
        "help": "Runs UI related tests",
        "marker-descr": "UI test marker",
        "skip-reason": "Test only runs with the --ui option.",
    },
}


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
