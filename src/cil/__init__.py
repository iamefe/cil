__version__ = "0.1.0"


def get_app():
    """Lazy-load Flask app to avoid import-time dependency on Flask."""
    from cil.api import create_app

    return create_app()

