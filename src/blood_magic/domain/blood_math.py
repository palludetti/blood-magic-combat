from pydantic import BaseModel, ConfigDict, Field
from blood_magic.domain.exceptions import CombatDomainError, ErrorCode


class PureCalculationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    hp_cost: int = Field(ge=1, description="Blood cost calculated from caster max HP and spell cost percentage")
    raw_damage: int = Field(ge=0, description="Raw damage before target defense")
    calculated_damage: int = Field(ge=0, description="Damage mitigated by defense (max(0, raw - def))")
    effective_damage: int = Field(ge=0, description="Actual HP loss applied to target")
    actor_hp_after: int = Field(ge=1, description="Caster HP after paying cost (strictly positive)")
    target_hp_after: int = Field(ge=0, description="Target HP after damage")


def calculate_blood_cost(caster_max_hp: int, hp_cost_percent: int) -> int:
    """
    C = max(1, ceil(HP_max * p / 100))
    With pure integer math:
    C = max(1, (HP_max * p + 99) // 100)
    """
    if caster_max_hp < 1:
        raise ValueError("caster_max_hp must be at least 1")
    if not (1 <= hp_cost_percent <= 100):
        raise ValueError("hp_cost_percent must be between 1 and 100")

    return max(1, (caster_max_hp * hp_cost_percent + 99) // 100)


def calculate_raw_damage(
    base_damage: int,
    magic_power: int,
    hp_cost: int,
    blood_multiplier_percent: int,
) -> int:
    """
    D_bruto = B + P + floor(C * m / 100)
    With integer math:
    D_bruto = base_damage + magic_power + (hp_cost * blood_multiplier_percent) // 100
    """
    if base_damage < 0:
        raise ValueError("base_damage cannot be negative")
    if magic_power < 0:
        raise ValueError("magic_power cannot be negative")
    if hp_cost < 1:
        raise ValueError("hp_cost must be at least 1")
    if blood_multiplier_percent < 0:
        raise ValueError("blood_multiplier_percent cannot be negative")

    return base_damage + magic_power + (hp_cost * blood_multiplier_percent) // 100


def calculate_calculated_damage(raw_damage: int, target_defense: int) -> int:
    """
    D_calculado = max(0, D_bruto - R)
    """
    if target_defense < 0:
        raise ValueError("target_defense cannot be negative")
    return max(0, raw_damage - target_defense)


def calculate_effective_damage(calculated_damage: int, target_current_hp: int) -> int:
    """
    D_efetivo = min(HP_alvo_antes, D_calculado)
    """
    if target_current_hp < 0:
        raise ValueError("target_current_hp cannot be negative")
    return min(target_current_hp, calculated_damage)


def validate_and_compute_spell_action(
    caster_max_hp: int,
    caster_current_hp: int,
    caster_magic_power: int,
    target_current_hp: int,
    target_defense: int,
    spell_hp_cost_percent: int,
    spell_base_damage: int,
    spell_blood_multiplier_percent: int,
) -> PureCalculationResult:
    """
    Pure business rule validation and computation for a blood magic casting.
    Strictly verifies non-suicide rule:
      If current_hp < C: INSUFFICIENT_HP
      If current_hp == C: LETHAL_BLOOD_COST
      Only permits casting when current_hp > C.
    """
    if caster_current_hp <= 0:
        raise CombatDomainError(
            ErrorCode.CHARACTER_DEAD,
            "Caster is dead and cannot cast spells",
        )
    if target_current_hp <= 0:
        raise CombatDomainError(
            ErrorCode.CHARACTER_DEAD,
            "Target is already dead",
        )

    hp_cost = calculate_blood_cost(caster_max_hp, spell_hp_cost_percent)

    if caster_current_hp < hp_cost:
        raise CombatDomainError(
            ErrorCode.INSUFFICIENT_HP,
            f"Insufficient HP: required blood cost is {hp_cost}, but caster has only {caster_current_hp} HP.",
        )

    if caster_current_hp == hp_cost:
        raise CombatDomainError(
            ErrorCode.LETHAL_BLOOD_COST,
            f"Lethal blood cost: casting cost is {hp_cost}, which equals current HP ({caster_current_hp}). Suicidal casting is strictly forbidden.",
        )

    raw_damage = calculate_raw_damage(
        base_damage=spell_base_damage,
        magic_power=caster_magic_power,
        hp_cost=hp_cost,
        blood_multiplier_percent=spell_blood_multiplier_percent,
    )

    calculated_damage = calculate_calculated_damage(
        raw_damage=raw_damage,
        target_defense=target_defense,
    )

    effective_damage = calculate_effective_damage(
        calculated_damage=calculated_damage,
        target_current_hp=target_current_hp,
    )

    actor_hp_after = caster_current_hp - hp_cost
    target_hp_after = target_current_hp - effective_damage

    return PureCalculationResult(
        hp_cost=hp_cost,
        raw_damage=raw_damage,
        calculated_damage=calculated_damage,
        effective_damage=effective_damage,
        actor_hp_after=actor_hp_after,
        target_hp_after=target_hp_after,
    )

