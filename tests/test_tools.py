"""
Tests for tool modules.
Google search mock mode should always work without API keys.
"""

from beigebox.tools.google_search import GoogleSearchTool


def test_google_search_mock_mode():
    """Google search returns mock results when no API key configured."""
    tool = GoogleSearchTool()
    assert tool.mock_mode is True
    result = tool.run("test query")
    assert "Mock Result" in result
    assert "example.com" in result


def test_google_search_detects_real_mode():
    """Google search enters real mode when API key is provided."""
    tool = GoogleSearchTool(api_key="fake-key", cse_id="fake-cse")
    assert tool.mock_mode is False
