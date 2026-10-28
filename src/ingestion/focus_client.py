"""Boletim Focus (BCB, API Olinda): expectativas anuais do mercado para IPCA, Selic, PIB e câmbio.

Guardamos a evolução das medianas desde ``início da janela`` para o ano corrente e os dois seguintes. ``baseCalculo = 0``
é a base dos últimos 30 dias (a mesma do relatório semanal).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from src.config import FOCUS_INDICATORS, FOCUS_URL, FOCUS_YEARS_AHEAD
from src.ingestion.cache import FetchResult, with_disk_fallback
from src.ingestion.http_client import IngestionError, build_session, get_json

CACHE_KEY = "focus"
HISTORY_DAYS = 3 * 365


def build_focus_url(indicator: str, year: int, since: date) -> str:
    flt = (f"Indicador eq '{indicator}' and DataReferencia eq '{year}' and baseCalculo eq 0 and Data ge '{since.isoformat()}'")
    return (f"{FOCUS_URL}?$filter={quote(flt)}&$orderby=Data&$top=3000&$format=json"
            "&$select=Indicador,Data,DataReferencia,Mediana,Media,Minimo,Maximo,numeroRespondentes")


def parse_focus(payload: object, indicator: str) -> pd.DataFrame:
    """``{"value": [...]}`` do Olinda -> DataFrame ``date, indicador, ano, mediana, media, minimo, maximo, n``."""
    if not isinstance(payload, dict) or "value" not in payload:
        raise IngestionError("payload do Focus inesperado")
    rows = payload["value"]
    if not rows:
        return pd.DataFrame(columns=["date", "indicador", "ano", "mediana", "media", "minimo", "maximo", "n"])
    df = pd.DataFrame(rows)
    return pd.DataFrame({
        "date": pd.to_datetime(df["Data"], errors="coerce"), "indicador": indicator,
        "ano": pd.to_numeric(df["DataReferencia"], errors="coerce"),
        "mediana": pd.to_numeric(df["Mediana"], errors="coerce"), "media": pd.to_numeric(df["Media"], errors="coerce"),
        "minimo": pd.to_numeric(df["Minimo"], errors="coerce"), "maximo": pd.to_numeric(df["Maximo"], errors="coerce"),
        "n": pd.to_numeric(df["numeroRespondentes"], errors="coerce"),
    }).dropna(subset=["date", "ano", "mediana"])


def fetch_focus(session: requests.Session | None = None, today: date | None = None) -> pd.DataFrame:
    sess = session or build_session()
    now = today or date.today()
    since = now - timedelta(days=HISTORY_DAYS)
    frames = []
    for indicator in FOCUS_INDICATORS:
        for year in range(now.year, now.year + FOCUS_YEARS_AHEAD):
            frames.append(parse_focus(get_json(build_focus_url(indicator, year, since), sess), indicator))
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise IngestionError("Focus sem dados")
    return pd.concat(frames, ignore_index=True).sort_values(["indicador", "ano", "date"]).reset_index(drop=True)


def get_focus(base: Path | None = None, session: requests.Session | None = None) -> FetchResult:
    return with_disk_fallback(CACHE_KEY, lambda: fetch_focus(session), base)
