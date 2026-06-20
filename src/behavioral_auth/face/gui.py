"""GUI utilities for OpenCV windows on Linux (X11/Wayland/XWayland).

Call `ensure_display()` before any cv2.imshow() call.
"""

from __future__ import annotations

import os
import sys


def ensure_display() -> None:
    """Configure Qt/OpenCV display backend for the current environment.

    On Wayland sessions Qt5 (used by opencv-contrib-python wheel) defaults to
    the wayland platform plugin which can silently fail.  Forcing xcb makes it
    go through XWayland, which is always available on modern desktops.

    Also points Qt to the system font directory so it doesn't spam the console
    with 'Cannot find font directory' warnings.
    """
    # Force X11/XCB backend (works via XWayland on Wayland sessions)
    if "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    # Silence Qt logging noise (Wayland/font warnings)
    os.environ.setdefault(
        "QT_LOGGING_RULES",
        "qt.qpa.*=false;qt.gui.fonts*=false",
    )
    # Suppress Gnome/Wayland session-type warning printed to stderr
    os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")
    # Tell Qt not to warn about XDG_SESSION_TYPE mismatch
    os.environ["XDG_SESSION_TYPE"] = "x11"

    # Point Qt to system fonts so text renders correctly
    if "QT_QPA_FONTDIR" not in os.environ:
        for font_dir in (
            "/usr/share/fonts",
            "/usr/share/fonts/truetype",
            "/usr/share/fonts/dejavu",
            "/usr/share/fonts/liberation",
        ):
            if os.path.isdir(font_dir):
                os.environ["QT_QPA_FONTDIR"] = font_dir
                break

    # On Linux make sure DISPLAY is forwarded (needed inside terminals/IDEs)
    if sys.platform.startswith("linux") and "DISPLAY" not in os.environ:
        os.environ["DISPLAY"] = ":0"

