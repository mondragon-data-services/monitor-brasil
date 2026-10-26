"""Extrator da API SGS do Banco Central (janelas de no máximo 10 anos por requisição)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from src.config import BCB_URL_TEMPLATE, SGS_HISTORY_START, SGS_MAX_WINDOW_YEARS, SGS_SERIES
from src.contracts import validate_series
from src.ingestion.cache import FetchResult, with_disk_fallback
from src.ingestion.http_client import IngestionError, build_session, get_json


def date_windows(start: date, end: date, max_years: int = SGS_MAX_WINDOW_YEARS) -> list[tuple[date, date]]:
    """Divide [start, end] em janelas consecutivas de no máximo ``max_years`` anos.

    O BCB bloqueia consultas sem filtro de data e com janela > 10 anos (desde mar/2025).
    """
    if start > end:
        raise ValueError("start deve ser <= end")
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        try:
            limit = cursor.replace(year=cursor.year + max_years) - timedelta(days=1)
        except ValueError:  # 29/fev
            limit = cursor.replace(year=cursor.year + max_years, day=28) - timedelta(days=1)
        window_end = min(limit, end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def build_url(code: int, start: date, end: date) -> str:
    return BCB_URL_TEMPLATE.format(code=code, start=start.strftime("%d/%m/%Y"), end=end.strftime("%d/%m/%Y"))


def parse_sgs_payload(payload: object) -> pd.DataFrame:
    """Converte a resposta ``[{"data": "dd/mm/aaaa", "valor": "1.23"}]`` em DataFrame date/value."""
    if not isinstance(payload, list):
        raise IngestionError(f"payload SGS inesperado: {str(payload)[:120]}")
    if not payload:
        return pd.DataFrame({"date": pd.to_datetime([]), "value": pd.Series([], dtype=float)})
    df = pd.DataFrame(payload)
    if not {"data", "valor"} <= set(df.columns):
        raise IngestionError(f"colunas SGS inesperadas: {list(df.columns)}")
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["data"], format="%d/%m/%Y", errors="coerce"),
            "value": pd.to_numeric(df["valor"], errors="coerce"),
        }
    )
    return out.dropna()


def fetch_sgs(code: int, start: date, end: date, session: requests.Session | None = None) -> pd.DataFrame:
    """Baixa uma série SGS no intervalo, particionando em janelas de até 10 anos."""
    sess = session or build_session()
    frames = [
        parse_sgs_payload(get_json(build_url(code, w_start, w_end), sess))
        for w_start, w_end in date_windows(start, end)
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise IngestionError(f"SGS {code} não devolveu dados entre {start} e {end}")
    df = pd.concat(frames, ignore_index=True)
    df = df[df["date"].dt.date <= date.today()]  # descarta datas futuras
    return validate_series(df)


def get_bcb_series(key: str, base: Path | None = None, session: requests.Session | None = None) -> FetchResult:
    """Série SGS pelo nome lógico (ver ``config.SGS_SERIES``) com fallback para o Parquet em disco."""
    meta = SGS_SERIES[key]
    start = date.fromisoformat(SGS_HISTORY_START)
    return with_disk_fallback(key, lambda: fetch_sgs(meta.code, start, date.today(), session), base)
