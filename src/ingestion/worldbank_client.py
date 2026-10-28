"""Indicadores nacionais de saúde do Banco Mundial (fonte secundária: OMS, UNICEF, ONU).

Usado onde não há API oficial simples do Ministério da Saúde: cobertura vacinal (sarampo, DTP), expectativa de vida e gasto
em saúde. Resposta: ``[meta, [{"date": "2024", "value": 93, ...}]]``; valores ``null`` (anos sem dado) são descartados.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from src.config import WORLD_BANK_INDICATORS, WORLD_BANK_URL
from src.contracts import validate_series
from src.ingestion.cache import FetchResult, with_disk_fallback
from src.ingestion.http_client import IngestionError, build_session, get_json


def parse_world_bank(payload: object) -> pd.DataFrame:
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise IngestionError("resposta inesperada do Banco Mundial")
    rows = [r for r in payload[1] if r.get("value") is not None]
    if not rows:
        raise IngestionError("Banco Mundial sem valores para o indicador")
    df = pd.DataFrame({"date": pd.to_datetime([f"{r['date']}-01-01" for r in rows], errors="coerce"),
                       "value": [float(r["value"]) for r in rows]})
    return validate_series(df)


def fetch_world_bank(key: str, session: requests.Session | None = None) -> pd.DataFrame:
    url = WORLD_BANK_URL.format(indicator=WORLD_BANK_INDICATORS[key])
    return parse_world_bank(get_json(url, session or build_session()))


def get_world_bank(key: str, base: Path | None = None, session: requests.Session | None = None) -> FetchResult:
    return with_disk_fallback(f"wb_{key}", lambda: fetch_world_bank(key, session), base)
