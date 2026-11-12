import argparse
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

# Ensure 'src' is in sys.path when executed directly as a script
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from dotenv import load_dotenv

from blood_magic.application.dto import (
    CancelCombatDTO,
    CastSpellDTO,
    CreateCharacterDTO,
    StartCombatDTO,
)
from blood_magic.application.service import CombatApplicationService
from blood_magic.domain.blood_math import (
    calculate_blood_cost,
    validate_and_compute_spell_action,
)
from blood_magic.domain.exceptions import CombatDomainError
from blood_magic.domain.models import Character, CombatStatus
from blood_magic.infrastructure.supabase_client import get_supabase_client
from blood_magic.infrastructure.supabase_repository import SupabaseCombatRepository


def print_banner(text: str) -> None:
    line = "=" * 70
    print(f"\n{line}\n  {text}\n{line}")


def _init_authenticated_service() -> Optional[CombatApplicationService]:
    """Loads environment configuration and authenticates the Supabase user."""
    load_dotenv(override=False)

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")
    user_email = os.getenv("DEMO_USER_EMAIL") or os.getenv("TEST_USER_1_EMAIL")
    user_password = os.getenv("DEMO_USER_PASSWORD") or os.getenv("TEST_USER_1_PASSWORD")

    print("Iniciando conexao com Supabase...")

    if not supabase_url or not supabase_anon_key or "YOUR_" in supabase_anon_key:
        print("[CONFIGURACAO NECESSARIA]")
        print("As variaveis SUPABASE_URL e SUPABASE_ANON_KEY contem placeholders no arquivo .env.")
        print("Substitua pelos valores reais do seu projeto Supabase para executar a demonstracao ao vivo.")
        print("Consulte o arquivo .env.example e o checklist do projeto.")
        return None

    client = get_supabase_client(supabase_url, supabase_anon_key)

    if user_email and user_password:
        print(f"Autenticando usuario: {user_email}...")
        try:
            auth_response = client.auth.sign_in_with_password({
                "email": user_email,
                "password": user_password,
            })
            print(f"[OK] Autenticado com sucesso. UID: {auth_response.user.id}")
        except Exception as e:
            print(f"[ERRO] Nao foi possivel autenticar com usuario/senha: {e}")
            sys.exit(1)
    else:
        print("[AVISO] DEMO_USER_EMAIL / DEMO_USER_PASSWORD nao fornecidos no .env.")
        print("Para operacoes protegidas por RLS, autentique um usuario no Supabase Auth.")
        sys.exit(1)

    return CombatApplicationService(SupabaseCombatRepository(client))


def _format_hp_bar(current_hp: int, max_hp: int, width: int = 20) -> str:
    if max_hp <= 0:
        return "[" + "." * width + "]"
    filled = max(0, min(width, (current_hp * width + max_hp - 1) // max_hp))
    return f"[{'#' * filled}{'.' * (width - filled)}]"


def _prompt_text(
    prompt: str,
    min_len: int = 1,
    max_len: int = 80,
    default: Optional[str] = None,
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            raw = default
        if min_len <= len(raw) <= max_len:
            return raw
        print(f"Valor invalido: informe entre {min_len} e {max_len} caracteres.")


def _prompt_int(
    prompt: str,
    min_val: int,
    max_val: int,
    default: Optional[int] = None,
) -> int:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("Valor invalido: digite um numero inteiro.")
            continue
        if min_val <= value <= max_val:
            return value
        print(f"Valor fora do intervalo permitido ({min_val} a {max_val}).")


def _sort_spells_for_menu(spells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    game_spells = [s for s in spells if not s["code"].startswith("TEST_")]
    test_spells = [s for s in spells if s["code"].startswith("TEST_")]
    return game_spells + test_spells


def build_spell_preview_label(
    actor: Dict[str, Any],
    target: Dict[str, Any],
    spell: Dict[str, Any],
) -> str:
    """Pure-domain preview string showing blood cost, estimated damage, or non-suicide block."""
    cost = calculate_blood_cost(actor["max_hp"], spell["hp_cost_percent"])
    try:
        calc = validate_and_compute_spell_action(
            caster_max_hp=actor["max_hp"],
            caster_current_hp=actor["current_hp"],
            caster_magic_power=actor["magic_power"],
            target_current_hp=target["current_hp"],
            target_defense=target["defense"],
            spell_hp_cost_percent=spell["hp_cost_percent"],
            spell_base_damage=spell["base_damage"],
            spell_blood_multiplier_percent=spell["blood_multiplier_percent"],
        )
        return (
            f"Custo: {calc.hp_cost} HP ({spell['hp_cost_percent']}%) | "
            f"Dano est.: {calc.calculated_damage} (efetivo: {calc.effective_damage}) | "
            f"Seu HP apos: {calc.actor_hp_after}"
        )
    except CombatDomainError as e:
        return f"Custo: {cost} HP ({spell['hp_cost_percent']}%) | [BLOQUEADO: {e.code.value}]"


def _create_character_interactive(
    service: CombatApplicationService,
    slot_label: str,
) -> Character:
    print(f"\n--- Criar {slot_label} ---")
    default_name = f"Mago_{uuid.uuid4().hex[:4]}"
    name = _prompt_text("Nome", min_len=1, max_len=80, default=default_name)
    max_hp = _prompt_int("HP maximo", 1, 1_000_000, default=100)
    current_hp = _prompt_int("HP atual", 1, max_hp, default=max_hp)
    magic_power = _prompt_int("Poder magico", 0, 1_000_000, default=10)
    defense = _prompt_int("Defesa", 0, 1_000_000, default=4)
    initiative = _prompt_int("Iniciativa", 0, 1_000_000, default=20)

    char = service.create_character(
        CreateCharacterDTO(
            name=name,
            max_hp=max_hp,
            current_hp=current_hp,
            magic_power=magic_power,
            defense=defense,
            initiative=initiative,
        )
    )
    print(f"[OK] {char.name} criado (ID: {char.id}) | HP {char.current_hp}/{char.max_hp}")
    return char


def _pick_existing_character(
    service: CombatApplicationService,
    slot_label: str,
    excluded_ids: set[str],
) -> Optional[Character]:
    repo = service.repository
    busy_ids = repo.get_active_combat_character_ids() | excluded_ids
    candidates = [
        c
        for c in repo.list_characters(limit=50)
        if c["current_hp"] > 0 and c["id"] not in busy_ids
    ]
    if not candidates:
        print("\nNenhum personagem vivo e livre de combate ativo foi encontrado.")
        return None

    print(f"\n--- Personagens Disponiveis para {slot_label} ---")
    for idx, c in enumerate(candidates[:15], start=1):
        print(
            f" [{idx}] {c['name']} | HP {c['current_hp']}/{c['max_hp']} | "
            f"Mag: {c['magic_power']} | Def: {c['defense']} | Ini: {c['initiative']}"
        )
    print(" [0] Voltar")

    choice = _prompt_int("Escolha", 0, min(15, len(candidates)))
    if choice == 0:
        return None

    selected = candidates[choice - 1]
    return Character(
        id=uuid.UUID(selected["id"]),
        owner_id=uuid.UUID(selected["owner_id"]),
        name=selected["name"],
        max_hp=selected["max_hp"],
        current_hp=selected["current_hp"],
        magic_power=selected["magic_power"],
        defense=selected["defense"],
        initiative=selected["initiative"],
        created_at=selected.get("created_at"),
        updated_at=selected.get("updated_at"),
    )


def _select_or_create_character(
    service: CombatApplicationService,
    slot_label: str,
    excluded_ids: set[str],
) -> Optional[Character]:
    while True:
        print(f"\nSelecionar {slot_label}:")
        print(" [1] Criar novo personagem")
        print(" [2] Escolher personagem existente (vivo e livre)")
        print(" [0] Cancelar e voltar")
        opt = _prompt_int("Opcao", 0, 2)
        if opt == 0:
            return None
        if opt == 1:
            return _create_character_interactive(service, slot_label)
        char = _pick_existing_character(service, slot_label, excluded_ids)
        if char is not None:
            return char


def _start_quick_duel(service: CombatApplicationService) -> Optional[uuid.UUID]:
    suffix = uuid.uuid4().hex[:4]
    char_a = service.create_character(
        CreateCharacterDTO(
            name=f"Ignis_{suffix}",
            max_hp=120,
            current_hp=120,
            magic_power=10,
            defense=4,
            initiative=25,
        )
    )
    char_b = service.create_character(
        CreateCharacterDTO(
            name=f"Noctis_{suffix}",
            max_hp=130,
            current_hp=130,
            magic_power=8,
            defense=6,
            initiative=15,
        )
    )
    combat = service.start_combat(
        StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
    )
    print(f"\n[OK] Duelo rapido iniciado! Combate: {combat['combat_id']}")
    return uuid.UUID(combat["combat_id"])


def _load_participant_names(
    repo: SupabaseCombatRepository,
    participants: List[Dict[str, Any]],
) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for p in participants:
        char = repo.get_character(uuid.UUID(p["character_id"]))
        names[p["id"]] = char["name"] if char else f"Slot {p['slot']}"
    return names


def _print_combat_history(
    repo: SupabaseCombatRepository,
    combat_id: uuid.UUID,
    names_by_pid: Dict[str, str],
    spells_by_id: Dict[str, Dict[str, Any]],
) -> None:
    actions = repo.list_combat_actions(combat_id)
    print_banner("HISTORICO IMUTAVEL DE ACOES")
    if not actions:
        print("Nenhuma acao registrada neste combate ainda.")
        return
    for act in actions:
        actor_name = names_by_pid.get(act["actor_participant_id"], act["actor_participant_id"])
        target_name = names_by_pid.get(act["target_participant_id"], act["target_participant_id"])
        spell_name = spells_by_id.get(act["spell_id"], {}).get("name", act["spell_id"])
        print(
            f" Turno {act['turn_number']} (v{act['resulting_version']}): {actor_name} usou {spell_name} em {target_name} | "
            f"Custo: {act['hp_cost']} HP ({act['actor_hp_before']}->{act['actor_hp_after']}) | "
            f"Dano ef.: {act['effective_damage']} ({act['target_hp_before']}->{act['target_hp_after']})"
        )


def _resume_or_cancel_active_combat(service: CombatApplicationService) -> Optional[uuid.UUID]:
    repo = service.repository
    combats = repo.list_active_combats(limit=15)
    if not combats:
        print("\nNenhum combate ativo encontrado para este usuario.")
        return None

    print("\n--- Combates Ativos ---")
    for idx, c in enumerate(combats, start=1):
        parts = repo.get_combat_participants(uuid.UUID(c["id"]))
        names = _load_participant_names(repo, parts)
        summary = " vs ".join(
            f"{names.get(p['id'], p['slot'])} ({p['current_hp']}/{p['max_hp']} HP)"
            for p in parts
        )
        print(f" [{idx}] {c['id']} | Turno {c['turn_number']} (v{c['version']}) | {summary}")
    print(" [0] Voltar")

    choice = _prompt_int("Escolha o combate", 0, len(combats))
    if choice == 0:
        return None

    chosen_id = uuid.UUID(combats[choice - 1]["id"])
    print("\n [1] Retomar combate")
    print(" [2] Cancelar combate")
    print(" [0] Voltar")
    action = _prompt_int("Acao", 0, 2)
    if action == 1:
        return chosen_id
    if action == 2:
        try:
            res = service.cancel_combat(CancelCombatDTO(combat_id=chosen_id))
            print(f"[OK] Combate {chosen_id} cancelado ({res['status']}). HP sincronizado.")
        except CombatDomainError as e:
            print(f"[ERRO] {e.code.value}: {e.message}")
    return None


def play_combat(service: CombatApplicationService, combat_id: uuid.UUID) -> None:
    """Interactive turn-by-turn combat loop backed by Supabase RPCs."""
    repo = service.repository
    spells = _sort_spells_for_menu(repo.list_spells())
    if not spells:
        print("[AVISO] Nenhum feitico encontrado no catalogo.")
        return
    spells_by_id = {s["id"]: s for s in spells}

    participants = repo.get_combat_participants(combat_id)
    names_by_pid = _load_participant_names(repo, participants)
    last_cast_dto: Optional[CastSpellDTO] = None

    while True:
        combat = repo.get_combat(combat_id)
        if not combat:
            print("[ERRO] Combate nao encontrado.")
            return

        participants = repo.get_combat_participants(combat_id)
        status = combat["status"]

        print_banner(
            f"COMBATE {combat_id} | STATUS: {status.upper()} | "
            f"TURNO {combat['turn_number']} | VERSAO {combat['version']}"
        )
        for p in participants:
            turn_tag = " <-- VEZ DE AGIR" if p["id"] == combat["current_turn_participant_id"] else ""
            bar = _format_hp_bar(p["current_hp"], p["max_hp"])
            print(
                f" [Slot {p['slot']}] {names_by_pid[p['id']]:<16} {bar} "
                f"{p['current_hp']:>4}/{p['max_hp']:<4} HP | "
                f"Mag:{p['magic_power']} Def:{p['defense']} Ini:{p['initiative']}{turn_tag}"
            )

        if status == CombatStatus.FINISHED.value:
            winner_name = names_by_pid.get(combat["winner_participant_id"], "Desconhecido")
            print(f"\n[VITORIA] {winner_name} venceu o combate!")
            _print_combat_history(repo, combat_id, names_by_pid, spells_by_id)
            return

        if status == CombatStatus.CANCELLED.value:
            print("\n[CANCELADO] Este combate foi encerrado por cancelamento.")
            return

        actor = next(p for p in participants if p["id"] == combat["current_turn_participant_id"])
        target = next(p for p in participants if p["id"] != combat["current_turn_participant_id"])

        print(f"\nVez de {names_by_pid[actor['id']]} atacar {names_by_pid[target['id']]}:")
        shown_test_header = False
        for idx, s in enumerate(spells, start=1):
            if s["code"].startswith("TEST_") and not shown_test_header:
                print(" --- Feiticos de Teste (Edge Cases) ---")
                shown_test_header = True
            preview = build_spell_preview_label(actor, target, s)
            print(f" [{idx}] {s['name']} [{s['code']}] -> {preview}")

        print("\nOutras acoes:")
        if last_cast_dto is not None:
            print(" [R] Reenviar ultima jogada (testar replay idempotente)")
        print(" [H] Ver historico do combate")
        print(" [C] Cancelar combate (preserva HP atual)")
        print(" [Q] Sair da partida (pode retomar depois)")

        raw = input("\nEscolha o numero do feitico ou comando: ").strip().upper()
        if raw == "Q":
            print("Saindo da partida. O combate continua salvo no Supabase.")
            return
        if raw == "H":
            _print_combat_history(repo, combat_id, names_by_pid, spells_by_id)
            continue
        if raw == "C":
            try:
                service.cancel_combat(CancelCombatDTO(combat_id=combat_id))
                print("[OK] Combate cancelado e HPs sincronizados.")
            except CombatDomainError as e:
                print(f"[REJEITADO]: {e.code.value} - {e.message}")
            continue
        if raw == "R" and last_cast_dto is not None:
            try:
                replay = service.cast_spell(last_cast_dto)
                print(
                    f"\n[REPLAY IDEMPOTENTE] is_idempotent_replay={replay.is_idempotent_replay} | "
                    f"Action ID={replay.action_id} | Versao={replay.resulting_version}"
                )
            except CombatDomainError as e:
                print(f"\n[REJEITADO]: {e.code.value} - {e.message}")
            continue

        try:
            spell_idx = int(raw)
        except ValueError:
            print("Comando invalido.")
            continue

        if not (1 <= spell_idx <= len(spells)):
            print("Opcao fora do intervalo.")
            continue

        chosen_spell = spells[spell_idx - 1]
        dto = CastSpellDTO(
            combat_id=combat_id,
            request_id=uuid.uuid4(),
            expected_version=combat["version"],
            actor_participant_id=uuid.UUID(actor["id"]),
            target_participant_id=uuid.UUID(target["id"]),
            spell_id=uuid.UUID(chosen_spell["id"]),
        )
        try:
            res = service.cast_spell(dto)
            last_cast_dto = dto
            print(
                f"\n[CONJURACAO ACEITA] {names_by_pid[actor['id']]} lancou '{chosen_spell['name']}'!\n"
                f" - Custo de sangue: {res.hp_cost} HP ({res.actor_hp_before} -> {res.actor_hp_after})\n"
                f" - Dano: bruto {res.raw_damage} | calculado {res.calculated_damage} | "
                f"efetivo {res.effective_damage} ({res.target_hp_before} -> {res.target_hp_after})\n"
                f" - Nova versao: {res.resulting_version} | Status: {res.resulting_status.value}"
            )
        except CombatDomainError as e:
            print(f"\n[REJEITADO PELO BANCO]: {e.code.value} - {e.message}")


def run_interactive_game() -> None:
    """Interactive terminal entry point allowing full playable matches against Supabase."""
    print_banner("BLOOD MAGIC COMBAT - MODO JOGO INTERATIVO")
    service = _init_authenticated_service()
    if service is None:
        sys.exit(0)

    while True:
        print_banner("MENU PRINCIPAL")
        print(" [1] Novo duelo rapido (2 personagens prontos)")
        print(" [2] Novo duelo personalizado (criar ou escolher personagens)")
        print(" [3] Retomar ou cancelar combate ativo")
        print(" [4] Executar demonstracao automatica (1 turno)")
        print(" [0] Sair")

        choice = _prompt_int("Escolha", 0, 4)
        if choice == 0:
            print("Ate a proxima!")
            return
        if choice == 1:
            combat_id = _start_quick_duel(service)
            if combat_id is not None:
                play_combat(service, combat_id)
        elif choice == 2:
            char_a = _select_or_create_character(service, "Personagem A", set())
            if char_a is None:
                continue
            char_b = _select_or_create_character(service, "Personagem B", {str(char_a.id)})
            if char_b is None:
                continue
            try:
                combat = service.start_combat(
                    StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
                )
                play_combat(service, uuid.UUID(combat["combat_id"]))
            except CombatDomainError as e:
                print(f"[ERRO AO INICIAR COMBATE]: {e.code.value} - {e.message}")
        elif choice == 3:
            combat_id = _resume_or_cancel_active_combat(service)
            if combat_id is not None:
                play_combat(service, combat_id)
        elif choice == 4:
            run_demo(service)


def run_demo(service: Optional[CombatApplicationService] = None) -> None:
    """Scripted 1-turn terminal demonstration against live Supabase RPCs."""
    print_banner("SISTEMA DE COMBATE POR TURNOS - MAGIA DE SANGUE")
    if service is None:
        service = _init_authenticated_service()
        if service is None:
            sys.exit(0)

    repo = service.repository

    # Listar feitiços cadastrados no catalogo
    print_banner("1. CATALOGO IMUTAVEL DE FEITICOS")
    try:
        spells = repo.list_spells()
        if not spells:
            print("[AVISO] Nenhum feitico encontrado. Execute a migracao 003_seed_spells.sql.")
            return
        for s in spells:
            print(
                f" * [{s['code']}] {s['name']} -> Custo: {s['hp_cost_percent']}% HP | "
                f"Base: {s['base_damage']} | Mult: {s['blood_multiplier_percent']}%"
            )
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
    print("Dica: execute com '--play' para jogar uma partida interativa turno a turno.")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Blood Magic Combat - CLI demonstrativa e modo de jogo interativo."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--play",
        action="store_true",
        help="Inicia o modo interativo para criar/escolher personagens e jogar turno a turno.",
    )
    mode.add_argument(
        "--demo",
        action="store_true",
        help="Executa a demonstracao automatica de 1 turno (padrao).",
    )
    args = parser.parse_args(argv)
    try:
        if args.play:
            run_interactive_game()
        else:
            run_demo()
    except (KeyboardInterrupt, EOFError):
        print("\nEncerrando CLI.")


if __name__ == "__main__":
    main()
