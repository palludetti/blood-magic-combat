from typing import Any, Dict, List, Optional
from uuid import UUID
from supabase import Client
from blood_magic.domain.exceptions import CombatDomainError
from blood_magic.infrastructure.error_parser import parse_rpc_error


class SupabaseCombatRepository:
    """
    Infrastructure layer interacting with Supabase via official client.
    Enforces that all mutations go through single atomic PostgreSQL RPC functions.
    Queries use RLS-filtered views/tables.
    """

    def __init__(self, client: Client):
        self._client = client

    @property
    def client(self) -> Client:
        return self._client

    def rpc_create_character(
        self,
        name: str,
        max_hp: int,
        current_hp: int,
        magic_power: int,
        defense: int,
        initiative: int,
    ) -> Dict[str, Any]:
        try:
            res = self._client.rpc(
                "fn_create_character",
                {
                    "p_name": name,
                    "p_max_hp": max_hp,
                    "p_current_hp": current_hp,
                    "p_magic_power": magic_power,
                    "p_defense": defense,
                    "p_initiative": initiative,
                },
            ).execute()
            return res.data
        except Exception as e:
            raise parse_rpc_error(e) from e

    def rpc_start_combat(
        self, character_a_id: UUID, character_b_id: UUID
    ) -> Dict[str, Any]:
        try:
            res = self._client.rpc(
                "fn_start_combat",
                {
                    "p_character_a_id": str(character_a_id),
                    "p_character_b_id": str(character_b_id),
                },
            ).execute()
            return res.data
        except Exception as e:
            raise parse_rpc_error(e) from e

    def rpc_cast_spell(
        self,
        combat_id: UUID,
        request_id: UUID,
        expected_version: int,
        actor_participant_id: UUID,
        target_participant_id: UUID,
        spell_id: UUID,
    ) -> Dict[str, Any]:
        try:
            res = self._client.rpc(
                "fn_cast_spell",
                {
                    "p_combat_id": str(combat_id),
                    "p_request_id": str(request_id),
                    "p_expected_version": expected_version,
                    "p_actor_participant_id": str(actor_participant_id),
                    "p_target_participant_id": str(target_participant_id),
                    "p_spell_id": str(spell_id),
                },
            ).execute()
            return res.data
        except Exception as e:
            raise parse_rpc_error(e) from e

    def rpc_cancel_combat(self, combat_id: UUID) -> Dict[str, Any]:
        try:
            res = self._client.rpc(
                "fn_cancel_combat",
                {"p_combat_id": str(combat_id)},
            ).execute()
            return res.data
        except Exception as e:
            raise parse_rpc_error(e) from e

    def get_character(self, character_id: UUID) -> Optional[Dict[str, Any]]:
        res = (
            self._client.table("characters")
            .select("*")
            .eq("id", str(character_id))
            .execute()
        )
        return res.data[0] if res.data else None

    def get_combat(self, combat_id: UUID) -> Optional[Dict[str, Any]]:
        res = (
            self._client.table("combats")
            .select("*")
            .eq("id", str(combat_id))
            .execute()
        )
        return res.data[0] if res.data else None

    def get_combat_participants(self, combat_id: UUID) -> List[Dict[str, Any]]:
        res = (
            self._client.table("combat_participants")
            .select("*")
            .eq("combat_id", str(combat_id))
            .order("slot")
            .execute()
        )
        return res.data

    def list_spells(self) -> List[Dict[str, Any]]:
        res = self._client.table("spells").select("*").order("name").execute()
        return res.data

    def list_combat_actions(self, combat_id: UUID) -> List[Dict[str, Any]]:
        res = (
            self._client.table("combat_actions")
            .select("*")
            .eq("combat_id", str(combat_id))
            .order("turn_number")
            .execute()
        )
        return res.data

