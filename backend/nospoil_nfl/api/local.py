"""Local development entry point: ``uvicorn nospoil_nfl.api.local:app``."""

from .http import create_app

app = create_app()
