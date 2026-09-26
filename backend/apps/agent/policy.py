from dataclasses import dataclass
from enum import StrEnum


class RiskLevel(StrEnum):
    READ = 'read'
    DRAFT = 'draft'
    ACTION = 'action'


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ''


class EditorToolPolicy:
    """Fail-closed policy: undeclared tools are treated as external actions."""

    def evaluate(self, risk_level: RiskLevel) -> PolicyDecision:
        if risk_level in {RiskLevel.READ, RiskLevel.DRAFT}:
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            requires_approval=True,
            reason='External actions require explicit editor approval.',
        )
