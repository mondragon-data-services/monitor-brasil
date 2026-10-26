"""Extrator da API OData aberta do Ipeadata (sem autenticação)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from src.config import IPEA_URL_TEMPLATE
from src.contracts import validate_series
from src.ingestion.cache import FetchResult, with_disk_fallback
from src.ingestion.http_client import IngestionError, build_session, get_json


def parse_ipea_payload(payload: object) -> pd.DataFrame:
    """Converte ``{"value": [{"VALDATA": ..., "VALVALOR": ...}]}`` em DataFrame date/value."""
    if not isinstance(payload, dict) or "value" not in payload:
        raise IngestionError("payload Ipeadata inesperado")
    rows = payload["value"]
    if not rows:
        raise IngestionError("Ipeadata não devolveu linhas para a série")
    df = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            # VALDATA vem com fuso (-03:00); descartamos o fuso para alinhar com as demais séries
            "date": pd.to_datetime(df["VALDATA"], utc=True, errors="coerce").dt.tz_convert(None).dt.normalize(),
            "value": pd.to_numeric(df["VALVALOR"], errors="coerce"),
        }
    )
    return validate_series(out)


def fetch_ipea(code: str, session: requests.Session | None = None) -> pd.DataFrame:
    url = IPEA_URL_TEMPLATE.format(code=code)
    return parse_ipea_payload(get_json(url, session or build_session()))


def get_ipea_series(key: str, code: str, base: Path | None = None,
                    session: requests.Session | None = None) -> FetchResult:
    """Série do Ipeadata com fallback para Parquet; ``missing`` se a série não existir/vier vazia."""
    return with_disk_fallback(f"ipea_{key}", lambda: fetch_ipea(code, session), base)
