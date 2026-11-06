from blood_magic.domain.exceptions import CombatDomainError, ErrorCode
from blood_magic.domain.blood_math import (
    calculate_blood_cost,
    calculate_raw_damage,
    calculate_calculated_damage,
    calculate_effective_damage,
    validate_and_compute_spell_action,
    PureCalculationResult,
)
from blood_magic.domain.models import (
    CombatStatus,
    Character,
    Spell,
    CombatParticipant,
    Combat,
    CombatAction,
)

__all__ = [
    "ErrorCode",
    "CombatDomainError",
    "calculate_blood_cost",
    "calculate_raw_damage",
    "calculate_calculated_damage",
    "calculate_effective_damage",
    "validate_and_compute_spell_action",
    "PureCalculationResult",
    "CombatStatus",
    "Character",
    "Spell",
    "CombatParticipant",
    "Combat",
    "CombatAction",
]

