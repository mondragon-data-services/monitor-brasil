"""Preço da gasolina comum por estado e Brasil — síntese semanal da ANP (Agência Nacional do Petróleo).

Fonte: duas planilhas oficiais ``semanal-brasil-desde-2013.xlsx`` (≈ 0,5 MB) e ``semanal-estados-desde-2013.xlsx``
(≈ 13 MB). O site da ANP só entrega o arquivo a clientes com cabeçalhos de navegador. Cada planilha tem ~17 linhas de
cabeçalho institucional antes da tabela; a linha de títulos é localizada por ``DATA INICIAL``.

Para não baixar 13 MB a cada abertura do app, o resultado fica em ``data/processed/anp_gasolina.parquet`` e só é refeito
quando o arquivo passa de ``ANP_MAX_AGE_DAYS`` (a ANP publica uma vez por semana).
"""
from __future__ import annotations

import io
import logging
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from src.config import (
    ANP_MAX_AGE_DAYS,
    ANP_PRODUCT,
    ANP_REFERER,
    ANP_URL_BRASIL,
    ANP_URL_ESTADOS,
    HTTP_TIMEOUT,
    UF_BY_ANP_NAME,
)
from src.ingestion.cache import FetchResult, load_parquet, save_parquet
from src.ingestion.http_client import IngestionError

log = logging.getLogger(__name__)
CACHE_KEY = "anp_gasolina"
ANP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "*/*", "Accept-Language": "pt-BR,pt;q=0.9", "Referer": ANP_REFERER,
}
COLUMNS = ["date", "uf", "value", "postos", "min", "max"]


def _fold(text: object) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(text).upper().strip()) if not unicodedata.combining(c))


def parse_anp_sheet(raw: pd.DataFrame, product: str = ANP_PRODUCT) -> pd.DataFrame:
    """Converte a planilha bruta (sem cabeçalho) em ``date, uf, value, postos, min, max`` do produto pedido.

    ``uf`` = sigla de 2 letras, ou ``BR`` na planilha nacional. ``date`` = último dia da semana pesquisada.
    """
    head = next((i for i in range(min(60, len(raw))) if _fold(raw.iloc[i, 0]).startswith("DATA INICIAL")), None)
    if head is None:
        raise IngestionError("planilha da ANP sem a linha 'DATA INICIAL' — layout mudou?")
    df = raw.iloc[head + 1:].copy()
    df.columns = [_fold(c) for c in raw.iloc[head]]
    need = {"DATA FINAL", "PRODUTO", "PRECO MEDIO REVENDA"}
    if not need <= set(df.columns):
        raise IngestionError(f"colunas ausentes na planilha da ANP: {sorted(need - set(df.columns))}")
    df = df[df["PRODUTO"].map(_fold) == _fold(product)]
    if "ESTADO" in df.columns:
        df = df.assign(uf=df["ESTADO"].map(lambda n: UF_BY_ANP_NAME.get(_fold(n))))
        df = df[df["uf"].notna()]
    else:
        df = df.assign(uf="BR")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["DATA FINAL"], errors="coerce"),
        "uf": df["uf"].astype(str),
        "value": pd.to_numeric(df["PRECO MEDIO REVENDA"], errors="coerce"),
        "postos": pd.to_numeric(df.get("NUMERO DE POSTOS PESQUISADOS"), errors="coerce"),
        "min": pd.to_numeric(df.get("PRECO MINIMO REVENDA"), errors="coerce"),
        "max": pd.to_numeric(df.get("PRECO MAXIMO REVENDA"), errors="coerce"),
    })
    return out.dropna(subset=["date", "value"]).reset_index(drop=True)


def _download(url: str, session: requests.Session) -> bytes:
    try:
        resp = session.get(url, headers=ANP_HEADERS, timeout=(HTTP_TIMEOUT[0], 180))
    except requests.RequestException as exc:
        raise IngestionError(f"falha de rede ao baixar a planilha da ANP: {exc}") from exc
    if resp.status_code >= 400:
        raise IngestionError(f"HTTP {resp.status_code} ao baixar {url.rsplit('/', 1)[-1]}")
    if resp.content[:2] != b"PK":
        raise IngestionError("a ANP não devolveu um .xlsx (bloqueio ou página de erro)")
    return resp.content


def _read(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_excel(io.BytesIO(content), engine="calamine", header=None)
    except ImportError:
        return pd.read_excel(io.BytesIO(content), engine="openpyxl", header=None)


def fetch_anp_gasolina(session: requests.Session | None = None) -> pd.DataFrame:
    """Baixa e combina as planilhas Brasil + Estados (gasolina comum)."""
    sess = session or requests.Session()
    brasil = parse_anp_sheet(_read(_download(ANP_URL_BRASIL, sess)))
    estados = parse_anp_sheet(_read(_download(ANP_URL_ESTADOS, sess)))
    if brasil.empty or estados.empty:
        raise IngestionError("planilha da ANP sem linhas de gasolina comum")
    return pd.concat([brasil, estados], ignore_index=True).sort_values(["uf", "date"]).reset_index(drop=True)


def get_anp_gasolina(base: Path | None = None, session: requests.Session | None = None, force: bool = False,
                     max_age_days: int = ANP_MAX_AGE_DAYS) -> FetchResult:
    """Cache em disco com validade; se a ANP estiver fora do ar, usa o último Parquet."""
    cached = load_parquet(CACHE_KEY, base)
    if cached is not None and not force:
        df, synced = cached
        if datetime.now() - synced < timedelta(days=max_age_days):
            return FetchResult(CACHE_KEY, df, "cache", synced, "cache válido (a ANP atualiza 1x por semana)")
    try:
        df = fetch_anp_gasolina(session)
        save_parquet(df, CACHE_KEY, base)
        return FetchResult(CACHE_KEY, df, "live", datetime.now())
    except Exception as exc:
        log.warning("ANP indisponível: %s", exc)
        if cached is not None:
            df, synced = cached
            return FetchResult(CACHE_KEY, df, "cache", synced, f"ANP indisponível ({type(exc).__name__}); usando cache local")
        return FetchResult(CACHE_KEY, pd.DataFrame(columns=COLUMNS), "missing", None, f"sem ANP e sem cache: {exc}")
