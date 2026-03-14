"""
Calculator tool — evaluates simple math expressions.

This gives the decision LLM something concrete to route math questions to.
Safe eval using ast.literal_eval for basic arithmetic.

Examples the decision LLM would route here:
  "What's 15% of 340?"
  "Calculate 2^16"
  "How many seconds in 3.5 hours?"
"""

import ast
import logging
import operator
import re

logger = logging.getLogger(__name__)

# Whitelist of AST node types → stdlib operator functions. The recursive
# evaluator rejects any node type not in this dict, preventing code injection
# via attribute access, function calls, or any other non-arithmetic construct.
SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _safe_eval(node):
    """Recursively evaluate an AST node with only safe math operations."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value}")
    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in SAFE_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if op_type is ast.Pow and abs(right) > 10000:
            raise ValueError("Exponent too large (max 10000)")
        return SAFE_OPS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in SAFE_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        return SAFE_OPS[op_type](_safe_eval(node.operand))
    else:
        raise ValueError(f"Unsupported expression: {type(node).__name__}")


class CalculatorTool:
    """Safe math expression evaluator."""

    description = 'Evaluate a math expression. input = expression string. Example: {"tool": "calculator", "input": "2 ** 10 + sqrt(144)"}'

    def __init__(self):
        logger.info("CalculatorTool initialized")

    def run(self, expression: str) -> str:
        """
        Evaluate a math expression safely.
        Supports: +, -, *, /, //, %, ** and parentheses.
        """
        # Clean up common natural language patterns
        cleaned = expression.strip()
        # Replace common text patterns
        cleaned = cleaned.replace("^", "**")
        cleaned = cleaned.replace("×", "*")
        cleaned = cleaned.replace("÷", "/")

        # Extract the longest contiguous math-looking substring so the LLM can
        # pass natural language like "what is 3 * 4?" without pre-cleaning.
        math_match = re.search(r'[\d\s\+\-\*/\.\(\)\%\*]+', cleaned)
        if math_match:
            cleaned = math_match.group().strip()

        try:
            tree = ast.parse(cleaned, mode='eval')
            result = _safe_eval(tree)

            # Coerce whole-number floats (e.g. 4.0) to int for cleaner output.
            if isinstance(result, float) and result == int(result):
                result = int(result)

            logger.debug("Calculator: %s = %s", expression, result)
            return f"{cleaned} = {result}"

        except (ValueError, SyntaxError, TypeError, ZeroDivisionError) as e:
            logger.debug("Calculator failed for '%s': %s", expression, e)
            return f"Could not evaluate '{expression}': {e}"
