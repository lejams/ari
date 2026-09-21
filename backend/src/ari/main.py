"""ASGI entry point (`uvicorn ari.main:app`): builds the learner application from settings.

Importing `ari.api.app` alone never opens a database; only this module does.
"""

from ari.api.app import create_app

app = create_app()

__all__ = ["app"]
