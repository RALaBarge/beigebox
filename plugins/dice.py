"""
Dice roller plugin.

Rolls one or more dice. Understands standard RPG notation.

Examples the LLM would route here:
  "Roll a d20"
  "Roll 3d6"
  "Roll 4d6 drop lowest"
  "Flip a coin"

Enable in config.yaml:
    tools:
      plugins:
        enabled: true
        dice:
          enabled: true
"""

import random
import re

PLUGIN_NAME = "dice"


class DiceTool:
    """Dice roller — understands XdY notation and common RPG rolls."""

    def run(self, query: str) -> str:
        q = query.lower().strip()

        # Coin flip
        if any(w in q for w in ("coin", "flip", "heads", "tails")):
            result = random.choice(["Heads", "Tails"])
            return f"🪙 {result}"

        # Drop-lowest (e.g. "4d6 drop lowest")
        drop_match = re.search(r"(\d+)d(\d+)\s+drop\s+low", q)
        if drop_match:
            count = min(int(drop_match.group(1)), 20)
            sides = min(int(drop_match.group(2)), 1000)
            rolls = sorted([random.randint(1, sides) for _ in range(count)])
            kept = rolls[1:]
            return (
                f"🎲 {count}d{sides} drop lowest: rolled {rolls} → "
                f"kept {kept} = **{sum(kept)}**"
            )

        # Standard XdY
        dice_match = re.search(r"(\d+)?d(\d+)", q)
        if dice_match:
            count = int(dice_match.group(1) or 1)
            sides = int(dice_match.group(2))
            count = min(count, 20)
            sides = min(sides, 10000)
            rolls = [random.randint(1, sides) for _ in range(count)]
            total = sum(rolls)
            if count == 1:
                return f"🎲 d{sides}: **{rolls[0]}**"
            return f"🎲 {count}d{sides}: {rolls} = **{total}**"

        # Fallback — roll a d20
        return f"🎲 d20: **{random.randint(1, 20)}**"
