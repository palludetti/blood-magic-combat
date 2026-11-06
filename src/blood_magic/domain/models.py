from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class CombatStatus(str, Enum):
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class Character(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    owner_id: UUID
    name: str = Field(min_length=1, max_length=80)
    max_hp: int = Field(ge=1, le=1000000)
    current_hp: int = Field(ge=0, le=1000000)
    magic_power: int = Field(ge=0, le=1000000)
    defense: int = Field(ge=0, le=1000000)
    initiative: int = Field(ge=0, le=1000000)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Spell(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    hp_cost_percent: int = Field(ge=1, le=100)
    base_damage: int = Field(ge=0, le=1000000)
    blood_multiplier_percent: int = Field(ge=0, le=10000)
    created_at: Optional[datetime] = None


class CombatParticipant(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    combat_id: UUID
    character_id: UUID
    slot: int = Field(ge=1, le=2)
    max_hp: int = Field(ge=1, le=1000000)
    current_hp: int = Field(ge=0, le=1000000)
    magic_power: int = Field(ge=0, le=1000000)
    defense: int = Field(ge=0, le=1000000)
    initiative: int = Field(ge=0, le=1000000)
    created_at: Optional[datetime] = None


class Combat(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    owner_id: UUID
    status: CombatStatus
    turn_number: int = Field(ge=1)
    version: int = Field(ge=0)
    current_turn_participant_id: Optional[UUID] = None
    winner_participant_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class CombatAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    combat_id: UUID
    request_id: UUID
    expected_version: int = Field(ge=0)
    turn_number: int = Field(ge=1)
    actor_participant_id: UUID
    target_participant_id: UUID
    spell_id: UUID
    hp_cost: int = Field(ge=1)
    raw_damage: int = Field(ge=0)
    calculated_damage: int = Field(ge=0)
    effective_damage: int = Field(ge=0)
    actor_hp_before: int
    actor_hp_after: int
    target_hp_before: int
    target_hp_after: int
    resulting_version: int
    resulting_status: CombatStatus
    next_turn_participant_id: Optional[UUID] = None
    winner_participant_id: Optional[UUID] = None
    created_at: Optional[datetime] = None

