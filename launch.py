"""
Starting point inside the application bundle.

The launcher binary runs this with the bundled copy of Python. It puts the
folder it lives in on the import path and hands over to the server.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.server import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
