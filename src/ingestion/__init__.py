"""Camada de ingestão: BCB (SGS), IBGE (SIDRA), Ipeadata e Sinesp/INEP com cache em Parquet."""
from __future__ import annotations

from src.config import IPEA_CANDIDATE_SERIES, SGS_SERIES, SIDRA_TABLES
from src.ingestion.bcb_client import get_bcb_series
from src.ingestion.cache import FetchResult
from src.ingestion.http_client import build_session
from src.ingestion.ibge_client import get_ibge_series
from src.ingestion.ipea_client import get_ipea_series
from src.ingestion.sinesp_loader import get_security_education


def sync_all(include_ipea: bool = True) -> dict[str, FetchResult]:
    """Baixa todas as fontes (BCB, IBGE, Ipeadata) e monta o dataset de segurança/educação.

    Cada item é independente: a falha de uma fonte nunca derruba as demais.
    """
    session = build_session()
    results: dict[str, FetchResult] = {}
    for key in SGS_SERIES:
        results[key] = get_bcb_series(key, session=session)
    for key in SIDRA_TABLES:
        results[key] = get_ibge_series(key, session=session)
    if include_ipea:
        for key, code in IPEA_CANDIDATE_SERIES.items():
            results[f"ipea_{key}"] = get_ipea_series(key, code, session=session)
    results["seguranca_educacao"] = get_security_education()
    return results


__all__ = ["sync_all", "FetchResult"]
