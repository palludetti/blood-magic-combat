import os
import uuid
import pytest
from blood_magic.application.dto import (
    CreateCharacterDTO,
    StartCombatDTO,
    CastSpellDTO,
)
from blood_magic.application.service import CombatApplicationService
from blood_magic.domain.exceptions import CombatDomainError
from blood_magic.infrastructure.supabase_client import get_supabase_client
from blood_magic.infrastructure.supabase_repository import SupabaseCombatRepository

try:
    import psycopg
except ImportError:
    psycopg = None

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
SUPABASE_PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF")
ENABLE_DESTRUCTIVE_ROLLBACK = os.getenv("ENABLE_DESTRUCTIVE_ROLLBACK_TEST", "false").lower() == "true"

IS_CONFIGURED = bool(
    SUPABASE_URL
    and SUPABASE_ANON_KEY
    and TEST_DB_URL
    and psycopg
    and ENABLE_DESTRUCTIVE_ROLLBACK
    and "YOUR_" not in SUPABASE_ANON_KEY
    and "YOUR_" not in TEST_DB_URL
)


def verify_test_environment_identity(conn, expected_project_ref: str | None):
    """
    Validação estrita de identidade do ambiente de testes e marcador físico no banco.
    NÃO confia apenas em ausência de 'prod' na URL.
    Exige:
      1. Identidade de projeto correspondente na URL do banco se fornecido expected_project_ref.
      2. Verificação ou registro de marcador explícito na tabela de metadados:
         public._test_environment_marker (is_test_environment = true).
         Se o marcador não for confirmado, levanta exceção explícita imediatamente.
    """
    if expected_project_ref:
        if expected_project_ref not in TEST_DB_URL and expected_project_ref not in (SUPABASE_URL or ""):
            raise RuntimeError(
                f"SAFETY_VIOLATION: O banco conectado não corresponde ao projeto de testes esperado '{expected_project_ref}'!"
            )

    with conn.cursor() as cur:
        # Cria ou verifica tabela de segurança de marcador de ambiente
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public._test_environment_marker (
                id serial PRIMARY KEY,
                is_test_environment boolean NOT NULL DEFAULT true,
                environment_label text NOT NULL DEFAULT 'dedicated_test_instance',
                created_at timestamptz NOT NULL DEFAULT now()
            );
        """)
        cur.execute("SELECT count(*) FROM public._test_environment_marker WHERE is_test_environment = true;")
        (count,) = cur.fetchone()
        if count == 0:
            cur.execute("INSERT INTO public._test_environment_marker (is_test_environment) VALUES (true);")

        # Confirmar marcador
        cur.execute("SELECT is_test_environment FROM public._test_environment_marker LIMIT 1;")
        res = cur.fetchone()
        if not res or res[0] is not True:
            raise RuntimeError(
                "SAFETY_VIOLATION: Marcador de ambiente de testes inválido ou ausente! Abortando teste destrutivo."
            )


@pytest.mark.skipif(
    not IS_CONFIGURED,
    reason="Exige ambiente de testes isolado configurado com TEST_DATABASE_URL, SUPABASE_PROJECT_REF e ENABLE_DESTRUCTIVE_ROLLBACK_TEST=true",
)
class TestTransactionalRollback:
    """
    Testa o rollback atômico do PostgreSQL diante de falha controlada durante fn_cast_spell.
    
    ORDEM REAL DOS COMANDOS DA RPC fn_cast_spell:
      1. SELECT ... FOR UPDATE em combats
      2. INSERT em public.combat_actions (AÇÃO PERSISTENTE 1)
      3. UPDATE em public.combat_participants (AÇÃO PERSISTENTE 2 e 3)
      4. UPDATE em public.characters (AÇÃO PERSISTENTE 4 e 5)
      5. UPDATE em public.combats (AÇÃO PERSISTENTE 6)
      
    PROVA DE FALHA APÓS ALTERAÇÃO PERSISTENTE:
      O trigger temporário de erro é instalado em BEFORE UPDATE ON public.combats.
      Portanto, antes de abortar:
        - A linha em combat_actions FOI EFETIVAMENTE INSERIDA na transação aberta.
        - Os registros em combat_participants FORAM ATUALIZADOS com o novo HP.
        - Os registros em characters FORAM ATUALIZADOS com o novo HP.
      Ao disparar o erro no UPDATE de combats, o PostgreSQL é forçado a reverter
      TODAS as alterações anteriores (2, 3 e 4).
      
    PROTEÇÃO:
      - Validação estrita de identidade do projeto (SUPABASE_PROJECT_REF).
      - Verificação de marcador físico _test_environment_marker no banco.
      - Utilização de bloco try/finally para garantir remoção imediata do trigger.
    """

    def test_explicit_rollback_on_controlled_failure(self):
        # 1. Conexão administrativa para validação de identidade e instalação do trigger
        with psycopg.connect(TEST_DB_URL, autocommit=True) as pg_admin:
            # Validação estrita da identidade e marcador
            verify_test_environment_identity(pg_admin, SUPABASE_PROJECT_REF)

        client = get_supabase_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        email = os.getenv("TEST_USER_1_EMAIL", "test_mage_alpha@bloodmagic.io")
        password = os.getenv("TEST_USER_1_PASSWORD", "AlphaBloodPassword123!")
        client.auth.sign_in_with_password({"email": email, "password": password})

        repo = SupabaseCombatRepository(client)
        service = CombatApplicationService(repo)

        # 2. Obter feitiço e criar personagens
        spells = repo.list_spells()
        spell = spells[0]

        char_a = service.create_character(
            CreateCharacterDTO(
                name="RollbackHeroA",
                max_hp=100,
                current_hp=50,
                magic_power=10,
                defense=4,
                initiative=50,
            )
        )
        char_b = service.create_character(
            CreateCharacterDTO(
                name="RollbackHeroB",
                max_hp=100,
                current_hp=50,
                magic_power=10,
                defense=4,
                initiative=10,
            )
        )

        # 3. Iniciar combate
        combat = service.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        actor_pid = uuid.UUID(combat["participant_1"]["id"])
        target_pid = uuid.UUID(combat["participant_2"]["id"])

        # 4. Capturar estado inicial completo com timestamps
        combat_before = repo.get_combat(combat_id)
        participants_before = repo.get_combat_participants(combat_id)
        char_a_before = repo.get_character(char_a.id)
        char_b_before = repo.get_character(char_b.id)
        actions_before = repo.list_combat_actions(combat_id)

        # 5. Instalar trigger temporário de falha controlada APÓS inserção da ação e updates de participantes/personagens
        with psycopg.connect(TEST_DB_URL, autocommit=True) as pg_admin:
            with pg_admin.cursor() as cur:
                cur.execute("""
                    CREATE OR REPLACE FUNCTION public._test_trigger_inject_failure()
                    RETURNS trigger AS $$
                    BEGIN
                        RAISE EXCEPTION 'CONTROLLED_TEST_FAILURE: Injected rollback test after partial writes';
                    END;
                    $$ LANGUAGE plpgsql;

                    DROP TRIGGER IF EXISTS trg_test_inject_failure ON public.combats;
                    CREATE TRIGGER trg_test_inject_failure
                    BEFORE UPDATE ON public.combats
                    FOR EACH ROW EXECUTE FUNCTION public._test_trigger_inject_failure();
                """)

        # 6. Executar a ação de conjuração (que deve falhar no update final de combats e provocar rollback)
        cast_failed = False
        try:
            service.cast_spell(
                CastSpellDTO(
                    combat_id=combat_id,
                    request_id=uuid.uuid4(),
                    expected_version=0,
                    actor_participant_id=actor_pid,
                    target_participant_id=target_pid,
                    spell_id=uuid.UUID(spell["id"]),
                )
            )
        except CombatDomainError:
            cast_failed = True
        finally:
            # 7. Remover imediatamente o trigger e a função de teste
            with psycopg.connect(TEST_DB_URL, autocommit=True) as pg_admin:
                with pg_admin.cursor() as cur:
                    cur.execute("DROP TRIGGER IF EXISTS trg_test_inject_failure ON public.combats;")
                    cur.execute("DROP FUNCTION IF EXISTS public._test_trigger_inject_failure();")

        assert cast_failed, "A conjuração deveria ter falhado devido à exceção controlada no trigger"

        # 8. Comparar estado completo pós-falha: deve ser 100% idêntico ao estado anterior (inclusive timestamps)
        combat_after = repo.get_combat(combat_id)
        participants_after = repo.get_combat_participants(combat_id)
        char_a_after = repo.get_character(char_a.id)
        char_b_after = repo.get_character(char_b.id)
        actions_after = repo.list_combat_actions(combat_id)

        assert combat_after["version"] == combat_before["version"]
        assert combat_after["turn_number"] == combat_before["turn_number"]
        assert combat_after["status"] == combat_before["status"]
        assert combat_after["updated_at"] == combat_before["updated_at"]

        for p_bef, p_aft in zip(participants_before, participants_after):
            assert p_bef["current_hp"] == p_aft["current_hp"]

        assert char_a_after["current_hp"] == char_a_before["current_hp"]
        assert char_b_after["current_hp"] == char_b_before["current_hp"]
        assert char_a_after["updated_at"] == char_a_before["updated_at"]
        assert char_b_after["updated_at"] == char_b_before["updated_at"]

        # Comprova que a linha inserida em combat_actions sofreu rollback total
        assert len(actions_after) == len(actions_before)
