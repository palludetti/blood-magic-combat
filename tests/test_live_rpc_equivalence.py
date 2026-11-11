import os
import uuid
import pytest
from blood_magic.application.dto import (
    CreateCharacterDTO,
    StartCombatDTO,
    CastSpellDTO,
)
from blood_magic.application.service import CombatApplicationService
from blood_magic.domain.exceptions import CombatDomainError, ErrorCode
from blood_magic.domain.blood_math import validate_and_compute_spell_action
from blood_magic.infrastructure.supabase_client import get_supabase_client
from blood_magic.infrastructure.supabase_repository import SupabaseCombatRepository

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
TEST_USER_EMAIL = os.getenv("TEST_USER_1_EMAIL")
TEST_USER_PASSWORD = os.getenv("TEST_USER_1_PASSWORD")

SUPABASE_AVAILABLE = bool(
    SUPABASE_URL
    and SUPABASE_ANON_KEY
    and "YOUR_" not in SUPABASE_ANON_KEY
    and TEST_USER_EMAIL
    and TEST_USER_PASSWORD
    and "YOUR_" not in TEST_USER_EMAIL
    and "YOUR_" not in TEST_USER_PASSWORD
)


@pytest.fixture(scope="session")
def client_user():
    if not SUPABASE_AVAILABLE:
        pytest.skip("Supabase environment variables (SUPABASE_URL, SUPABASE_ANON_KEY, TEST_USER_1_*) not set")
    client = get_supabase_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    try:
        client.auth.sign_in_with_password({"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD})
    except Exception as e:
        pytest.skip(f"Failed to authenticate on Supabase: {e}")
    return client


@pytest.fixture
def combat_service(client_user):
    return CombatApplicationService(SupabaseCombatRepository(client_user))


@pytest.mark.skipif(not SUPABASE_AVAILABLE, reason="Supabase instance not configured in environment")
class TestLiveRpcPureMathEquivalence:
    """
    Testa a equivalência direta executando a RPC REAL fn_cast_spell
    contra a função pura Python validate_and_compute_spell_action.
    Compara:
      - hp_cost
      - raw_damage
      - calculated_damage
      - effective_damage
      - actor_hp_after
      - target_hp_after
      - persistência exata no banco após a execução
    """

    @pytest.mark.parametrize(
        "desc, spell_code, caster_max_hp, caster_cur_hp, caster_pwr, target_hp, target_def, cost_pct, base_dmg, mult_pct",
        [
            ("Exemplo obrigatorio", "HAEMATIC_SPEAR_V1", 100, 50, 5, 100, 4, 12, 8, 150),
            ("Arredondamento teto", "TEST_CEIL_ROUND_V1", 95, 60, 10, 80, 5, 10, 20, 100),
            ("Defesa reduz dano a zero", "TEST_ZERO_DMG_V1", 100, 70, 0, 90, 500, 10, 5, 50),
            ("Dano maior que HP restante (capping)", "TEST_OVERKILL_V1", 100, 80, 20, 15, 0, 15, 50, 200),
            ("Valores maximos permitidos", "TEST_MAX_VALS_V1", 1000000, 800000, 500000, 900000, 100000, 10, 200000, 5000),
        ],
    )
    def test_live_rpc_matches_python_pure_math(
        self,
        combat_service,
        desc,
        spell_code,
        caster_max_hp,
        caster_cur_hp,
        caster_pwr,
        target_hp,
        target_def,
        cost_pct,
        base_dmg,
        mult_pct,
    ):
        repo = combat_service.repository

        # 1. Obter feitiço pré-cadastrado no catálogo imutável
        spells_res = repo.client.table("spells").select("*").eq("code", spell_code).execute()
        if not spells_res.data:
            # Fallback: se TEST_DATABASE_URL estiver disponível, inserir como admin
            test_db_url = os.getenv("TEST_DATABASE_URL")
            if test_db_url and "YOUR_" not in test_db_url:
                try:
                    import psycopg
                    with psycopg.connect(test_db_url, autocommit=True) as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO public.spells (code, name, hp_cost_percent, base_damage, blood_multiplier_percent)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (code) DO NOTHING;
                            """, (spell_code, f"Spell {desc}", cost_pct, base_dmg, mult_pct))
                    spells_res = repo.client.table("spells").select("*").eq("code", spell_code).execute()
                except Exception:
                    pass

        if not spells_res.data:
            pytest.skip(f"Feitiço {spell_code} não encontrado no catálogo. Execute a migração 003_seed_spells.sql.")

        spell_id = uuid.UUID(spells_res.data[0]["id"])

        # 2. Criar personagens com os atributos conhecidos
        char_a = combat_service.create_character(
            CreateCharacterDTO(
                name=f"Caster_{uuid.uuid4().hex[:4]}",
                max_hp=caster_max_hp,
                current_hp=caster_cur_hp,
                magic_power=caster_pwr,
                defense=10,
                initiative=100,  # Garante primeiro turno
            )
        )
        char_b = combat_service.create_character(
            CreateCharacterDTO(
                name=f"Target_{uuid.uuid4().hex[:4]}",
                max_hp=1000000 if target_hp > 100000 else 100,
                current_hp=target_hp,
                magic_power=10,
                defense=target_def,
                initiative=10,
            )
        )

        # 3. Iniciar combate
        combat_data = combat_service.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat_data["combat_id"])
        actor_pid = uuid.UUID(combat_data["participant_1"]["id"])
        target_pid = uuid.UUID(combat_data["participant_2"]["id"])

        # 4. Calcular via função pura Python
        py_result = validate_and_compute_spell_action(
            caster_max_hp=caster_max_hp,
            caster_current_hp=caster_cur_hp,
            caster_magic_power=caster_pwr,
            target_current_hp=target_hp,
            target_defense=target_def,
            spell_hp_cost_percent=cost_pct,
            spell_base_damage=base_dmg,
            spell_blood_multiplier_percent=mult_pct,
        )

        # 5. Executar RPC real no PostgreSQL
        rpc_result = combat_service.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=uuid.uuid4(),
                expected_version=0,
                actor_participant_id=actor_pid,
                target_participant_id=target_pid,
                spell_id=spell_id,
            )
        )

        # 6. Comparar retorno da RPC com a função pura Python
        assert rpc_result.hp_cost == py_result.hp_cost
        assert rpc_result.raw_damage == py_result.raw_damage
        assert rpc_result.calculated_damage == py_result.calculated_damage
        assert rpc_result.effective_damage == py_result.effective_damage
        assert rpc_result.actor_hp_after == py_result.actor_hp_after
        assert rpc_result.target_hp_after == py_result.target_hp_after

        # 7. Comparar estado persistido no banco de dados
        participants = repo.get_combat_participants(combat_id)
        part_actor = next(p for p in participants if p["id"] == str(actor_pid))
        part_target = next(p for p in participants if p["id"] == str(target_pid))

        assert part_actor["current_hp"] == py_result.actor_hp_after
        assert part_target["current_hp"] == py_result.target_hp_after

        char_actor = repo.get_character(char_a.id)
        char_target = repo.get_character(char_b.id)
        assert char_actor["current_hp"] == py_result.actor_hp_after
        assert char_target["current_hp"] == py_result.target_hp_after

    def test_live_rpc_insufficient_hp_and_lethal_cost_no_state_change(self, combat_service):
        repo = combat_service.repository
        spells = repo.list_spells()
        spell = spells[0]
        cost_pct = spell["hp_cost_percent"]

        # Caster max HP = 100, cost = cost_pct
        # Teste 1: current_hp = cost - 1 -> INSUFFICIENT_HP
        cost = max(1, (100 * cost_pct + 99) // 100)
        cur_hp_insufficient = max(0, cost - 1)

        char_a = combat_service.create_character(
            CreateCharacterDTO(
                name="CasterInsuf",
                max_hp=100,
                current_hp=cur_hp_insufficient,
                magic_power=10,
                defense=5,
                initiative=50,
            )
        )
        char_b = combat_service.create_character(
            CreateCharacterDTO(
                name="TargetInsuf",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=10,
            )
        )

        if cur_hp_insufficient == 0:
            # Não pode iniciar se morto
            return

        combat = combat_service.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        actor_pid = uuid.UUID(combat["participant_1"]["id"])
        target_pid = uuid.UUID(combat["participant_2"]["id"])

        with pytest.raises(CombatDomainError) as exc_info:
            combat_service.cast_spell(
                CastSpellDTO(
                    combat_id=combat_id,
                    request_id=uuid.uuid4(),
                    expected_version=0,
                    actor_participant_id=actor_pid,
                    target_participant_id=target_pid,
                    spell_id=uuid.UUID(spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.INSUFFICIENT_HP

        # Verificar ausência total de alteração
        combat_row = repo.get_combat(combat_id)
        assert combat_row["version"] == 0
        assert combat_row["turn_number"] == 1
        assert combat_row["status"] == "active"
        participants = repo.get_combat_participants(combat_id)
        assert next(p for p in participants if p["id"] == str(actor_pid))["current_hp"] == cur_hp_insufficient
        assert next(p for p in participants if p["id"] == str(target_pid))["current_hp"] == 100
        assert repo.get_character(char_a.id)["current_hp"] == cur_hp_insufficient
        assert repo.get_character(char_b.id)["current_hp"] == 100
        actions = repo.list_combat_actions(combat_id)
        assert len(actions) == 0

        # Teste 2: current_hp == cost -> LETHAL_BLOOD_COST (usa novo oponente pois char_b segue em combate ativo)
        char_lethal = combat_service.create_character(
            CreateCharacterDTO(
                name=f"CasterLethal_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=cost,
                magic_power=10,
                defense=5,
                initiative=50,
            )
        )
        char_target_lethal = combat_service.create_character(
            CreateCharacterDTO(
                name=f"TargetLethal_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=10,
            )
        )
        combat_lethal = combat_service.start_combat(
            StartCombatDTO(character_a_id=char_lethal.id, character_b_id=char_target_lethal.id)
        )
        c_lethal_id = uuid.UUID(combat_lethal["combat_id"])
        act_lethal_id = uuid.UUID(combat_lethal["participant_1"]["id"])
        tgt_lethal_id = uuid.UUID(combat_lethal["participant_2"]["id"])

        with pytest.raises(CombatDomainError) as exc_info:
            combat_service.cast_spell(
                CastSpellDTO(
                    combat_id=c_lethal_id,
                    request_id=uuid.uuid4(),
                    expected_version=0,
                    actor_participant_id=act_lethal_id,
                    target_participant_id=tgt_lethal_id,
                    spell_id=uuid.UUID(spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.LETHAL_BLOOD_COST

        # Verificar ausência total de alteração
        combat_row = repo.get_combat(c_lethal_id)
        assert combat_row["version"] == 0
        assert combat_row["turn_number"] == 1
        assert combat_row["status"] == "active"
        participants_lethal = repo.get_combat_participants(c_lethal_id)
        assert next(p for p in participants_lethal if p["id"] == str(act_lethal_id))["current_hp"] == cost
        assert next(p for p in participants_lethal if p["id"] == str(tgt_lethal_id))["current_hp"] == 100
        assert repo.get_character(char_lethal.id)["current_hp"] == cost
        assert repo.get_character(char_target_lethal.id)["current_hp"] == 100
        actions = repo.list_combat_actions(c_lethal_id)
        assert len(actions) == 0

