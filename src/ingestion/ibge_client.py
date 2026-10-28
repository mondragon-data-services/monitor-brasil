"""Extrator da API SIDRA do IBGE.

Regra crítica: o elemento ``[0]`` do JSON é o cabeçalho descritivo das colunas, não dado.
Os dados reais começam em ``[1:]``.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from src.config import SIDRA_TABLES
from src.contracts import validate_series
from src.ingestion.cache import FetchResult, with_disk_fallback
from src.ingestion.http_client import IngestionError, build_session, get_json


_PERIOD_HEADERS = ("Trimestre", "Mês", "Ano")


def sidra_period_to_date(code: str, kind: str) -> pd.Timestamp:
    """Converte o código de período SIDRA em data (primeiro dia do mês final do período).

    * ``movel``: ``AAAAMM`` do último mês do trimestre móvel (ex.: ``202608`` = jun-jul-ago/2026).
    * ``trimestre``: ``AAAA0Q`` (ex.: ``202602`` = 2º trimestre → mês de referência 06).
    """
    code = str(code).strip()
    if kind == "ano":
        return pd.Timestamp(year=int(code[:4]), month=1, day=1)
    year, tail = int(code[:4]), int(code[4:6])
    month = tail * 3 if kind == "trimestre" else tail
    if not 1 <= month <= 12:
        raise IngestionError(f"código de período SIDRA inválido: {code}")
    return pd.Timestamp(year=year, month=month, day=1)


def parse_sidra_payload(payload: object, period_kind: str) -> pd.DataFrame:
    """Converte o JSON SIDRA em DataFrame ``date``/``value``/``period_label``.

    Descarta o cabeçalho (índice 0) e converte valores sigilosos/indisponíveis
    (``"..."``, ``"-"``, ``"X"``, ``".."``) em NaN, removidos em seguida.
    """
    if not isinstance(payload, list) or len(payload) < 2:
        raise IngestionError("payload SIDRA vazio ou sem linhas de dados")
    rows = payload[1:]  # [0] é o cabeçalho descritivo
    df = pd.DataFrame(rows)
    header = payload[0]
    period_col = next(
        (c for c in df.columns if c.startswith("D") and c.endswith("C") and str(header.get(c, "")).startswith(_PERIOD_HEADERS)),
        None,
    )
    if period_col is None or "V" not in df.columns:
        raise IngestionError("colunas de período/valor ausentes na resposta SIDRA")
    name_col = period_col[:-1] + "N"
    out = pd.DataFrame(
        {
            "date": [sidra_period_to_date(c, period_kind) for c in df[period_col]],
            "value": pd.to_numeric(df["V"], errors="coerce"),
            "period_label": df[name_col] if name_col in df.columns else df[period_col],
        }
    )
    out = out.dropna(subset=["value"])
    if out.empty:
        raise IngestionError("SIDRA devolveu apenas valores indisponíveis")
    validated = validate_series(out[["date", "value"]])
    return validated.merge(out[["date", "period_label"]].drop_duplicates("date"), on="date", how="left")


def parse_sidra_dims(payload: object, period_kind: str) -> pd.DataFrame:
    """JSON SIDRA com dimensões extras -> ``date, value, period_label, geo_code, geo_name, cat_code, cat``.

    ``geo`` = D1 (Brasil/UF); ``cat`` = a última dimensão que não é território, variável nem período (vazia se não houver).
    """
    if not isinstance(payload, list) or len(payload) < 2:
        raise IngestionError("payload SIDRA vazio ou sem linhas de dados")
    header, rows = payload[0], pd.DataFrame(payload[1:])
    code_cols = [c for c in rows.columns if c.startswith("D") and c.endswith("C")]
    period_col = next((c for c in code_cols if str(header.get(c, "")).startswith(_PERIOD_HEADERS)), None)
    if period_col is None or "V" not in rows.columns:
        raise IngestionError("colunas de período/valor ausentes na resposta SIDRA")
    extra = [c for c in code_cols if c not in ("D1C", "D2C", period_col)]
    cat_col = extra[-1] if extra else None
    out = pd.DataFrame({
        "date": [sidra_period_to_date(c, period_kind) for c in rows[period_col]],
        "value": pd.to_numeric(rows["V"], errors="coerce"),
        "period_label": rows[period_col[:-1] + "N"] if period_col[:-1] + "N" in rows.columns else rows[period_col],
        "geo_code": rows["D1C"].astype(str), "geo_name": rows["D1N"].astype(str),
        "cat_code": rows[cat_col].astype(str) if cat_col else "", "cat": rows[cat_col[:-1] + "N"].astype(str) if cat_col else "",
    })
    out = out.dropna(subset=["value"])
    if out.empty:
        raise IngestionError("SIDRA devolveu apenas valores indisponíveis")
    return out.sort_values(["geo_code", "cat_code", "date"]).reset_index(drop=True)


def get_sidra_social(key: str, base: Path | None = None, session: requests.Session | None = None) -> FetchResult:
    """Tabelas sociais do IBGE (``config.SIDRA_SOCIAL``) com dimensões extras e cache em Parquet."""
    from src.config import SIDRA_SOCIAL
    spec = SIDRA_SOCIAL[key]
    return with_disk_fallback(f"soc_{key}", lambda: parse_sidra_dims(get_json(spec.url, session or build_session()), spec.period_kind), base)


def fetch_sidra(key: str, session: requests.Session | None = None) -> pd.DataFrame:
    table = SIDRA_TABLES[key]
    payload = get_json(table.url, session or build_session())
    return parse_sidra_payload(payload, table.period_kind)


def get_ibge_series(key: str, base: Path | None = None, session: requests.Session | None = None) -> FetchResult:
    """Série SIDRA pelo nome lógico (ver ``config.SIDRA_TABLES``) com fallback para Parquet em disco."""
    return with_disk_fallback(key, lambda: fetch_sidra(key, session), base)
