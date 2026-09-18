"""ASGI entry point of the back-office (`uvicorn ari.backoffice:app --port 8100`)."""

from ari.backoffice_api.app import create_backoffice_app

app = create_backoffice_app()

__all__ = ["app"]
