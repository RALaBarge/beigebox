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
    NmapAdvancedScanTool,
    MasscanScanTool,
    RustscanScanTool,
    FierceScanTool,
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
    KatanaCrawlTool,
    FfufScanTool,
    GobusterScanTool,
    FeroxbusterScanTool,
    DirsearchScanTool,
    NiktoScanTool,
    SqlmapScanTool,
    DalfoxXssScanTool,
    WpscanScanTool,
)
from beigebox.security_mcp.tools.discovery import (
    GauDiscoveryTool,
    WaybackurlsDiscoveryTool,
    ArjunParameterDiscoveryTool,
    ParamspiderMiningTool,
    HakrawlerCrawlTool,
)
from beigebox.security_mcp.tools.lateral import (
    Enum4linuxScanTool,
    Enum4linuxNgScanTool,
    SmbmapScanTool,
    NetexecScanTool,
)
from beigebox.security_mcp.tools.creds import (
    HydraAttackTool,
    JohnCrackTool,
    HashcatCrackTool,
)
from beigebox.security_mcp.tools.binary import (
    BinwalkAnalyzeTool,
    ExiftoolExtractTool,
    ChecksecAnalyzeTool,
)
from beigebox.security_mcp.tools.tls import (
    TestsslScanTool,
    SslscanScanTool,
    SshAuditScanTool,
)
from beigebox.security_mcp.tools.protocols import (
    SnmpwalkScanTool,
    OnesixtyoneScanTool,
    NbtscanScanTool,
    LdapsearchScanTool,
)
from beigebox.security_mcp.tools.ad import (
    ImpacketSecretsdumpTool,
    ImpacketGetuserspnsTool,
    ImpacketGetnpusersTool,
    KerbruteUserenumTool,
)
from beigebox.security_mcp.tools.osint import (
    WhatwebScanTool,
    SearchsploitLookupTool,
    TheharvesterScanTool,
    CewlWordlistGenTool,
    MsfvenomGenerateTool,
)
from beigebox.security_mcp.tools.projectdiscovery import (
    NaabuScanTool,
    DnsxResolveTool,
)

ALL_TOOL_FACTORIES = [
    # Network discovery / port scanning
    NmapScanTool,
    NmapAdvancedScanTool,
    MasscanScanTool,
    RustscanScanTool,
    FierceScanTool,
    DnsenumScanTool,
    # Subdomain / asset discovery
    AmassScanTool,
    SubfinderScanTool,
    HttpxProbeTool,
    WafW00fScanTool,
    # Web vuln / fuzz / crawl
    NucleiScanTool,
    KatanaCrawlTool,
    FfufScanTool,
    GobusterScanTool,
    FeroxbusterScanTool,
    DirsearchScanTool,
    NiktoScanTool,
    SqlmapScanTool,
    DalfoxXssScanTool,
    WpscanScanTool,
    # URL / parameter discovery (passive intel)
    GauDiscoveryTool,
    WaybackurlsDiscoveryTool,
    ArjunParameterDiscoveryTool,
    ParamspiderMiningTool,
    HakrawlerCrawlTool,
    # SMB / AD / lateral
    Enum4linuxScanTool,
    Enum4linuxNgScanTool,
    SmbmapScanTool,
    NetexecScanTool,
    # Credentials / cracking (require authorization=true)
    HydraAttackTool,
    JohnCrackTool,
    HashcatCrackTool,
    # Binary / forensics
    BinwalkAnalyzeTool,
    ExiftoolExtractTool,
    ChecksecAnalyzeTool,
    # SSL/TLS / SSH config audit
    TestsslScanTool,
    SslscanScanTool,
    SshAuditScanTool,
    # SNMP / NetBIOS / LDAP
    SnmpwalkScanTool,
    OnesixtyoneScanTool,
    NbtscanScanTool,
    LdapsearchScanTool,
    # Active Directory / Kerberos (impacket + kerbrute) — most require authorization
    ImpacketSecretsdumpTool,
    ImpacketGetuserspnsTool,
    ImpacketGetnpusersTool,
    KerbruteUserenumTool,
    # OSINT / exploit lookup / payload gen
    WhatwebScanTool,
    SearchsploitLookupTool,
    TheharvesterScanTool,
    CewlWordlistGenTool,
    MsfvenomGenerateTool,
    # ProjectDiscovery extras
    NaabuScanTool,
    DnsxResolveTool,
]
