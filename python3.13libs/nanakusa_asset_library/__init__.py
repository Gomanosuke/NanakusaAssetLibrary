"""Cross-project asset library for Houdini 22. No third-party dependencies."""
__version__ = "0.17.0"

def show():
    from .ui import show_window
    return show_window()
