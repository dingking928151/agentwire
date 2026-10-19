"""Local format baseline entry points.

DRIXL follows the SamoTech/DRIXL v0.3.0 README compact envelope/body syntax:
https://github.com/SamoTech/DRIXL/blob/v0.3.0/README.md

This is not a DRIXL library-runtime test. No drixl package is installed/imported.
All three renderers carry every MessageV1 field explicitly. The implementation
lives in the installable agentcodec package so the benchmark API also works
outside a source checkout. Percent-encoded JSON parameter values are our local
field mapping, not a claim about upstream escaping/schema support.
"""

from agentcodec.benchmark import (
    DRIXL_REFERENCE,
    render_drixl,
    render_json,
    render_natural_language,
)

__all__ = ["DRIXL_REFERENCE", "render_drixl", "render_json", "render_natural_language"]
