"""Extrator da API OData aberta do Ipeadata (sem autenticação)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from src.config import IPEA_TIMEOUT, IPEA_URL_TEMPLATE
from src.contracts import validate_series
from src.ingestion.cache import FetchResult, with_disk_fallback
from src.ingestion.http_client import IngestionError, build_session, get_json


def parse_ipea_payload(payload: object, level: str | None = None) -> pd.DataFrame:
    """Converte ``{"value": [{"VALDATA": ..., "VALVALOR": ...}]}`` em DataFrame date/value.

    ``level``: algumas séries trazem vários níveis geográficos (``NIVNOME``: Brasil, Regiões, Estados); informe ``"Brasil"``
    para ficar só com o total nacional.
    """
    if not isinstance(payload, dict) or "value" not in payload:
        raise IngestionError("payload Ipeadata inesperado")
    rows = payload["value"]
    if not rows:
        raise IngestionError("Ipeadata não devolveu linhas para a série")
    df = pd.DataFrame(rows)
    if level is not None and "NIVNOME" in df.columns:
        df = df[df["NIVNOME"] == level]
        if df.empty:
            raise IngestionError(f"Ipeadata sem linhas no nível {level!r}")
    out = pd.DataFrame(
        {
            # VALDATA vem com fuso (-03:00); descartamos o fuso para alinhar com as demais séries
            "date": pd.to_datetime(df["VALDATA"], utc=True, errors="coerce").dt.tz_convert(None).dt.normalize(),
            "value": pd.to_numeric(df["VALVALOR"], errors="coerce"),
        }
    )
    return validate_series(out)


def parse_ipea_levels(payload: object, levels: tuple[str, ...] = ("Brasil", "Estados")) -> pd.DataFrame:
    """Ipeadata multinível -> ``date, uf, value``; ``uf`` = ``BR`` (nível Brasil) ou a sigla (TERCODIGO = código IBGE da UF)."""
    from src.config import UF_BY_IBGE_CODE
    if not isinstance(payload, dict) or not payload.get("value"):
        raise IngestionError("payload Ipeadata multinível vazio")
    df = pd.DataFrame(payload["value"])
    if "NIVNOME" not in df.columns:
        raise IngestionError("série do Ipeadata sem níveis geográficos")
    df = df[df["NIVNOME"].isin(levels)]
    uf = [("BR" if n == "Brasil" else UF_BY_IBGE_CODE.get(str(t).strip()[:2])) for n, t in zip(df["NIVNOME"], df["TERCODIGO"], strict=True)]
    out = pd.DataFrame({
        "date": pd.to_datetime(df["VALDATA"], utc=True, errors="coerce").dt.tz_convert(None).dt.normalize(),
        "uf": uf, "value": pd.to_numeric(df["VALVALOR"], errors="coerce"),
    }).dropna()
    if out.empty:
        raise IngestionError("Ipeadata sem linhas nos níveis pedidos")
    return out.sort_values(["uf", "date"]).reset_index(drop=True)


def get_ipea_levels(key: str, code: str, base: Path | None = None, session: requests.Session | None = None,
                    force: bool = False) -> FetchResult:
    """Série anual do Ipeadata (Brasil + UFs) com cache por validade (o arquivo completo tem ~20 MB)."""
    from src.config import IPEA_LEVELS_MAX_AGE_DAYS
    from src.ingestion.cache import with_freshness
    return with_freshness(f"ipea_{key}", lambda: parse_ipea_levels(
        get_json(IPEA_URL_TEMPLATE.format(code=code), session or build_session(), timeout=IPEA_TIMEOUT)),
        IPEA_LEVELS_MAX_AGE_DAYS, base, force)


def fetch_ipea(code: str, session: requests.Session | None = None, level: str | None = None) -> pd.DataFrame:
    url = IPEA_URL_TEMPLATE.format(code=code)
    return parse_ipea_payload(get_json(url, session or build_session(), timeout=IPEA_TIMEOUT), level)


def get_ipea_series(key: str, code: str, base: Path | None = None, session: requests.Session | None = None,
                    level: str | None = None) -> FetchResult:
    """Série do Ipeadata com fallback para Parquet; ``missing`` se a série não existir/vier vazia."""
    return with_disk_fallback(f"ipea_{key}", lambda: fetch_ipea(code, session, level), base)
