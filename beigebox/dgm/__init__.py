"""
DGM — Darwin Gödel Machine self-improvement loop for BeigeBox.

Iteratively proposes and tests config changes, keeping improvements and
reverting regressions. Uses a rotating pairwise judge rubric to mitigate
Goodhart's Law (can't game a metric that keeps changing).

Scope 2 targets: model assignments, routing prompts, inference parameters.

Quickstart:
    from beigebox.dgm.loop import DGMLoop
    loop = DGMLoop.from_config(n_probes=3, n_iterations=20)
    result = await loop.run()

CLI:
    beigebox dgm run --iterations 20
    beigebox dgm status
"""
from beigebox.dgm.loop import DGMLoop, DGMRunResult
from beigebox.dgm.judge import DGMJudge, JudgeVerdict
from beigebox.dgm.proposer import DGMProposer, Proposal
from beigebox.dgm.patcher import ConfigPatcher, Patch, ALLOWED_KEYS
from beigebox.dgm.rubrics import RubricRotator, RUBRIC_BANK, Rubric

__all__ = [
    "DGMLoop",
    "DGMRunResult",
    "DGMJudge",
    "JudgeVerdict",
    "DGMProposer",
    "Proposal",
    "ConfigPatcher",
    "Patch",
    "ALLOWED_KEYS",
    "RubricRotator",
    "RUBRIC_BANK",
    "Rubric",
]
