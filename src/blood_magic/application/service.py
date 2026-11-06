from uuid import UUID
from blood_magic.application.dto import (
    CreateCharacterDTO,
    StartCombatDTO,
    CastSpellDTO,
    CastSpellResultDTO,
    CancelCombatDTO,
)
from blood_magic.domain.models import Character, CombatStatus
from blood_magic.infrastructure.supabase_repository import SupabaseCombatRepository


class CombatApplicationService:
    """
    Application Service orchestrating the use cases.
    Delegates atomic transactional execution to PostgreSQL RPC via the repository,
    guaranteeing atomic single-transaction state changes and full validation.
    """

    def __init__(self, repository: SupabaseCombatRepository):
        self._repo = repository

    @property
    def repository(self) -> SupabaseCombatRepository:
        return self._repo

    def create_character(self, dto: CreateCharacterDTO) -> Character:
        data = self._repo.rpc_create_character(
            name=dto.name,
            max_hp=dto.max_hp,
            current_hp=dto.current_hp,
            magic_power=dto.magic_power,
            defense=dto.defense,
            initiative=dto.initiative,
        )
        return Character(
            id=UUID(data["id"]),
            owner_id=UUID(data["owner_id"]),
            name=data["name"],
            max_hp=data["max_hp"],
            current_hp=data["current_hp"],
            magic_power=data["magic_power"],
            defense=data["defense"],
            initiative=data["initiative"],
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def start_combat(self, dto: StartCombatDTO) -> dict:
        data = self._repo.rpc_start_combat(
            character_a_id=dto.character_a_id,
            character_b_id=dto.character_b_id,
        )
        return data

    def cast_spell(self, dto: CastSpellDTO) -> CastSpellResultDTO:
        data = self._repo.rpc_cast_spell(
            combat_id=dto.combat_id,
            request_id=dto.request_id,
            expected_version=dto.expected_version,
            actor_participant_id=dto.actor_participant_id,
            target_participant_id=dto.target_participant_id,
            spell_id=dto.spell_id,
        )

        return CastSpellResultDTO(
            action_id=UUID(data["action_id"]),
            hp_cost=data["hp_cost"],
            raw_damage=data["raw_damage"],
            calculated_damage=data["calculated_damage"],
            effective_damage=data["effective_damage"],
            actor_hp_before=data["actor_hp_before"],
            actor_hp_after=data["actor_hp_after"],
            target_hp_before=data["target_hp_before"],
            target_hp_after=data["target_hp_after"],
            turn_number=data["turn_number"],
            resulting_version=data["resulting_version"],
            resulting_status=CombatStatus(data["resulting_status"]),
            next_turn_participant_id=UUID(data["next_turn_participant_id"])
            if data.get("next_turn_participant_id")
            else None,
            winner_participant_id=UUID(data["winner_participant_id"])
            if data.get("winner_participant_id")
            else None,
            is_idempotent_replay=data.get("is_idempotent_replay", False),
        )

    def cancel_combat(self, dto: CancelCombatDTO) -> dict:
        return self._repo.rpc_cancel_combat(combat_id=dto.combat_id)

