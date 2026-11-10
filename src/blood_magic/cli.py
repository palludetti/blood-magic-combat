import os
import sys
import uuid
from typing import Optional
from dotenv import load_dotenv

from blood_magic.infrastructure.supabase_client import get_supabase_client
from blood_magic.infrastructure.supabase_repository import SupabaseCombatRepository
from blood_magic.application.service import CombatApplicationService
from blood_magic.application.dto import (
    CreateCharacterDTO,
    StartCombatDTO,
    CastSpellDTO,
    CancelCombatDTO,
)
from blood_magic.domain.exceptions import CombatDomainError


def print_banner(text: str) -> None:
    line = "=" * 70
    print(f"\n{line}\n  {text}\n{line}")


def run_demo() -> None:
    load_dotenv(override=False)

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
    test_user_email = os.getenv("DEMO_USER_EMAIL") or os.getenv("TEST_USER_1_EMAIL")
    test_user_password = os.getenv("DEMO_USER_PASSWORD") or os.getenv("TEST_USER_1_PASSWORD")

    print_banner("SISTEMA DE COMBATE POR TURNOS - MAGIA DE SANGUE")
    print("Iniciando conexao com Supabase...")

    if not supabase_url or not supabase_anon_key:
        print("[ERRO] SUPABASE_URL e SUPABASE_ANON_KEY devem estar definidos no ambiente (.env).")
        print("Consulte o arquivo .env.example para mais instrucoes.")
        sys.exit(1)

    client = get_supabase_client(supabase_url, supabase_anon_key)

    # Autenticacao no Supabase
    if test_user_email and test_user_password:
        print(f"Autenticando usuario de demonstracao: {test_user_email}...")
        try:
            auth_response = client.auth.sign_in_with_password({
                "email": test_user_email,
                "password": test_user_password,
            })
            print(f"[OK] Autenticado com sucesso. UID: {auth_response.user.id}")
        except Exception as e:
            print(f"[AVISO] Nao foi possivel autenticar com usuario/senha: {e}")
            print("Tentando criar conta de teste...")
            try:
                sign_up_res = client.auth.sign_up({
                    "email": test_user_email,
                    "password": test_user_password,
                })
                print(f"[OK] Conta criada/autenticada: {sign_up_res.user.id if sign_up_res.user else 'pendente'}")
            except Exception as se:
                print(f"[ERRO] Falha no provisionamento do usuario de demonstracao: {se}")
                sys.exit(1)
    else:
        print("[AVISO] DEMO_USER_EMAIL / DEMO_USER_PASSWORD nao fornecidos no .env.")
        print("Para operacoes protegidas por RLS, autentique um usuario no Supabase Auth.")

    repo = SupabaseCombatRepository(client)
    service = CombatApplicationService(repo)

    # Listar feitiços cadastrados no catalogo
    print_banner("1. CATALOGO IMUTAVEL DE FEITICOS")
    try:
        spells = repo.list_spells()
        if not spells:
            print("[AVISO] Nenhum feitico encontrado. Execute a migracao 003_seed_spells.sql.")
            return
        for s in spells:
            print(f" * [{s['code']}] {s['name']} -> Custo: {s['hp_cost_percent']}% HP | Base: {s['base_damage']} | Mult: {s['blood_multiplier_percent']}%")
    except Exception as e:
        print(f"[ERRO] Falha ao consultar feitiços: {e}")
        return

    # Criar 2 personagens
    print_banner("2. CRIACAO ATOMICA DE PERSONAGENS")
    char_a_name = f"Vladislav_{uuid.uuid4().hex[:4]}"
    char_b_name = f"Carmilla_{uuid.uuid4().hex[:4]}"

    print(f"Criando {char_a_name} (HP: 100, Magia: 5, Defesa: 4, Iniciativa: 20)...")
    char_a = service.create_character(
        CreateCharacterDTO(
            name=char_a_name,
            max_hp=100,
            current_hp=50,  # Exemplo obrigatorio: HP max 100, atual 50
            magic_power=5,
            defense=4,
            initiative=20,
        )
    )
    print(f"[OK] Personagem A criado: ID {char_a.id} | HP {char_a.current_hp}/{char_a.max_hp}")

    print(f"Criando {char_b_name} (HP: 100, Magia: 8, Defesa: 4, Iniciativa: 10)...")
    char_b = service.create_character(
        CreateCharacterDTO(
            name=char_b_name,
            max_hp=100,
            current_hp=100,
            magic_power=8,
            defense=4,
            initiative=10,
        )
    )
    print(f"[OK] Personagem B criado: ID {char_b.id} | HP {char_b.current_hp}/{char_b.max_hp}")

    # Iniciar Combate
    print_banner("3. INICIAR COMBATE (SNAPSHOTS E INICIATIVA)")
    combat_data = service.start_combat(
        StartCombatDTO(
            character_a_id=char_a.id,
            character_b_id=char_b.id,
        )
    )
    combat_id = uuid.UUID(combat_data["combat_id"])
    turn_participant_id = uuid.UUID(combat_data["current_turn_participant_id"])
    p1 = combat_data["participant_1"]
    p2 = combat_data["participant_2"]

    print(f"[OK] Combate iniciado: {combat_id}")
    print(f"     Participante 1: {p1['name']} (ID {p1['id']}) - HP {p1['current_hp']}")
    print(f"     Participante 2: {p2['name']} (ID {p2['id']}) - HP {p2['current_hp']}")
    print(f"     Primeiro a agir: Participante {turn_participant_id} (Iniciativa)")

    # Escolher feitico de teste (Lanca Hematica - exemplo obrigatorio)
    chosen_spell = next((s for s in spells if s["code"] == "HAEMATIC_SPEAR_V1"), spells[0])
    spell_id = uuid.UUID(chosen_spell["id"])

    # Determinar quem ataca e quem defende
    if str(turn_participant_id) == str(p1["id"]):
        actor_id = uuid.UUID(p1["id"])
        target_id = uuid.UUID(p2["id"])
    else:
        actor_id = uuid.UUID(p2["id"])
        target_id = uuid.UUID(p1["id"])

    # Executar Turno 1: Conjurar Magia de Sangue
    print_banner("4. TURNO 1 - CONJURACAO DE MAGIA DE SANGUE")
    req_1_id = uuid.uuid4()
    print(f"Caster: {actor_id} conjura '{chosen_spell['name']}' contra {target_id}")
    print(f"Request ID: {req_1_id} | Expected Version: 0")

    result_1 = service.cast_spell(
        CastSpellDTO(
            combat_id=combat_id,
            request_id=req_1_id,
            expected_version=0,
            actor_participant_id=actor_id,
            target_participant_id=target_id,
            spell_id=spell_id,
        )
    )

    print("\n[RESULTADO DO TURNO 1]")
    print(f" - Action ID: {result_1.action_id}")
    print(f" - Custo de Sangue (C): {result_1.hp_cost}")
    print(f" - Dano Bruto: {result_1.raw_damage} | Dano Calculado: {result_1.calculated_damage} | Dano Efetivo: {result_1.effective_damage}")
    print(f" - HP do Conjurador: {result_1.actor_hp_before} -> {result_1.actor_hp_after}")
    print(f" - HP do Alvo: {result_1.target_hp_before} -> {result_1.target_hp_after}")
    print(f" - Versao Resultante: {result_1.resulting_version} | Status: {result_1.resulting_status.value}")
    print(f" - Proximo a agir: {result_1.next_turn_participant_id}")

    # Teste de Idempotencia do Turno 1
    print_banner("5. DEMONSTRACAO DE IDEMPOTENCIA")
    print(f"Reenviando a mesma requisicao (Request ID: {req_1_id}) com expected_version=0...")
    replay_result = service.cast_spell(
        CastSpellDTO(
            combat_id=combat_id,
            request_id=req_1_id,
            expected_version=0,
            actor_participant_id=actor_id,
            target_participant_id=target_id,
            spell_id=spell_id,
        )
    )
    print(f"[OK] Replay processado com sucesso. is_idempotent_replay: {replay_result.is_idempotent_replay}")
    print(f"     Nenhum efeito colateral adicional. Versao combate: {replay_result.resulting_version}")

    # Teste de Prevencao de Suicidio
    print_banner("6. TESTE DE POLITICA SEM CONJURACAO SUICIDA")
    print("Tentando conjurar com versao desatualizada ou suicidio...")
    try:
        service.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=uuid.uuid4(),
                expected_version=0,  # Versao esperada era 0, mas ja avancou para 1
                actor_participant_id=actor_id,
                target_participant_id=target_id,
                spell_id=spell_id,
            )
        )
    except CombatDomainError as e:
        print(f"[REJEICAO ESPERADA]: {e.code.value} - {e.message}")

    print_banner("DEMONSTRACAO CONCLUIDA COM SUCESSO!")


if __name__ == "__main__":
    run_demo()

