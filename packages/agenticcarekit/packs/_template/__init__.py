"""Template pack (`_template`) — the near-empty second pack that proves
domain is a pack, not the architecture (brief invariant 8). Copy this
whole directory to start a new domain pack; see README.md here for the
full walkthrough of manifest keys, exports, and discovery.
"""

from .models import Example
from .redactor import TemplateRedactor

__all__ = ["Example", "TemplateRedactor"]
