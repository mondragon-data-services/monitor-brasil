"""Camada de ingestão: BCB (SGS), IBGE (SIDRA), Ipeadata e Sinesp/INEP com cache em Parquet."""
from __future__ import annotations

from collections.abc import Callable
from functools import partial

import requests

from src.config import IPEA_CANDIDATE_SERIES, IPEA_LEVELS, IPEA_SOCIAL, SGS_SERIES, SIDRA_SOCIAL, SIDRA_TABLES, WORLD_BANK_INDICATORS
from src.ingestion.anp_client import get_anp_gasolina
from src.ingestion.bcb_client import get_bcb_series
from src.ingestion.bolsa_familia_download import get_bolsa_familia_mensal
from src.ingestion.cache import FetchResult
from src.ingestion.dengue_client import get_dengue
from src.ingestion.focus_client import get_focus
from src.ingestion.http_client import build_session
from src.ingestion.ibge_client import get_ibge_series, get_sidra_social
from src.ingestion.ipca_itens_client import get_ipca_items
from src.ingestion.ipea_client import get_ipea_levels, get_ipea_series
from src.ingestion.sinesp_loader import get_security_education
from src.ingestion.tabnet_client import get_leitos, get_pni
from src.ingestion.worldbank_client import get_world_bank


def extra_jobs() -> dict[str, Callable[[requests.Session], FetchResult]]:
    """Fontes de pobreza, trabalho, saúde e dengue: ``chave -> função(session)``.

    Chaves: ``soc_*`` (IBGE SIDRA), ``ipea_*`` (Ipeadata: pobreza, Caged, Bolsa Família), ``wb_*`` (Banco Mundial), ``dengue``.
    Usado pelo ``sync_all`` (sequencial) e pelo app (em paralelo).
    """
    jobs: dict[str, Callable[[requests.Session], FetchResult]] = {}
    for key in SIDRA_SOCIAL:
        jobs[f"soc_{key}"] = lambda s, k=key: get_sidra_social(k, session=s)
    for key, spec in IPEA_SOCIAL.items():
        jobs[f"ipea_{key}"] = lambda s, k=key, sp=spec: get_ipea_series(k, str(sp["code"]), session=s, level=sp["level"])
    for key in WORLD_BANK_INDICATORS:
        jobs[f"wb_{key}"] = partial(lambda s, k: get_world_bank(k, session=s), k=key)
    for key, code in IPEA_LEVELS.items():
        jobs[f"ipea_{key}"] = lambda s, k=key, c=code: get_ipea_levels(k, c, session=s)
    jobs["pbf_mensal"] = lambda s: get_bolsa_familia_mensal()  # só lê o Parquet; o ETL é manual (--bolsa-familia)
    jobs["tabnet_leitos"] = lambda s: get_leitos(session=s)
    jobs["tabnet_pni"] = lambda s: get_pni(session=s)
    jobs["dengue"] = lambda s: get_dengue(session=s)
    return jobs


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
    results["anp_gasolina"] = get_anp_gasolina(session=session)
    results["ipca_itens"] = get_ipca_items(session=session)
    results["focus"] = get_focus(session=session)
    if include_ipea:
        for key, code in IPEA_CANDIDATE_SERIES.items():
            results[f"ipea_{key}"] = get_ipea_series(key, code, session=session)
    for key, job in extra_jobs().items():
        results[key] = job(session)
    results["seguranca_educacao"] = get_security_education()
    return results


__all__ = ["sync_all", "extra_jobs", "FetchResult"]
