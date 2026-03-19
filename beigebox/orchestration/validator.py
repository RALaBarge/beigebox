"""
ResultValidator: Quality gate for agent outputs.

Validates that agent responses match the output contract defined in TaskPacket.
Uses Pydantic for robust type checking and clear error messages.

Invalid results can be retried once with error feedback, then escalated.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ValidationError, Field

from beigebox.orchestration.packet import TaskPacket, WorkerResult

logger = logging.getLogger(__name__)


class ResultValidator:
    """
    Validates agent outputs against the TaskPacket output contract.

    Workflow:
    1. Check if response is valid JSON
    2. Parse as WorkerResult (strict schema)
    3. Return (is_valid, parsed_result, error_list)
    """

    def validate(
        self,
        raw_response: Dict[str, Any] | str,
        packet: TaskPacket,
    ) -> Tuple[bool, Optional[WorkerResult], List[str]]:
        """
        Validate raw agent response against packet output schema.

        Args:
            raw_response: Response from agent (usually dict, sometimes JSON string)
            packet: TaskPacket defining the expected output schema

        Returns:
            (is_valid, parsed_result, error_messages)
            - is_valid: True if response matches schema
            - parsed_result: Parsed WorkerResult if valid, None otherwise
            - error_messages: List of validation errors (if any)
        """
        errors = []

        # Step 1: Parse response if needed
        if isinstance(raw_response, str):
            try:
                raw_response = json.loads(raw_response)
            except json.JSONDecodeError as e:
                errors.append(f"Invalid JSON: {str(e)}")
                return False, None, errors

        # Step 2: Validate structure
        if not isinstance(raw_response, dict):
            errors.append(f"Response must be dict, got {type(raw_response).__name__}")
            return False, None, errors

        # Step 3: Check required fields
        required_fields = ["status", "answer", "confidence"]
        for field in required_fields:
            if field not in raw_response:
                errors.append(f"Missing required field: {field}")

        if errors:
            return False, None, errors

        # Step 4: Validate using Pydantic model
        try:
            result = WorkerResult(
                status=raw_response.get("status", "success"),
                answer=raw_response.get("answer", ""),
                confidence=float(raw_response.get("confidence", 0.5)),
                evidence=raw_response.get("evidence", []),
                follow_up_needed=raw_response.get("follow_up_needed", []),
                artifacts_created=raw_response.get("artifacts_created", []),
            )

            # Validate confidence is in [0, 1]
            if not (0.0 <= result.confidence <= 1.0):
                errors.append(f"Confidence must be 0-1, got {result.confidence}")
                return False, None, errors

            # Validate status is valid
            if result.status not in ["success", "needs_escalation", "blocked"]:
                errors.append(
                    f"Invalid status: {result.status}. "
                    f"Must be 'success', 'needs_escalation', or 'blocked'"
                )
                return False, None, errors

            logger.debug(
                f"Validated result {packet.task_id}: status={result.status}, "
                f"confidence={result.confidence:.2f}"
            )
            return True, result, []

        except Exception as e:
            errors.append(f"Validation error: {str(e)}")
            return False, None, errors

    def build_retry_prompt(
        self,
        packet: TaskPacket,
        raw_response: Dict[str, Any],
        errors: List[str],
    ) -> str:
        """
        Build a prompt for retrying the agent with validation feedback.

        Args:
            packet: Original task packet
            raw_response: The invalid response from the agent
            errors: List of validation errors

        Returns:
            String prompt to send back to agent
        """
        return f"""
Your previous response was invalid. Please fix and resubmit.

Original task: {packet.objective}

Required output format:
{json.dumps(packet.output_schema, indent=2)}

Your previous response:
{json.dumps(raw_response, indent=2)}

Validation errors:
{chr(10).join(f"- {e}" for e in errors)}

Please respond with valid JSON matching the schema above.
"""
