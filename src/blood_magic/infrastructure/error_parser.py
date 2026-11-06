from typing import Any, Dict
from postgrest.exceptions import APIError
from blood_magic.domain.exceptions import CombatDomainError, ErrorCode


KNOWN_ERROR_CODES = {code.value for code in ErrorCode}


def parse_rpc_error(exc: Exception) -> CombatDomainError:
    """
    Parses Postgres/PostgREST exceptions raised by Supabase RPC
    and maps them to strongly typed CombatDomainError with ErrorCode.
    """
    message = str(exc)

    if isinstance(exc, APIError):
        # PostgREST error format
        details = exc.message or ""
        hint = exc.hint or ""
        code_str = exc.code or ""
        combined = f"{details} {hint} {message}"
    else:
        combined = message

    # Check for our domain error code prefixes e.g. "INSUFFICIENT_HP: ..." or "[INSUFFICIENT_HP]"
    for code_value in KNOWN_ERROR_CODES:
        if code_value in combined:
            # Extract readable message if possible
            # Postgres RAISE EXCEPTION 'INSUFFICIENT_HP: message'
            idx = combined.find(code_value)
            extracted = combined[idx + len(code_value) :].lstrip(": ").rstrip()
            if not extracted:
                extracted = f"Operation failed with {code_value}"
            return CombatDomainError(ErrorCode(code_value), extracted)

    # Specific database constraint mappings if triggered directly
    if "23505" in combined or "duplicate key" in combined:
        return CombatDomainError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Unique constraint violation / Idempotency conflict",
        )

    return CombatDomainError(ErrorCode.FORBIDDEN, f"Database error: {message}")

