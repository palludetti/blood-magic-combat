from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from blood_magic.domain.models import CombatStatus


class CreateCharacterDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=80)
    max_hp: int = Field(ge=1, le=1000000)
    current_hp: int = Field(ge=0, le=1000000)
    magic_power: int = Field(ge=0, le=1000000)
    defense: int = Field(ge=0, le=1000000)
    initiative: int = Field(ge=0, le=1000000)


class StartCombatDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    character_a_id: UUID
    character_b_id: UUID


class CastSpellDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    combat_id: UUID
    request_id: UUID
    expected_version: int = Field(ge=0)
    actor_participant_id: UUID
    target_participant_id: UUID
    spell_id: UUID


class CancelCombatDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    combat_id: UUID


class CastSpellResultDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: UUID
    hp_cost: int
    raw_damage: int
    calculated_damage: int
    effective_damage: int
    actor_hp_before: int
    actor_hp_after: int
    target_hp_before: int
    target_hp_after: int
    turn_number: int
    resulting_version: int
    resulting_status: CombatStatus
    next_turn_participant_id: Optional[UUID] = None
    winner_participant_id: Optional[UUID] = None
    is_idempotent_replay: bool = False


class CombatDetailDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    combat_id: UUID
    owner_id: UUID
    status: CombatStatus
    turn_number: int
    version: int
    current_turn_participant_id: Optional[UUID] = None
    winner_participant_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

