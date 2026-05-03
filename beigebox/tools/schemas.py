"""
Tool Input Schemas — Pydantic models for type validation and documentation.

These schemas define the expected shape of inputs for each tool and serve as:
  1. Type validation (Pydantic enforces at parse time)
  2. OpenAPI documentation (FastAPI auto-generates endpoints)
  3. MCP schema introspection (tools expose structured schema)
  4. IDE autocomplete (type hints in operator code)

Philosophy: Conservative defaults (strict validation, fail-safe).
Use `Config.extra = "forbid"` to reject unknown fields (catch typos).
"""

from typing import Optional, Literal, List, Any
from pydantic import BaseModel, Field, validator


# ──────────────────────────────────────────────────────────────────────────────
# Critical Tools (High Validation)
# ──────────────────────────────────────────────────────────────────────────────


class NetworkAuditScanNetworkInput(BaseModel):
    """NetworkAuditTool.scan_network: RFC1918-only network scan."""

    subnet: Optional[str] = Field(
        None,
        description="RFC1918 subnet (e.g., 192.168.1.0/24). CIDR notation required.",
    )
    ports: Literal["top-1000", "top-100", "common"] = Field(
        "top-1000",
        description="Port range to scan",
    )
    timeout: float = Field(
        1.0,
        ge=0.1,
        le=10.0,
        description="Timeout per host (seconds)",
    )
    concurrency: int = Field(
        100,
        ge=1,
        le=500,
        description="Max concurrent probes",
    )

    class Config:
        extra = "forbid"


class CDPNavigateInput(BaseModel):
    """CDP.navigate: navigate browser to URL (http/https only)."""

    url: str = Field(
        ...,
        max_length=2048,
        description="HTTP/HTTPS URL only",
    )

    @validator("url")
    def validate_url(cls, v):
        """Reject data:, javascript:, blob: schemes."""
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError(f"URL must start with http:// or https://")
        dangerous_schemes = {"javascript:", "data:", "blob:", "vbscript:"}
        v_lower = v.lower()
        if any(v_lower.startswith(s) for s in dangerous_schemes):
            raise ValueError(f"Dangerous URL scheme: {v[:30]}...")
        return v

    class Config:
        extra = "forbid"


# ──────────────────────────────────────────────────────────────────────────────
# Medium-Risk Tools
# ──────────────────────────────────────────────────────────────────────────────


class ApexAnalyzerInput(BaseModel):
    """ApexAnalyzerTool: search Apex code (ReDoS prevention)."""

    query: str = Field(
        ...,
        max_length=1000,
        description="Search query or regex pattern",
    )
    search_type: Literal["class", "trigger", "soql", "pattern"] = Field(
        "pattern",
        description="Type of search",
    )

    @validator("query")
    def validate_query(cls, v):
        """Basic ReDoS prevention: limit quantifiers."""
        import re

        quantifier_count = len(re.findall(r"[*+?{]", v))
        if quantifier_count > 15:
            raise ValueError(
                f"Regex too complex: {quantifier_count} quantifiers (max 15)"
            )
        return v

    class Config:
        extra = "forbid"


class AtlassianSearchInput(BaseModel):
    """AtlassianTool: JQL query with length limit."""

    query: str = Field(
        ...,
        max_length=2000,
        description="JQL query or Confluence search",
    )
    service: Literal["jira", "confluence"] = Field(
        "jira",
        description="Which Atlassian service",
    )

    class Config:
        extra = "forbid"


class ConfluenceCrawlerInput(BaseModel):
    """ConfluenceCrawler: crawl Confluence page."""

    url: str = Field(
        ...,
        max_length=2048,
        description="Confluence page URL",
    )

    @validator("url")
    def validate_url(cls, v):
        """Ensure http/https scheme."""
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    class Config:
        extra = "forbid"


# ──────────────────────────────────────────────────────────────────────────────
# Simple Tools (Basic Type Validation)
# ──────────────────────────────────────────────────────────────────────────────


class WebSearchInput(BaseModel):
    """WebSearchTool: search the web."""

    query: str = Field(
        ...,
        max_length=500,
        description="Search query",
    )

    class Config:
        extra = "forbid"


class WebScraperInput(BaseModel):
    """WebScraperTool: scrape a web page."""

    url: str = Field(
        ...,
        max_length=2048,
        description="URL to scrape",
    )

    @validator("url")
    def validate_url(cls, v):
        """Ensure http/https scheme."""
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    class Config:
        extra = "forbid"


class CalculatorInput(BaseModel):
    """CalculatorTool: evaluate math expression."""

    expression: str = Field(
        ...,
        max_length=200,
        description="Math expression (e.g., '2 ** 10 + sqrt(144)')",
    )

    class Config:
        extra = "forbid"


class DocumentSearchInput(BaseModel):
    """DocumentSearchTool: semantic search over documents."""

    query: str = Field(
        ...,
        max_length=1000,
        description="Search query",
    )

    class Config:
        extra = "forbid"


class BlueTruthInput(BaseModel):
    """BlueTruthTool: Bluetooth diagnostics."""

    command: str = Field(
        ...,
        max_length=500,
        description="Command to execute",
    )
    args: Optional[dict] = Field(
        None,
        description="Command arguments",
    )

    class Config:
        extra = "forbid"


class BrowserboxInput(BaseModel):
    """BrowserboxTool: browser automation."""

    action: str = Field(
        ...,
        max_length=100,
        description="Action to perform",
    )
    args: Optional[dict] = Field(
        None,
        description="Action arguments",
    )

    class Config:
        extra = "forbid"


class AuraReconInput(BaseModel):
    """AuraReconTool: Salesforce Aura descriptor sniffing."""

    action: str = Field(
        ...,
        max_length=100,
        description="Action to perform",
    )
    args: Optional[dict] = Field(
        None,
        description="Action arguments",
    )

    class Config:
        extra = "forbid"


class ConnectionInput(BaseModel):
    """ConnectionTool: agentauth connection management."""

    name: str = Field(
        ...,
        max_length=100,
        description="Connection name",
    )
    action: Optional[str] = Field(
        None,
        max_length=100,
        description="Action to perform on connection",
    )

    class Config:
        extra = "forbid"


# ──────────────────────────────────────────────────────────────────────────────
# Generic Input (for unknown tools)
# ──────────────────────────────────────────────────────────────────────────────


class GenericToolInput(BaseModel):
    """Fallback for unknown tools: minimal validation."""

    input: Any = Field(
        ...,
        description="Tool input (any format)",
    )

    class Config:
        extra = "allow"  # Allow unknown fields for forwards compatibility


# ──────────────────────────────────────────────────────────────────────────────
# Helper: Get schema by tool name
# ──────────────────────────────────────────────────────────────────────────────


TOOL_INPUT_SCHEMAS = {
    "network_audit": NetworkAuditScanNetworkInput,
    "cdp": CDPNavigateInput,
    "apex_analyzer": ApexAnalyzerInput,
    "atlassian": AtlassianSearchInput,
    "web_search": WebSearchInput,
    "web_scraper": WebScraperInput,
    "calculator": CalculatorInput,
    "document_search": DocumentSearchInput,
    "bluetruth": BlueTruthInput,
    "browserbox": BrowserboxInput,
    "aura_recon": AuraReconInput,
    "confluence_crawler": ConfluenceCrawlerInput,
    "connection": ConnectionInput,
}


def get_schema_for_tool(tool_name: str):
    """Get Pydantic schema class for a tool by name."""
    base_tool = tool_name.split(".")[0]
    return TOOL_INPUT_SCHEMAS.get(base_tool, GenericToolInput)
