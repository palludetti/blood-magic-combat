from enum import Enum


class ErrorCode(str, Enum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    COMBAT_NOT_FOUND = "COMBAT_NOT_FOUND"
    COMBAT_NOT_ACTIVE = "COMBAT_NOT_ACTIVE"
    INVALID_PARTICIPANT = "INVALID_PARTICIPANT"
    NOT_YOUR_TURN = "NOT_YOUR_TURN"
    CHARACTER_DEAD = "CHARACTER_DEAD"
    SPELL_NOT_FOUND = "SPELL_NOT_FOUND"
    INSUFFICIENT_HP = "INSUFFICIENT_HP"
    LETHAL_BLOOD_COST = "LETHAL_BLOOD_COST"
    STALE_COMBAT_VERSION = "STALE_COMBAT_VERSION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CHARACTER_ALREADY_IN_COMBAT = "CHARACTER_ALREADY_IN_COMBAT"


class CombatDomainError(Exception):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(f"[{code.value}] {message}")
        self.code = code
        self.message = message

