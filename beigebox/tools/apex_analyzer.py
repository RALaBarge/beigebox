"""
ApexAnalyzerTool — search and analyze Salesforce Apex code locally.

Searches IntelliJ IDEA project structure (macOS standard: ~/IdeaProjects/).
Provides grep, class inspection, SOQL extraction, and anti-pattern detection.

Input format (JSON string):
    {
        "action": "search",
        "query": "keyword to search for"
    }

Actions:
    search       — grep Apex files for keyword (classes, triggers, methods)
    find_queries — extract SOQL from a class or all classes
    find_triggers — list all triggers matching a pattern
    read_class   — read full source of a class
    check_pattern — detect anti-patterns (N+1, bulk loops, etc.)
    list_classes — list all classes in project
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ApexAnalyzerTool:
    description = (
        "Search and analyze Salesforce Apex code in your local IDE project.\n"
        "Supports: class/trigger/query search, SOQL extraction, anti-pattern detection.\n"
        "\n"
        "Input format: {\"action\": \"...\", \"query\": \"...\"}\n"
        "\n"
        "Actions:\n"
        '  search       — Find keyword in Apex code:  {"action": "search", "query": "Account"}\n'
        '  find_queries — Extract SOQL from code:    {"action": "find_queries", "class": "MyClass"}\n'
        '  find_triggers — List triggers:            {"action": "find_triggers"}\n'
        '  read_class   — Read full class source:    {"action": "read_class", "class": "MyClass"}\n'
        '  check_pattern — Find anti-patterns:       {"action": "check_pattern", "pattern": "n+1"}\n'
        '  list_classes — List all Apex classes:     {"action": "list_classes"}\n'
    )

    def __init__(self, project_root: str | None = None):
        """
        Initialize with project root. Defaults to macOS standard location.
        Falls back to ~/IdeaProjects or ~/Projects.
        """
        if project_root:
            self._project_root = Path(project_root)
        else:
            # Try standard macOS IntelliJ CE locations
            candidates = [
                Path.home() / "IdeaProjects",
                Path.home() / "Projects",
            ]
            self._project_root = None
            for cand in candidates:
                if cand.exists():
                    self._project_root = cand
                    logger.info("ApexAnalyzerTool: using project root %s", cand)
                    break

            if not self._project_root:
                logger.warning("ApexAnalyzerTool: no project root found. Searched: %s", candidates)
                self._project_root = candidates[0]  # Fallback to first candidate

    def _find_apex_files(self, pattern: str | None = None) -> list[Path]:
        """Find all .cls and .trigger files in project."""
        if not self._project_root.exists():
            return []

        files = []
        try:
            # Search recursively for .cls and .trigger files
            for ext in ["*.cls", "*.trigger"]:
                files.extend(self._project_root.rglob(ext))
        except Exception as e:
            logger.warning("Error scanning Apex files: %s", e)

        # Optional: filter by pattern
        if pattern:
            pattern_lower = pattern.lower()
            files = [f for f in files if pattern_lower in f.name.lower()]

        return sorted(files)

    def _grep_apex(self, query: str, limit: int = 20) -> list[dict]:
        """Grep Apex files for query. Returns matches with context."""
        results = []
        apex_files = self._find_apex_files()

        if not apex_files:
            return []

        for file_path in apex_files:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()

                for line_no, line in enumerate(lines, 1):
                    if query.lower() in line.lower():
                        results.append({
                            "file": file_path.name,
                            "path": str(file_path),
                            "line_no": line_no,
                            "text": line.strip(),
                        })
                        if len(results) >= limit:
                            return results
            except Exception as e:
                logger.warning("Error reading %s: %s", file_path, e)

        return results

    def _extract_soql_from_file(self, file_path: Path) -> list[str]:
        """Extract SOQL queries from a file."""
        queries = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Match common SOQL patterns
            # 1. Database.query('SELECT ...')
            # 2. [SELECT ... FROM ...]
            # 3. String query = 'SELECT ...'
            patterns = [
                r"Database\.query\s*\(\s*['\"]([^'\"]*SELECT[^'\"]*)['\"]",
                r"\[\s*SELECT\s+[^\]]+\]",
                r"(?:String|List)\s+\w+\s*=\s*['\"]([^'\"]*SELECT[^'\"]*)['\"]",
            ]

            for pattern in patterns:
                matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
                for match in matches:
                    # Clean up match
                    if isinstance(match, tuple):
                        match = match[0] if match else ""
                    if match.strip():
                        queries.append(match.strip())

        except Exception as e:
            logger.warning("Error extracting SOQL from %s: %s", file_path, e)

        return queries

    def _check_antipatterns(self, file_path: Path) -> list[dict]:
        """Detect common Apex anti-patterns."""
        issues = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                content = "".join(lines)

            # Pattern: N+1 query inside loop
            if re.search(r"for\s*\([^)]*\)\s*{[^}]*(?:Database\.query|SELECT)[^}]*}", content):
                issues.append({
                    "type": "n+1_query",
                    "severity": "HIGH",
                    "description": "Potential N+1 query: SOQL inside loop",
                })

            # Pattern: DML inside loop
            if re.search(
                r"for\s*\([^)]*\)\s*{[^}]*(?:insert|update|delete|upsert)\s+\w+[^}]*}",
                content, re.IGNORECASE
            ):
                issues.append({
                    "type": "bulk_dml",
                    "severity": "HIGH",
                    "description": "DML inside loop: use bulk operations",
                })

            # Pattern: No LIMIT on SOQL
            if re.search(r"SELECT\s+[^FROM]*FROM\s+\w+(?!.*LIMIT)", content, re.IGNORECASE):
                issues.append({
                    "type": "no_soql_limit",
                    "severity": "MEDIUM",
                    "description": "SOQL query without LIMIT clause",
                })

            # Pattern: Async call issues (future/batch without await)
            if re.search(r"@future|Batch\s*<", content) and not re.search(r"Test\.stopTest", content):
                issues.append({
                    "type": "async_not_tested",
                    "severity": "MEDIUM",
                    "description": "@future or Batch without Test.stopTest",
                })

        except Exception as e:
            logger.warning("Error checking anti-patterns in %s: %s", file_path, e)

        return issues

    def run(self, input_str: str) -> str:
        """Parse input JSON and dispatch to appropriate action."""
        try:
            params = json.loads(input_str.strip())
        except json.JSONDecodeError:
            return 'Error: input must be JSON {"action": "...", "query": "..."}'

        action = params.get("action", "").lower()
        query = params.get("query", "")
        class_name = params.get("class", "")
        pattern = params.get("pattern", "")

        try:
            if action == "search":
                return self._do_search(query)
            elif action == "find_queries":
                return self._do_find_queries(class_name)
            elif action == "find_triggers":
                return self._do_find_triggers(pattern)
            elif action == "read_class":
                return self._do_read_class(class_name)
            elif action == "check_pattern":
                return self._do_check_pattern(pattern)
            elif action == "list_classes":
                return self._do_list_classes()
            else:
                return f"Error: unknown action '{action}'. Use: search, find_queries, find_triggers, read_class, check_pattern, list_classes"
        except Exception as exc:
            logger.error("ApexAnalyzerTool error: %s", exc)
            return f"Error: {exc}"

    def _do_search(self, query: str) -> str:
        """Search for keyword in Apex code."""
        if not query:
            return "Error: query required for search action"

        results = self._grep_apex(query)
        if not results:
            return f"No matches found for '{query}'"

        lines = [f"Found {len(results)} matches for '{query}':"]
        for res in results[:20]:
            lines.append(f"\n  {res['file']}:{res['line_no']}")
            lines.append(f"    {res['text'][:100]}")

        return "\n".join(lines)

    def _do_find_queries(self, class_name: str) -> str:
        """Extract SOQL queries from a class."""
        if not class_name:
            # Find all SOQL in all classes
            all_queries = []
            for file_path in self._find_apex_files():
                queries = self._extract_soql_from_file(file_path)
                all_queries.extend([(file_path.name, q) for q in queries])

            if not all_queries:
                return "No SOQL queries found in project."

            lines = [f"Found {len(all_queries)} SOQL queries:"]
            for filename, query in all_queries[:10]:
                lines.append(f"\n  {filename}:")
                lines.append(f"    {query[:100]}...")
            return "\n".join(lines)

        # Find specific class
        apex_files = self._find_apex_files(class_name)
        if not apex_files:
            return f"Class '{class_name}' not found"

        queries = self._extract_soql_from_file(apex_files[0])
        if not queries:
            return f"No SOQL queries found in {class_name}"

        lines = [f"SOQL in {class_name}:"]
        for query in queries:
            lines.append(f"\n  {query}")

        return "\n".join(lines)

    def _do_find_triggers(self, pattern: str) -> str:
        """List all triggers matching pattern."""
        triggers = self._find_apex_files(pattern or "")
        triggers = [f for f in triggers if f.suffix == ".trigger"]

        if not triggers:
            return f"No triggers found{f' matching {pattern}' if pattern else ''}."

        lines = [f"Found {len(triggers)} trigger(s):"]
        for trig in triggers:
            lines.append(f"  • {trig.name}")

        return "\n".join(lines)

    def _do_read_class(self, class_name: str) -> str:
        """Read full class source."""
        if not class_name:
            return "Error: class name required"

        apex_files = self._find_apex_files(class_name)
        if not apex_files:
            return f"Class '{class_name}' not found"

        try:
            with open(apex_files[0], "r", encoding="utf-8") as f:
                content = f.read()

            # Truncate if very long
            if len(content) > 10000:
                content = content[:10000] + f"\n... ({len(content)} chars total, truncated)"

            return f"Source of {class_name}:\n\n{content}"
        except Exception as e:
            return f"Error reading {class_name}: {e}"

    def _do_check_pattern(self, pattern: str) -> str:
        """Check all files for anti-patterns."""
        if not pattern:
            # Check all files
            all_issues = []
            for file_path in self._find_apex_files():
                issues = self._check_antipatterns(file_path)
                all_issues.extend([(file_path.name, issue) for issue in issues])

            if not all_issues:
                return "No anti-patterns detected."

            lines = [f"Found {len(all_issues)} potential issues:"]
            for filename, issue in all_issues:
                lines.append(
                    f"\n  [{issue['severity']}] {filename}: {issue['description']} ({issue['type']})"
                )

            return "\n".join(lines)

        # Check for specific pattern
        pattern_lower = pattern.lower()
        matching = []
        for file_path in self._find_apex_files():
            issues = self._check_antipatterns(file_path)
            matching.extend([(file_path.name, i) for i in issues if pattern_lower in i["type"].lower()])

        if not matching:
            return f"No '{pattern}' patterns found."

        lines = [f"Found {len(matching)} '{pattern}' issues:"]
        for filename, issue in matching:
            lines.append(f"\n  {filename}: {issue['description']}")

        return "\n".join(lines)

    def _do_list_classes(self) -> str:
        """List all Apex classes in project."""
        apex_files = self._find_apex_files()
        if not apex_files:
            return f"No Apex classes found in {self._project_root}"

        classes = [f for f in apex_files if f.suffix == ".cls"]
        triggers = [f for f in apex_files if f.suffix == ".trigger"]

        lines = [f"Apex project ({self._project_root}):"]
        if classes:
            lines.append(f"\nClasses ({len(classes)}):")
            for cls in classes[:30]:
                lines.append(f"  • {cls.name}")
            if len(classes) > 30:
                lines.append(f"  ... and {len(classes) - 30} more")

        if triggers:
            lines.append(f"\nTriggers ({len(triggers)}):")
            for trig in triggers:
                lines.append(f"  • {trig.name}")

        return "\n".join(lines)
