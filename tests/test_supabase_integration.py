import os
import uuid
import pytest
from blood_magic.application.dto import (
    CreateCharacterDTO,
    StartCombatDTO,
    CastSpellDTO,
    CancelCombatDTO,
)
from blood_magic.application.service import CombatApplicationService
from blood_magic.domain.exceptions import CombatDomainError, ErrorCode
from blood_magic.domain.models import CombatStatus
from blood_magic.infrastructure.supabase_client import get_supabase_client
from blood_magic.infrastructure.supabase_repository import SupabaseCombatRepository

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
TEST_USER_1_EMAIL = os.getenv("TEST_USER_1_EMAIL")
TEST_USER_1_PASSWORD = os.getenv("TEST_USER_1_PASSWORD")
TEST_USER_2_EMAIL = os.getenv("TEST_USER_2_EMAIL")
TEST_USER_2_PASSWORD = os.getenv("TEST_USER_2_PASSWORD")

SUPABASE_AVAILABLE = bool(
    SUPABASE_URL
    and SUPABASE_ANON_KEY
    and "YOUR_" not in SUPABASE_ANON_KEY
    and TEST_USER_1_EMAIL
    and TEST_USER_1_PASSWORD
    and TEST_USER_2_EMAIL
    and TEST_USER_2_PASSWORD
    and "YOUR_" not in TEST_USER_1_EMAIL
    and "YOUR_" not in TEST_USER_1_PASSWORD
    and "YOUR_" not in TEST_USER_2_EMAIL
    and "YOUR_" not in TEST_USER_2_PASSWORD
)


@pytest.fixture(scope="session")
def client_user_1():
    if not SUPABASE_AVAILABLE:
        pytest.skip("Supabase environment variables not configured")
    client = get_supabase_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    try:
        client.auth.sign_in_with_password({"email": TEST_USER_1_EMAIL, "password": TEST_USER_1_PASSWORD})
    except Exception as e:
        pytest.skip(f"Failed to authenticate User 1 on Supabase: {e}")
    return client


@pytest.fixture(scope="session")
def client_user_2():
    if not SUPABASE_AVAILABLE:
        pytest.skip("Supabase environment variables not configured")
    client = get_supabase_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    try:
        client.auth.sign_in_with_password({"email": TEST_USER_2_EMAIL, "password": TEST_USER_2_PASSWORD})
    except Exception as e:
        pytest.skip(f"Failed to authenticate User 2 on Supabase: {e}")
    return client


@pytest.fixture
def service_user_1(client_user_1):
    repo = SupabaseCombatRepository(client_user_1)
    return CombatApplicationService(repo)


@pytest.fixture
def service_user_2(client_user_2):
    repo = SupabaseCombatRepository(client_user_2)
    return CombatApplicationService(repo)


@pytest.mark.skipif(not SUPABASE_AVAILABLE, reason="Supabase instance not configured in environment")
class TestSupabaseCombatIntegration:
    """
    Live Supabase integration tests verifying atomic single-transaction RPC execution,
    idempotency, RLS, cross-user isolation, dead-locks, rollback, and state synchronization.
    """

    def test_full_combat_lifecycle_and_turn_alternation(self, service_user_1):
        repo = service_user_1.repository
        spells = repo.list_spells()
        assert len(spells) >= 3, "Catalog must have at least 3 seed spells"

        spear_spell = next(s for s in spells if s["code"] == "HAEMATIC_SPEAR_V1")
        pin_spell = next(s for s in spells if s["code"] == "BLOOD_PIN_V1")
        exsang_spell = next(s for s in spells if s["code"] == "EXSANGUINATION_V1")

        # Create two characters
        char_a = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"TesterA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=50,
                magic_power=5,
                defense=4,
                initiative=50,
            )
        )
        char_b = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"TesterB_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=50,
                magic_power=2,
                defense=4,
                initiative=10,
            )
        )

        # Start combat
        combat = service_user_1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        p1 = combat["participant_1"]
        p2 = combat["participant_2"]

        assert combat["current_turn_participant_id"] == p1["id"]

        # Turn 1: Actor A casts spell on Target B
        # Char A: max 100, cur 50, cost 12 -> after = 38
        # Char B: cur 50, calc dmg 27, eff dmg 27 -> after = 23
        req_1 = uuid.uuid4()
        res_turn_1 = service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=req_1,
                expected_version=0,
                actor_participant_id=uuid.UUID(p1["id"]),
                target_participant_id=uuid.UUID(p2["id"]),
                spell_id=uuid.UUID(spear_spell["id"]),
            )
        )

        assert res_turn_1.hp_cost == 12
        assert res_turn_1.actor_hp_after == 38
        assert res_turn_1.target_hp_after == 23
        assert res_turn_1.resulting_version == 1
        assert res_turn_1.resulting_status == CombatStatus.ACTIVE
        assert res_turn_1.next_turn_participant_id == uuid.UUID(p2["id"])

        # Check idempotency replay on Turn 1 (reconstructed from combat_actions)
        replay_res = service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=req_1,
                expected_version=0,
                actor_participant_id=uuid.UUID(p1["id"]),
                target_participant_id=uuid.UUID(p2["id"]),
                spell_id=uuid.UUID(spear_spell["id"]),
            )
        )
        assert replay_res.is_idempotent_replay is True
        assert replay_res.action_id == res_turn_1.action_id
        assert replay_res.actor_hp_after == 38
        assert replay_res.target_hp_after == 23

        # Verify idempotency conflict when request_id is reused with different payload
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.cast_spell(
                CastSpellDTO(
                    combat_id=combat_id,
                    request_id=req_1,
                    expected_version=1,
                    actor_participant_id=uuid.UUID(p2["id"]),
                    target_participant_id=uuid.UUID(p1["id"]),
                    spell_id=uuid.UUID(spear_spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.IDEMPOTENCY_CONFLICT

        # Verify stale combat version rejection
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.cast_spell(
                CastSpellDTO(
                    combat_id=combat_id,
                    request_id=uuid.uuid4(),
                    expected_version=0,  # Current is 1
                    actor_participant_id=uuid.UUID(p2["id"]),
                    target_participant_id=uuid.UUID(p1["id"]),
                    spell_id=uuid.UUID(spear_spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.STALE_COMBAT_VERSION

        # Turn 2 out-of-turn check: P1 tries to act on P2's turn
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.cast_spell(
                CastSpellDTO(
                    combat_id=combat_id,
                    request_id=uuid.uuid4(),
                    expected_version=1,
                    actor_participant_id=uuid.UUID(p1["id"]),
                    target_participant_id=uuid.UUID(p2["id"]),
                    spell_id=uuid.UUID(spear_spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.NOT_YOUR_TURN

        # Turn 2 valid alternation: P2 (HP 23) casts BLOOD_PIN_V1 (cost 5%) on P1 (HP 38)
        # Cost = 5 -> P2 HP = 18; Raw = 15 + 2 + (5 * 80 // 100) = 21; Calc = 21 - 4 = 17 -> P1 HP = 21
        res_turn_2 = service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=uuid.uuid4(),
                expected_version=1,
                actor_participant_id=uuid.UUID(p2["id"]),
                target_participant_id=uuid.UUID(p1["id"]),
                spell_id=uuid.UUID(pin_spell["id"]),
            )
        )
        assert res_turn_2.turn_number == 2
        assert res_turn_2.hp_cost == 5
        assert res_turn_2.actor_hp_after == 18
        assert res_turn_2.target_hp_after == 21
        assert res_turn_2.resulting_version == 2
        assert res_turn_2.resulting_status == CombatStatus.ACTIVE
        assert res_turn_2.next_turn_participant_id == uuid.UUID(p1["id"])

        # Turn 3 legal turn, but P1 has HP 21 and EXSANGUINATION_V1 costs 40 HP.
        # Must be rejected with INSUFFICIENT_HP without altering state.
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.cast_spell(
                CastSpellDTO(
                    combat_id=combat_id,
                    request_id=uuid.uuid4(),
                    expected_version=2,
                    actor_participant_id=uuid.UUID(p1["id"]),
                    target_participant_id=uuid.UUID(p2["id"]),
                    spell_id=uuid.UUID(exsang_spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.INSUFFICIENT_HP

        # Verify state was not modified after rejection
        combat_after_reject = repo.get_combat(combat_id)
        assert combat_after_reject["version"] == 2
        assert combat_after_reject["turn_number"] == 3
        assert combat_after_reject["current_turn_participant_id"] == p1["id"]
        assert repo.get_character(char_a.id)["current_hp"] == 21
        assert repo.get_character(char_b.id)["current_hp"] == 18
        assert len(repo.list_combat_actions(combat_id)) == 2

    def test_concurrent_active_combat_prevention(self, service_user_1):
        char_a = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"DuelistA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=20,
            )
        )
        char_b = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"DuelistB_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=10,
            )
        )
        char_c = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"DuelistC_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=15,
            )
        )

        service_user_1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )

        # Attempt to start another combat with char_a
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.start_combat(
                StartCombatDTO(character_a_id=char_a.id, character_b_id=char_c.id)
            )
        assert exc_info.value.code == ErrorCode.CHARACTER_ALREADY_IN_COMBAT

    def test_cancel_combat_and_hp_synchronization(self, service_user_1):
        repo = service_user_1.repository
        spells = repo.list_spells()
        spear_spell = next(s for s in spells if s["code"] == "HAEMATIC_SPEAR_V1")

        char_a = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"CancelA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=80,
                magic_power=10,
                defense=5,
                initiative=30,
            )
        )
        char_b = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"CancelB_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=80,
                magic_power=10,
                defense=5,
                initiative=10,
            )
        )

        combat = service_user_1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        p1_id = uuid.UUID(combat["participant_1"]["id"])
        p2_id = uuid.UUID(combat["participant_2"]["id"])

        # Cast 1 spell before cancelling so HP changes:
        # Cost = 12 -> P1 HP = 68; Raw = 8 + 10 + (12 * 150 // 100) = 36; Calc = 36 - 5 = 31 -> P2 HP = 49
        cast_res = service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=uuid.uuid4(),
                expected_version=0,
                actor_participant_id=p1_id,
                target_participant_id=p2_id,
                spell_id=uuid.UUID(spear_spell["id"]),
            )
        )
        assert cast_res.actor_hp_after == 68
        assert cast_res.target_hp_after == 49

        # Cancel combat
        cancel_res = service_user_1.cancel_combat(CancelCombatDTO(combat_id=combat_id))
        assert cancel_res["status"] == "cancelled"

        # Verify combat state
        combat_row = repo.get_combat(combat_id)
        assert combat_row["status"] == "cancelled"
        assert combat_row["current_turn_participant_id"] is None
        assert combat_row["winner_participant_id"] is None
        assert combat_row["ended_at"] is not None

        # Verify participant snapshots and characters keep exact post-action HP
        participants = repo.get_combat_participants(combat_id)
        part_1 = next(p for p in participants if p["id"] == str(p1_id))
        part_2 = next(p for p in participants if p["id"] == str(p2_id))
        assert part_1["current_hp"] == 68
        assert part_2["current_hp"] == 49
        assert repo.get_character(char_a.id)["current_hp"] == 68
        assert repo.get_character(char_b.id)["current_hp"] == 49

        # Subsequent actions or duplicate cancellations on cancelled combat must fail
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.cast_spell(
                CastSpellDTO(
                    combat_id=combat_id,
                    request_id=uuid.uuid4(),
                    expected_version=1,
                    actor_participant_id=p2_id,
                    target_participant_id=p1_id,
                    spell_id=uuid.UUID(spear_spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.COMBAT_NOT_ACTIVE

        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.cancel_combat(CancelCombatDTO(combat_id=combat_id))
        assert exc_info.value.code == ErrorCode.COMBAT_NOT_ACTIVE

    def test_rls_and_multi_user_isolation(self, service_user_1, service_user_2):
        """
        Confirms cross-user isolation: User 2 cannot access or mutate User 1's combat or characters.
        """
        spells = service_user_1.repository.list_spells()
        spell = spells[0]

        # User 1 creates two characters and starts a combat
        char_u1 = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"User1Char_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=20,
            )
        )
        char_u1_ally = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"User1Ally_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=10,
            )
        )
        combat_u1 = service_user_1.start_combat(
            StartCombatDTO(character_a_id=char_u1.id, character_b_id=char_u1_ally.id)
        )
        combat_u1_id = uuid.UUID(combat_u1["combat_id"])
        p1_id = uuid.UUID(combat_u1["participant_1"]["id"])
        p2_id = uuid.UUID(combat_u1["participant_2"]["id"])

        service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_u1_id,
                request_id=uuid.uuid4(),
                expected_version=0,
                actor_participant_id=p1_id,
                target_participant_id=p2_id,
                spell_id=uuid.UUID(spell["id"]),
            )
        )

        # User 2 creates character
        char_u2 = service_user_2.create_character(
            CreateCharacterDTO(
                name=f"User2Char_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=20,
            )
        )

        # RLS checks: User 2 cannot read User 1's character, combat, participants, or action history
        assert service_user_2.repository.get_character(char_u1.id) is None
        assert service_user_2.repository.get_combat(combat_u1_id) is None
        assert service_user_2.repository.get_combat_participants(combat_u1_id) == []
        assert service_user_2.repository.list_combat_actions(combat_u1_id) == []

        # User 2 cannot mutate User 1's combat via RPCs
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_2.cast_spell(
                CastSpellDTO(
                    combat_id=combat_u1_id,
                    request_id=uuid.uuid4(),
                    expected_version=1,
                    actor_participant_id=p2_id,
                    target_participant_id=p1_id,
                    spell_id=uuid.UUID(spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.FORBIDDEN

        with pytest.raises(CombatDomainError) as exc_info:
            service_user_2.cancel_combat(CancelCombatDTO(combat_id=combat_u1_id))
        assert exc_info.value.code == ErrorCode.FORBIDDEN

        # Cross-user combat start should fail with FORBIDDEN
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.start_combat(
                StartCombatDTO(character_a_id=char_u1_ally.id, character_b_id=char_u2.id)
            )
        assert exc_info.value.code == ErrorCode.FORBIDDEN

    def test_combat_victory_and_finished_state(self, service_user_1):
        """
        Confirms that when lethal damage reduces target HP to 0:
        1. Combat transitions to 'finished'.
        2. Winner is assigned to the attacking participant.
        3. current_turn_participant_id is set to None.
        4. ended_at timestamp is populated.
        5. Snapshots and characters tables are synchronized with 0 HP for target.
        """
        repo = service_user_1.repository
        spells = repo.list_spells()
        spell = spells[0]  # E.g. BLOOD_PIN_V1: base 15, cost 5%

        # Char A: HP 100/100, magic 10, initiative 50 (starts)
        char_a = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"VictorA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=50,
            )
        )
        # Char B: low HP so single blow is lethal (HP 10/10)
        char_b = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"FragileB_{uuid.uuid4().hex[:4]}",
                max_hp=10,
                current_hp=10,
                magic_power=0,
                defense=0,
                initiative=10,
            )
        )

        combat = service_user_1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        p1_id = uuid.UUID(combat["participant_1"]["id"])
        p2_id = uuid.UUID(combat["participant_2"]["id"])

        req_id = uuid.uuid4()
        res = service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=req_id,
                expected_version=0,
                actor_participant_id=p1_id,
                target_participant_id=p2_id,
                spell_id=uuid.UUID(spell["id"]),
            )
        )

        assert res.resulting_status == CombatStatus.FINISHED
        assert res.winner_participant_id == p1_id
        assert res.next_turn_participant_id is None
        assert res.target_hp_after == 0

        # Verify DB combat record
        combat_row = repo.get_combat(combat_id)
        assert combat_row["status"] == "finished"
        assert combat_row["winner_participant_id"] == str(p1_id)
        assert combat_row["current_turn_participant_id"] is None
        assert combat_row["ended_at"] is not None

        # Verify target character synchronized in characters table
        char_b_synced = repo.get_character(char_b.id)
        assert char_b_synced["current_hp"] == 0

    def test_idempotency_after_combat_finished(self, service_user_1):
        """
        Confirms idempotency replay even after combat is finished:
        1. A replayed winning request returns original result.
        2. A NEW request against finished combat is rejected with COMBAT_NOT_ACTIVE.
        """
        repo = service_user_1.repository
        spells = repo.list_spells()
        spell = spells[0]

        char_a = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"IdemFinA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=0,
                initiative=50,
            )
        )
        char_b = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"IdemFinB_{uuid.uuid4().hex[:4]}",
                max_hp=10,
                current_hp=10,
                magic_power=0,
                defense=0,
                initiative=10,
            )
        )

        combat = service_user_1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        p1_id = uuid.UUID(combat["participant_1"]["id"])
        p2_id = uuid.UUID(combat["participant_2"]["id"])

        win_req_id = uuid.uuid4()
        original_res = service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=win_req_id,
                expected_version=0,
                actor_participant_id=p1_id,
                target_participant_id=p2_id,
                spell_id=uuid.UUID(spell["id"]),
            )
        )
        assert original_res.resulting_status == CombatStatus.FINISHED

        # Idempotent replay on the finished combat
        replay_res = service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=win_req_id,
                expected_version=0,
                actor_participant_id=p1_id,
                target_participant_id=p2_id,
                spell_id=uuid.UUID(spell["id"]),
            )
        )
        assert replay_res.is_idempotent_replay is True
        assert replay_res.action_id == original_res.action_id
        assert replay_res.resulting_status == CombatStatus.FINISHED

        # New request on finished combat must be rejected with COMBAT_NOT_ACTIVE
        with pytest.raises(CombatDomainError) as exc_info:
            service_user_1.cast_spell(
                CastSpellDTO(
                    combat_id=combat_id,
                    request_id=uuid.uuid4(),
                    expected_version=1,
                    actor_participant_id=p1_id,
                    target_participant_id=p2_id,
                    spell_id=uuid.UUID(spell["id"]),
                )
            )
        assert exc_info.value.code == ErrorCode.COMBAT_NOT_ACTIVE

    def test_catalog_and_history_immutability(self, service_user_1):
        """
        Confirms immutability:
        Direct UPDATE or DELETE on spells and combat_actions is prohibited by triggers and permissions.
        """
        repo = service_user_1.repository
        spells = repo.list_spells()
        assert len(spells) > 0
        spell_id = spells[0]["id"]

        # Attempting direct update on spells should fail
        with pytest.raises(Exception):
            repo.client.table("spells").update({"name": "Hacked Name"}).eq("id", spell_id).execute()

        # Attempting direct delete on spells should fail
        with pytest.raises(Exception):
            repo.client.table("spells").delete().eq("id", spell_id).execute()

        # Create a combat and execute one action to verify combat_actions immutability
        char_a = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"ImmutA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=50,
            )
        )
        char_b = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"ImmutB_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=10,
            )
        )
        combat = service_user_1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        combat_id = uuid.UUID(combat["combat_id"])
        cast_res = service_user_1.cast_spell(
            CastSpellDTO(
                combat_id=combat_id,
                request_id=uuid.uuid4(),
                expected_version=0,
                actor_participant_id=uuid.UUID(combat["participant_1"]["id"]),
                target_participant_id=uuid.UUID(combat["participant_2"]["id"]),
                spell_id=uuid.UUID(spell_id),
            )
        )
        action_id = str(cast_res.action_id)

        with pytest.raises(Exception):
            repo.client.table("combat_actions").update({"hp_cost": 999}).eq("id", action_id).execute()

        with pytest.raises(Exception):
            repo.client.table("combat_actions").delete().eq("id", action_id).execute()

    def test_initiative_tie_breaks_by_smaller_uuid(self, service_user_1):
        """
        Confirms that when initiatives are equal, the character with the
        lexicographically smaller UUID begins the combat.
        """
        char_a = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"TieA_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=25,
            )
        )
        char_b = service_user_1.create_character(
            CreateCharacterDTO(
                name=f"TieB_{uuid.uuid4().hex[:4]}",
                max_hp=100,
                current_hp=100,
                magic_power=10,
                defense=5,
                initiative=25,
            )
        )
        combat = service_user_1.start_combat(
            StartCombatDTO(character_a_id=char_a.id, character_b_id=char_b.id)
        )
        expected_first_pid = (
            combat["participant_1"]["id"]
            if str(char_a.id) < str(char_b.id)
            else combat["participant_2"]["id"]
        )
        assert combat["current_turn_participant_id"] == expected_first_pid

