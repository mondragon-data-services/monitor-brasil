"""IPCA por item (alimentos e gasolina) — IBGE SIDRA, tabela 7060, variável 63 (variação mensal %).

O IBGE publica a VARIAÇÃO de preço de cada item, não o preço em reais. Por isso o painel mostra inflação acumulada e
"quanto custa hoje o que custava R$ 100 em dez/2019" — e preço em reais apenas para a gasolina (ANP).
Regra do SIDRA: ``payload[0]`` é cabeçalho; ``"..."``, ``"-"``, ``"X"``, ``".."`` viram NaN.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from src.config import IPCA_ITEM_BY_CODE, IPCA_ITEMS, IPCA_ITEMS_URL
from src.ingestion.cache import FetchResult, with_disk_fallback
from src.ingestion.http_client import IngestionError, build_session, get_json
from src.ingestion.ibge_client import sidra_period_to_date

CACHE_KEY = "ipca_itens"


def parse_ipca_items(payload: object) -> pd.DataFrame:
    """JSON SIDRA multi-item → DataFrame longo ``date, item, value`` (item = chave de ``config.IPCA_ITEMS``)."""
    if not isinstance(payload, list) or len(payload) < 2:
        raise IngestionError("payload do IPCA por item vazio")
    header, rows = payload[0], pd.DataFrame(payload[1:])
    item_col = next((c for c in rows.columns if c.startswith("D") and c.endswith("C")
                     and "Geral, grupo" in str(header.get(c, ""))), None)
    period_col = next((c for c in rows.columns if c.startswith("D") and c.endswith("C")
                       and str(header.get(c, "")).startswith("Mês")), None)
    if item_col is None or period_col is None or "V" not in rows.columns:
        raise IngestionError("colunas de item/período ausentes no IPCA por item")
    out = pd.DataFrame({
        "date": [sidra_period_to_date(c, "movel") for c in rows[period_col]],
        "item": pd.to_numeric(rows[item_col], errors="coerce").map(IPCA_ITEM_BY_CODE),
        "value": pd.to_numeric(rows["V"], errors="coerce"),
    })
    out = out.dropna(subset=["item", "value"]).drop_duplicates(["date", "item"]).sort_values(["item", "date"])
    if out.empty:
        raise IngestionError("IPCA por item sem valores válidos")
    return out.reset_index(drop=True)


def fetch_ipca_items(session: requests.Session | None = None) -> pd.DataFrame:
    url = IPCA_ITEMS_URL.format(codes=",".join(str(i.code) for i in IPCA_ITEMS.values()))
    return parse_ipca_items(get_json(url, session or build_session()))


def get_ipca_items(base: Path | None = None, session: requests.Session | None = None) -> FetchResult:
    return with_disk_fallback(CACHE_KEY, lambda: fetch_ipca_items(session), base)
