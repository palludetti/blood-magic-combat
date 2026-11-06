import os
import pytest

try:
    import psycopg
except ImportError:
    psycopg = None

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
IS_CONFIGURED = bool(TEST_DB_URL and psycopg)


@pytest.mark.skipif(not IS_CONFIGURED, reason="Exige TEST_DATABASE_URL e psycopg para consulta aos catálogos do PostgreSQL")
class TestEffectivePrivilegesCatalog:
    """
    Inspeciona os catálogos do PostgreSQL (information_schema / pg_proc / pg_class)
    para auditar os privilégios efetivos das tabelas e funções.
    """

    def test_table_privileges_for_authenticated_and_anon(self):
        with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                # 1. Verificar privilégios de tabela para 'authenticated'
                cur.execute("""
                    SELECT table_name, privilege_type 
                    FROM information_schema.table_privileges 
                    WHERE grantee = 'authenticated' 
                      AND table_schema = 'public'
                      AND table_name IN ('characters', 'combats', 'combat_participants', 'combat_actions', 'spells');
                """)
                privs_auth = cur.fetchall()
                priv_types_auth = {row[1] for row in privs_auth}
                
                # Usuários autenticados devem ter APENAS 'SELECT'
                assert priv_types_auth == {"SELECT"}, f"authenticated possui privilégios indevidos: {priv_types_auth}"

                # 2. Verificar privilégios de escrita para 'authenticated' (INSERT, UPDATE, DELETE devem ser zero)
                forbidden_privs = [row for row in privs_auth if row[1] in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')]
                assert len(forbidden_privs) == 0, f"Escrita direta permitida para authenticated: {forbidden_privs}"

                # 3. Verificar privilégios de 'anon' nas tabelas de dados privados
                cur.execute("""
                    SELECT table_name, privilege_type 
                    FROM information_schema.table_privileges 
                    WHERE grantee = 'anon' 
                      AND table_schema = 'public'
                      AND table_name IN ('characters', 'combats', 'combat_participants', 'combat_actions');
                """)
                anon_privs = cur.fetchall()
                assert len(anon_privs) == 0, f"anon possui acesso a tabelas restritas: {anon_privs}"

    def test_rpc_execution_privileges_and_internal_helper(self):
        with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                # 1. fn_calculate_blood_magic NÃO deve possuir EXECUTE para PUBLIC, anon ou authenticated
                cur.execute("""
                    SELECT r.rolname
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    CROSS JOIN pg_roles r
                    WHERE n.nspname = 'public'
                      AND p.proname = 'fn_calculate_blood_magic'
                      AND r.rolname IN ('public', 'anon', 'authenticated')
                      AND has_function_privilege(r.rolname, p.oid, 'EXECUTE');
                """)
                unauthorized_roles = cur.fetchall()
                assert len(unauthorized_roles) == 0, f"Função interna fn_calculate_blood_magic exposta para: {unauthorized_roles}"

                # 2. RPCs expostas devem ter EXECUTE para authenticated
                cur.execute("""
                    SELECT p.proname, has_function_privilege('authenticated', p.oid, 'EXECUTE')
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'public'
                      AND p.proname IN ('fn_create_character', 'fn_start_combat', 'fn_cast_spell', 'fn_cancel_combat');
                """)
                rpcs = cur.fetchall()
                assert len(rpcs) == 4
                for rpc_name, has_exec in rpcs:
                    assert has_exec is True, f"RPC {rpc_name} sem permissão de execução para authenticated"

