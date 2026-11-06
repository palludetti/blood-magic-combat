import os
import pytest
from blood_magic.domain.blood_math import (
    calculate_blood_cost,
    calculate_raw_damage,
    calculate_calculated_damage,
    calculate_effective_damage,
)

POSTGRES_DB_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
POSTGRES_AVAILABLE = bool(POSTGRES_DB_URL)

try:
    import psycopg
except ImportError:
    psycopg = None


@pytest.fixture(scope="module")
def pg_conn():
    if not POSTGRES_AVAILABLE:
        pytest.skip("No real PostgreSQL instance configured (set TEST_DATABASE_URL or DATABASE_URL)")
    if psycopg is None:
        pytest.skip("psycopg driver not installed")

    conn = psycopg.connect(POSTGRES_DB_URL, autocommit=True)
    yield conn
    conn.close()


@pytest.mark.skipif(not POSTGRES_AVAILABLE, reason="PostgreSQL instance not configured in environment")
class TestLivePostgreSqlEquivalence:
    """
    Executes actual SQL queries directly in a real PostgreSQL engine
    and compares the resulting values with the Python pure functions.
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
    def test_live_postgres_blood_cost_equivalence(self, pg_conn, max_hp: int, p: int):
        py_cost = calculate_blood_cost(max_hp, p)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT GREATEST(1::bigint, ((%s::bigint * %s::bigint + 99::bigint) / 100::bigint))",
                (max_hp, p),
            )
            (pg_cost,) = cur.fetchone()

        assert py_cost == pg_cost

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
    def test_live_postgres_raw_damage_equivalence(self, pg_conn, base: int, pwr: int, cost: int, mult: int):
        py_dmg = calculate_raw_damage(base, pwr, cost, mult)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT %s::bigint + %s::bigint + ((%s::bigint * %s::bigint) / 100::bigint)",
                (base, pwr, cost, mult),
            )
            (pg_dmg,) = cur.fetchone()

        assert py_dmg == pg_dmg

    @pytest.mark.parametrize(
        "raw, defense",
        [
            (31, 4),
            (10, 20),
            (50, 50),
            (100, 0),
        ],
    )
    def test_live_postgres_calculated_damage_equivalence(self, pg_conn, raw: int, defense: int):
        py_calc = calculate_calculated_damage(raw, defense)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT GREATEST(0::bigint, %s::bigint - %s::bigint)",
                (raw, defense),
            )
            (pg_calc,) = cur.fetchone()

        assert py_calc == pg_calc

    @pytest.mark.parametrize(
        "target_hp, calculated",
        [
            (100, 27),
            (15, 65),
            (0, 10),
            (50, 50),
        ],
    )
    def test_live_postgres_effective_damage_equivalence(self, pg_conn, target_hp: int, calculated: int):
        py_eff = calculate_effective_damage(calculated, target_hp)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT LEAST(%s::bigint, %s::bigint)",
                (target_hp, calculated),
            )
            (pg_eff,) = cur.fetchone()

        assert py_eff == pg_eff

