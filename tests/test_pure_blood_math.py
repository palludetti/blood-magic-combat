import pytest
from blood_magic.domain.exceptions import CombatDomainError, ErrorCode
from blood_magic.domain.blood_math import (
    calculate_blood_cost,
    calculate_raw_damage,
    calculate_calculated_damage,
    calculate_effective_damage,
    validate_and_compute_spell_action,
)


class TestBloodMagicMathRules:
    """
    Unit tests for pure domain blood magic mathematics and non-suicide rules.
    Runs purely in Python without database connections.
    """

    def test_mandatory_specification_example(self):
        """
        Exemplo obrigatório em documentação e teste:
        HP máximo 100, HP atual 50, custo percentual 12, dano base 8, poder mágico 5,
        multiplicador 150 e defesa 4 resultam em:
        - custo: 12
        - dano bruto: 31
        - dano calculado: 27
        - HP final do conjurador: 38
        """
        caster_max_hp = 100
        caster_current_hp = 50
        hp_cost_percent = 12
        base_damage = 8
        magic_power = 5
        blood_multiplier_percent = 150
        target_defense = 4
        target_current_hp = 100

        res = validate_and_compute_spell_action(
            caster_max_hp=caster_max_hp,
            caster_current_hp=caster_current_hp,
            caster_magic_power=magic_power,
            target_current_hp=target_current_hp,
            target_defense=target_defense,
            spell_hp_cost_percent=hp_cost_percent,
            spell_base_damage=base_damage,
            spell_blood_multiplier_percent=blood_multiplier_percent,
        )

        assert res.hp_cost == 12
        assert res.raw_damage == 31
        assert res.calculated_damage == 27
        assert res.effective_damage == 27
        assert res.actor_hp_after == 38
        assert res.target_hp_after == 73

    def test_blood_cost_ceiling_rounding(self):
        """
        Verify that cost formula max(1, (HP_max * p + 99) // 100)
        always rounds up ceiling correctly.
        """
        # HP max 95, p = 10% -> 95 * 10 / 100 = 9.5 -> ceil is 10
        # (95 * 10 + 99) // 100 = 1049 // 100 = 10
        assert calculate_blood_cost(95, 10) == 10

        # Exact integer division: HP max 100, p = 10% -> 10
        # (1000 + 99) // 100 = 1099 // 100 = 10
        assert calculate_blood_cost(100, 10) == 10

        # Very small percentage with small HP: 10 HP, 1% -> 0.1 -> ceil is 1
        assert calculate_blood_cost(10, 1) == 1

        # High HP: 1005 HP, 1% -> 10.05 -> ceil is 11
        # (1005 * 1 + 99) // 100 = 1104 // 100 = 11
        assert calculate_blood_cost(1005, 1) == 11

    def test_defense_reduces_damage_to_zero(self):
        """
        Defense greater than or equal to raw damage yields 0 calculated and effective damage.
        Full blood cost is still deducted from caster.
        """
        caster_max_hp = 100
        caster_current_hp = 50
        res = validate_and_compute_spell_action(
            caster_max_hp=caster_max_hp,
            caster_current_hp=caster_current_hp,
            caster_magic_power=0,
            target_current_hp=80,
            target_defense=1000,  # Impenetrable defense
            spell_hp_cost_percent=10,
            spell_base_damage=10,
            spell_blood_multiplier_percent=100,
        )

        assert res.hp_cost == 10
        assert res.raw_damage == 20  # 10 base + 0 magic + (10 * 100 // 100) = 20
        assert res.calculated_damage == 0
        assert res.effective_damage == 0
        assert res.actor_hp_after == 40  # Cost still paid
        assert res.target_hp_after == 80  # Target unscathed

    def test_damage_greater_than_target_remaining_hp(self):
        """
        Effective damage is capped at target current HP: D_efetivo = min(HP_alvo_antes, D_calculado).
        Target HP after cannot drop below 0.
        """
        res = validate_and_compute_spell_action(
            caster_max_hp=100,
            caster_current_hp=80,
            caster_magic_power=10,
            target_current_hp=15,  # Low target HP
            target_defense=5,
            spell_hp_cost_percent=10,
            spell_base_damage=50,
            spell_blood_multiplier_percent=100,
        )

        # Raw: 50 + 10 + (10 * 100 // 100) = 70
        assert res.raw_damage == 70
        # Calculated: 70 - 5 = 65
        assert res.calculated_damage == 65
        # Effective: min(15, 65) = 15
        assert res.effective_damage == 15
        assert res.target_hp_after == 0

    def test_cost_greater_than_current_hp_rejected(self):
        """
        If current_hp < C, reject with INSUFFICIENT_HP.
        """
        with pytest.raises(CombatDomainError) as exc_info:
            validate_and_compute_spell_action(
                caster_max_hp=100,
                caster_current_hp=10,
                caster_magic_power=5,
                target_current_hp=50,
                target_defense=0,
                spell_hp_cost_percent=20,  # Cost will be 20
                spell_base_damage=10,
                spell_blood_multiplier_percent=100,
            )
        assert exc_info.value.code == ErrorCode.INSUFFICIENT_HP

    def test_cost_equal_to_current_hp_rejected_as_suicide(self):
        """
        If current_hp == C, reject with LETHAL_BLOOD_COST.
        Non-suicide policy: suicidal casting is strictly forbidden.
        """
        with pytest.raises(CombatDomainError) as exc_info:
            validate_and_compute_spell_action(
                caster_max_hp=100,
                caster_current_hp=20,
                caster_magic_power=5,
                target_current_hp=50,
                target_defense=0,
                spell_hp_cost_percent=20,  # Cost is exactly 20
                spell_base_damage=10,
                spell_blood_multiplier_percent=100,
            )
        assert exc_info.value.code == ErrorCode.LETHAL_BLOOD_COST

    def test_cost_strictly_less_than_current_hp_allowed(self):
        """
        Only permit casting when current_hp > C.
        """
        res = validate_and_compute_spell_action(
            caster_max_hp=100,
            caster_current_hp=21,  # 21 > 20
            caster_magic_power=5,
            target_current_hp=50,
            target_defense=0,
            spell_hp_cost_percent=20,  # Cost is 20
            spell_base_damage=10,
            spell_blood_multiplier_percent=100,
        )
        assert res.hp_cost == 20
        assert res.actor_hp_after == 1

    def test_dead_characters_cannot_act_or_be_targeted(self):
        with pytest.raises(CombatDomainError) as exc_info:
            validate_and_compute_spell_action(
                caster_max_hp=100,
                caster_current_hp=0,  # Dead caster
                caster_magic_power=5,
                target_current_hp=50,
                target_defense=0,
                spell_hp_cost_percent=10,
                spell_base_damage=10,
                spell_blood_multiplier_percent=100,
            )
        assert exc_info.value.code == ErrorCode.CHARACTER_DEAD

        with pytest.raises(CombatDomainError) as exc_info:
            validate_and_compute_spell_action(
                caster_max_hp=100,
                caster_current_hp=50,
                caster_magic_power=5,
                target_current_hp=0,  # Dead target
                target_defense=0,
                spell_hp_cost_percent=10,
                spell_base_damage=10,
                spell_blood_multiplier_percent=100,
            )
        assert exc_info.value.code == ErrorCode.CHARACTER_DEAD

    def test_maximum_allowed_values(self):
        """
        Verify math calculations at maximum constraint boundaries:
        max_hp=1,000,000, magic_power=1,000,000, base_damage=1,000,000,
        blood_multiplier=10,000% (100x), defense=1,000,000.
        Ensures intermediate arithmetic handles large 64-bit values without precision loss or overflow.
        """
        res = validate_and_compute_spell_action(
            caster_max_hp=1_000_000,
            caster_current_hp=1_000_000,
            caster_magic_power=1_000_000,
            target_current_hp=1_000_000,
            target_defense=1_000_000,
            spell_hp_cost_percent=99,
            spell_base_damage=1_000_000,
            spell_blood_multiplier_percent=10_000,
        )

        assert res.hp_cost == 990_000
        assert res.raw_damage == 101_000_000  # 1M base + 1M pwr + (990k * 10k // 100) = 101M
        assert res.calculated_damage == 100_000_000  # 101M - 1M defense
        assert res.effective_damage == 1_000_000  # Capped at target HP
        assert res.actor_hp_after == 10_000
        assert res.target_hp_after == 0


