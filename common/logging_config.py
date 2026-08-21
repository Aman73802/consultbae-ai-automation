"""One-line logging setup, called once by each entrypoint (the Streamlit
app, the CLI merge pipeline, the Flask API, the reset script). Replaces
bare print()/silently-swallowed exceptions with real, leveled, module-
attributed log records, so a failure has a trace somewhere other than
whatever terminal happened to be open when it occurred.

Deliberately minimal: stdlib logging.basicConfig, no handlers/rotation/
aggregation setup -- this is a single-process local tool, not a fleet.
"""
import logging
import os


def setup_logging():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
