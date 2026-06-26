"""Revert koreksi stok S3 — kembalikan stok_akhir gudang ke nilai sebelum koreksi."""

from __future__ import annotations

from pathlib import Path

from reconcile_lib.stok_revert_skenario import run_revert_skenario

FEATURE_OUTPUT = Path(__file__).resolve().parent.parent / "output"


def run_revert_s3(pg, **kwargs) -> dict:
    return run_revert_skenario(pg, "S3", **kwargs)
