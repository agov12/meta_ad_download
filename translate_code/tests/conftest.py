"""Make `ad_localizer` importable from src/ even when not pip-installed.

The normal path is `uv sync --extra dev && uv run pytest`, which installs the
package. This fallback keeps the suite runnable in a bare environment
(e.g. while the pinned `syncsdk>=1.0` dependency is unresolvable on PyPI).
"""

import sys
from pathlib import Path

try:
    import ad_localizer  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
