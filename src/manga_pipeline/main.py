"""Application entry point.

Initializes the Typer app and delegates to cli module.
"""

from manga_pipeline.cli import app

if __name__ == "__main__":
    app()
