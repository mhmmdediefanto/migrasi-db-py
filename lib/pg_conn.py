"""Koneksi PostgreSQL dari .env (termasuk tunnel 127.0.0.1:55432)."""
from __future__ import annotations

from lib.config import load_settings
from lib.db import connect_pg


def connect_pg_from_env() -> object:
    settings = load_settings()
    return connect_pg(settings["pg"])
