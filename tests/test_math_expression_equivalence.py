import pytest
from blood_magic.domain.blood_math import (
    calculate_blood_cost,
    calculate_raw_damage,
    calculate_calculated_damage,
    calculate_effective_damage,
)


def simulated_sql_blood_cost(max_hp: int, p: int) -> int:
    """
    Python expression mirror of the PL/pgSQL expression:
    GREATEST(1::bigint, ((max_hp::bigint * p::bigint + 99::bigint) / 100::bigint))
    """
    return max(1, (max_hp * p + 99) // 100)


def simulated_sql_raw_damage(base_dmg: int, magic_pwr: int, hp_cost: int, mult: int) -> int:
    """
    Python expression mirror of the PL/pgSQL expression:
    base_damage::bigint + magic_power::bigint + ((hp_cost * mult::bigint) / 100::bigint)
    """
    return base_dmg + magic_pwr + (hp_cost * mult) // 100


def simulated_sql_calculated_damage(raw_damage: int, defense: int) -> int:
    """
    Python expression mirror of the PL/pgSQL expression:
    GREATEST(0::bigint, raw_damage - defense::bigint)
    """
    return max(0, raw_damage - defense)


def simulated_sql_effective_damage(target_hp: int, calculated_damage: int) -> int:
    """
    Python expression mirror of the PL/pgSQL expression:
    LEAST(target_hp::bigint, calculated_damage)
    """
    return min(target_hp, calculated_damage)


class TestMathExpressionEquivalence:
    """
    NOTICE: This unit test evaluates algorithm expression parity in pure Python.
    It does NOT connect to or execute inside PostgreSQL.
    For live SQL server execution against a real PostgreSQL instance, see
    `tests/test_live_postgresql_equivalence.py`.
    """

    @pytest.mark.parametrize(
        "max_hp, p",
        [
            (1, 1),
            (1, 100),
            (10, 1),
            (100, 12),
            (95, 10),
            (777, 33),
            (1000, 50),
            (1000000, 1),
            (1000000, 100),
        ],
    )
    def test_cost_expression_parity(self, max_hp: int, p: int):
        py_cost = calculate_blood_cost(max_hp, p)
        expr_cost = simulated_sql_blood_cost(max_hp, p)
        assert py_cost == expr_cost

    @pytest.mark.parametrize(
        "base, pwr, cost, mult",
        [
            (8, 5, 12, 150),
            (0, 0, 1, 0),
            (50, 20, 10, 200),
            (100, 50, 30, 300),
            (10000, 5000, 250, 10000),
        ],
    )
    def test_raw_damage_expression_parity(self, base: int, pwr: int, cost: int, mult: int):
        py_dmg = calculate_raw_damage(base, pwr, cost, mult)
        expr_dmg = simulated_sql_raw_damage(base, pwr, cost, mult)
        assert py_dmg == expr_dmg

    @pytest.mark.parametrize(
        "raw, defense",
        [
            (31, 4),
            (10, 20),
            (50, 50),
            (100, 0),
        ],
    )
    def test_calculated_damage_expression_parity(self, raw: int, defense: int):
        py_calc = calculate_calculated_damage(raw, defense)
        expr_calc = simulated_sql_calculated_damage(raw, defense)
        assert py_calc == expr_calc

    @pytest.mark.parametrize(
        "target_hp, calculated",
        [
            (100, 27),
            (15, 65),
            (0, 10),
            (50, 50),
        ],
    )
    def test_effective_damage_expression_parity(self, target_hp: int, calculated: int):
        py_eff = calculate_effective_damage(calculated, target_hp)
        expr_eff = simulated_sql_effective_damage(target_hp, calculated)
        assert py_eff == expr_eff
