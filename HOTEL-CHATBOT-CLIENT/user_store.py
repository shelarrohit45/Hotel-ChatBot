"""Load Mongo user helpers from the MCP package."""

from __future__ import annotations

import sys
from typing import Optional

from config import get_settings


def _catalog_db():
    root = str(get_settings().mcp_server_cwd)
    if root not in sys.path:
        sys.path.insert(0, root)
    import db as catalog_db

    catalog_db.init_db()
    return catalog_db


def upsert_guest(name: str, email: str, phone: str) -> dict:
    return _catalog_db().upsert_guest(name, email, phone)


def public_user(doc) -> Optional[dict]:
    return _catalog_db().public_user(doc)


def load_login_session(session_id: str) -> Optional[dict]:
    return _catalog_db().load_login_session(session_id)


def save_login_session(session_id: str, user: dict) -> None:
    _catalog_db().save_login_session(session_id, user)


def delete_login_session(session_id: str) -> None:
    _catalog_db().delete_login_session(session_id)
