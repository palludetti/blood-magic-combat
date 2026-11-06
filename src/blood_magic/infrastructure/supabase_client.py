import os
from dotenv import load_dotenv
from supabase import Client, create_client


def get_supabase_client(
    supabase_url: str | None = None,
    supabase_key: str | None = None,
) -> Client:
    """
    Initializes Supabase Client.
    SECURITY NOTICE: Uses anonymous / public key (anon key) with JWT auth or test user tokens.
    NEVER use or expose service_role key to clients or store in repo.
    """
    load_dotenv()

    url = supabase_url or os.getenv("SUPABASE_URL")
    key = supabase_key or os.getenv("SUPABASE_ANON_KEY")

    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be provided via arguments or environment variables."
        )

    return create_client(url, key)

