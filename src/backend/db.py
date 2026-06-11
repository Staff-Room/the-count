"""
Storage dispatcher: BACKEND_STORE=sqlite (default, local dashboard) or
supabase (cloud sync target). Both stores expose the same function surface;
the supabase store is sync-oriented and does not support local Link flows.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

STORE = os.getenv("BACKEND_STORE", "sqlite").strip().lower()

if STORE == "supabase":
    from db_supabase import *  # noqa: F401,F403
else:
    STORE = "sqlite"
    from db_sqlite import *  # noqa: F401,F403
