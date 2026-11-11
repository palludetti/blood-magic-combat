import os
import threading
import uuid
import pytest
from concurrent.futures import ThreadPoolExecutor
from blood_magic.application.dto import (
    CreateCharacterDTO,
    StartCombatDTO,
    CastSpellDTO,
)
from blood_magic.application.service import CombatApplicationService
from blood_magic.domain.exceptions import CombatDomainError, ErrorCode
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


def get_fresh_authenticated_service():
    client = get_supabase_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.auth.sign_in_with_password({"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD})
    repo = SupabaseCombatRepository(client)
    # Warm up PostgREST client before barrier synchronization so threads race on the RPC itself
    repo.list_spells()
    return CombatApplicationService(repo)


@pytest.mark.skipif(not SUPABASE_AVAILABLE, reason="Supabase instance not configured in environment")
class TestConcurrencyAndRollback:
    """
    Independent concurrent sessions testing race conditions and PostgreSQL atomicity:
    1. Two different actions competing for the same expected_version (only one succeeds).
    2. Two simultaneous calls with identical request_id (exactly one action persisted).
    3. Two concurrent combat starts with the same character (only one created).
    """

    def test_concurrent_actions_same_expected_version(self):
        svc1 = get_fresh_authenticated_service()
        svc2 = get_fresh_authenticated_service()

        repo = svc1.repository
        spells = repo.list_spells()
        spell = spells[0]

        char_a = svc1.create_character(
            CreateCharacterDTO(
                name=f"RacerA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=4,
                initiative=50,
            )
        )
        char_b = svc1.create_character(
            CreateCharacterDTO(
                name=f"RacerB_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=5,
                defense=4,
                initiative=10,
            )
        )

        combat = svc1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        p1_id = uuid.UUID(combat["participant_1"]["id"])
        p2_id = uuid.UUID(combat["participant_2"]["id"])

        dto1 = CastSpellDTO(
            combat_id=combat_id,
            request_id=uuid.uuid4(),
            expected_version=0,
            actor_participant_id=p1_id,
            target_participant_id=p2_id,
            spell_id=uuid.UUID(spell["id"]),
        )
        dto2 = CastSpellDTO(
            combat_id=combat_id,
            request_id=uuid.uuid4(),
            expected_version=0,
            actor_participant_id=p1_id,
            target_participant_id=p2_id,
            spell_id=uuid.UUID(spell["id"]),
        )

        barrier = threading.Barrier(2, timeout=15)
        lock = threading.Lock()
        results = []
        errors = []

        def execute_cast(svc, dto):
            barrier.wait()
            try:
                res = svc.cast_spell(dto)
                with lock:
                    results.append(res)
            except CombatDomainError as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(execute_cast, svc1, dto1)
            f2 = executor.submit(execute_cast, svc2, dto2)
            f1.result()
            f2.result()

        # Exactly one action must succeed; the other must be rejected with STALE_COMBAT_VERSION
        assert len(results) == 1
        assert len(errors) == 1
        assert errors[0].code == ErrorCode.STALE_COMBAT_VERSION
        assert len(repo.list_combat_actions(combat_id)) == 1

    def test_simultaneous_identical_request_id(self):
        svc1 = get_fresh_authenticated_service()
        svc2 = get_fresh_authenticated_service()

        repo = svc1.repository
        spells = repo.list_spells()
        spell = spells[0]

        char_a = svc1.create_character(
            CreateCharacterDTO(
                name=f"IdemA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=4,
                initiative=50,
            )
        )
        char_b = svc1.create_character(
            CreateCharacterDTO(
                name=f"IdemB_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=5,
                defense=4,
                initiative=10,
            )
        )

        combat = svc1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        p1_id = uuid.UUID(combat["participant_1"]["id"])
        p2_id = uuid.UUID(combat["participant_2"]["id"])

        shared_req_id = uuid.uuid4()
        dto = CastSpellDTO(
            combat_id=combat_id,
            request_id=shared_req_id,
            expected_version=0,
            actor_participant_id=p1_id,
            target_participant_id=p2_id,
            spell_id=uuid.UUID(spell["id"]),
        )

        barrier = threading.Barrier(2, timeout=15)
        lock = threading.Lock()
        results = []

        def execute_cast(svc):
            barrier.wait()
            res = svc.cast_spell(dto)
            with lock:
                results.append(res)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(execute_cast, svc1)
            f2 = executor.submit(execute_cast, svc2)
            f1.result()
            f2.result()

        # Both calls succeed with identical action payload; one fresh, one idempotent replay
        assert len(results) == 2
        r0, r1 = results[0], results[1]
        assert r0.action_id == r1.action_id
        assert r0.hp_cost == r1.hp_cost
        assert r0.raw_damage == r1.raw_damage
        assert r0.calculated_damage == r1.calculated_damage
        assert r0.effective_damage == r1.effective_damage
        assert r0.actor_hp_before == r1.actor_hp_before
        assert r0.actor_hp_after == r1.actor_hp_after
        assert r0.target_hp_before == r1.target_hp_before
        assert r0.target_hp_after == r1.target_hp_after
        assert r0.resulting_version == r1.resulting_version == 1
        assert r0.resulting_status == r1.resulting_status
        assert {r0.is_idempotent_replay, r1.is_idempotent_replay} == {False, True}
        # Exactly one persisted action row in combat_actions
        actions = repo.list_combat_actions(combat_id)
        assert len(actions) == 1

    def test_concurrent_combat_creation_conflict(self):
        svc1 = get_fresh_authenticated_service()
        svc2 = get_fresh_authenticated_service()

        char_a = svc1.create_character(
            CreateCharacterDTO(
                name=f"SharedHero_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=20,
            )
        )
        char_b = svc1.create_character(
            CreateCharacterDTO(
                name=f"Opponent1_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=10,
            )
        )
        char_c = svc1.create_character(
            CreateCharacterDTO(
                name=f"Opponent2_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=15,
            )
        )

        barrier = threading.Barrier(2, timeout=15)
        lock = threading.Lock()
        results = []
        errors = []

        def start_c1():
            barrier.wait()
            try:
                res = svc1.start_combat(StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id))
                with lock:
                    results.append(res)
            except CombatDomainError as e:
                with lock:
                    errors.append(e)

        def start_c2():
            barrier.wait()
            try:
                res = svc2.start_combat(StartCombatDTO(character_a_id=char_a.id, character_b_id=char_c.id))
                with lock:
                    results.append(res)
            except CombatDomainError as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(start_c1)
            f2 = executor.submit(start_c2)
            f1.result()
            f2.result()

        # Exactly one succeeds; the other is rejected with CHARACTER_ALREADY_IN_COMBAT
        assert len(results) == 1
        assert len(errors) == 1
        assert errors[0].code == ErrorCode.CHARACTER_ALREADY_IN_COMBAT

