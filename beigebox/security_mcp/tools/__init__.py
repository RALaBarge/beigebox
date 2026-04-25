"""
Concrete security tool wrappers. Each module exports one or more SecurityTool
subclasses; this __init__ aggregates them into ALL_TOOL_FACTORIES so the
registry can construct them all in one go.

Adding a new wrapper:
  1. Drop a new module in this directory exposing a SecurityTool subclass.
  2. Append its factory (the class itself works — it's callable) to
     ALL_TOOL_FACTORIES below.
"""
from beigebox.security_mcp.tools.network import (
    NmapScanTool,
    MasscanScanTool,
    DnsenumScanTool,
)
from beigebox.security_mcp.tools.recon import (
    AmassScanTool,
    SubfinderScanTool,
    HttpxProbeTool,
    WafW00fScanTool,
)
from beigebox.security_mcp.tools.web import (
    NucleiScanTool,
    FfufScanTool,
    GobusterScanTool,
    NiktoScanTool,
    SqlmapScanTool,
    WpscanScanTool,
)

ALL_TOOL_FACTORIES = [
    # Network discovery / port scanning
    NmapScanTool,
    MasscanScanTool,
    DnsenumScanTool,
    # Subdomain / asset discovery
    AmassScanTool,
    SubfinderScanTool,
    HttpxProbeTool,
    WafW00fScanTool,
    # Web vuln / fuzz
    NucleiScanTool,
    FfufScanTool,
    GobusterScanTool,
    NiktoScanTool,
    SqlmapScanTool,
    WpscanScanTool,
]
