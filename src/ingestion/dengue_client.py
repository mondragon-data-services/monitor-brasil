"""Dengue — microdados do SINAN (OpenDataSUS), agregados por semana epidemiológica e UF.

Fonte: ``DENGBR{aa}.csv.zip`` (15–45 MB por ano; o CSV tem >100 MB). Cada ano é lido em poucos segundos, reduzido a
``semana × UF`` e guardado em ``data/processed/dengue_{ano}.parquet``. Só o ano corrente é rebaixado (a cada
``DENGUE_MAX_AGE_DAYS`` dias); anos fechados ficam em cache.

Definições: ``casos`` = notificações não descartadas (todas as linhas do arquivo); ``graves`` = classificação final 11 (com
sinais de alarme) ou 12 (grave); ``obitos`` = evolução 2 (óbito pelo agravo). As últimas semanas chegam incompletas.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from src.config import (
    DATA_PROCESSED,
    DENGUE_FIRST_YEAR,
    DENGUE_MAX_AGE_DAYS,
    DENGUE_URL,
    UF_BY_IBGE_CODE,
)
from src.ingestion.cache import FetchResult
from src.ingestion.http_client import IngestionError

log = logging.getLogger(__name__)
CACHE_KEY = "dengue"
COLUMNS = ["date", "ano", "semana", "uf", "casos", "graves", "obitos"]
USECOLS = ["SEM_NOT", "SG_UF_NOT", "CLASSI_FIN", "EVOLUCAO"]


def epiweek_start(year: int, week: int) -> pd.Timestamp:
    """Domingo que inicia a semana epidemiológica (MMWR): a semana 1 é a que contém 4 de janeiro."""
    jan4 = date(year, 1, 4)
    week1_start = jan4 - timedelta(days=(jan4.weekday() + 1) % 7)
    return pd.Timestamp(week1_start + timedelta(days=7 * (week - 1)))


def aggregate_dengue(raw: pd.DataFrame) -> pd.DataFrame:
    """Microdados (colunas ``USECOLS``, como texto) -> ``date, ano, semana, uf, casos, graves, obitos``."""
    missing = set(USECOLS) - set(raw.columns)
    if missing:
        raise IngestionError(f"arquivo do SINAN sem as colunas {sorted(missing)} — layout mudou?")
    df = raw.dropna(subset=["SEM_NOT", "SG_UF_NOT"]).copy()
    df["SEM_NOT"] = pd.to_numeric(df["SEM_NOT"], errors="coerce")
    df = df.dropna(subset=["SEM_NOT"])
    df["uf"] = df["SG_UF_NOT"].astype(str).str.strip().str[:2].map(UF_BY_IBGE_CODE)
    df = df.dropna(subset=["uf"])
    df["grave"] = df["CLASSI_FIN"].isin(["11", "12", "11.0", "12.0"])
    df["obito"] = df["EVOLUCAO"].isin(["2", "2.0"])
    out = (df.groupby(["SEM_NOT", "uf"], as_index=False)
             .agg(casos=("uf", "size"), graves=("grave", "sum"), obitos=("obito", "sum")))
    out["ano"] = (out["SEM_NOT"] // 100).astype(int)
    out["semana"] = (out["SEM_NOT"] % 100).astype(int)
    out = out[(out["semana"] >= 1) & (out["semana"] <= 53)]
    out["date"] = [epiweek_start(a, s) for a, s in zip(out["ano"], out["semana"], strict=True)]
    for col in ("casos", "graves", "obitos"):
        out[col] = out[col].astype(int)
    return out[COLUMNS].sort_values(["date", "uf"]).reset_index(drop=True)


def download_dengue_year(year: int, session: requests.Session | None = None) -> pd.DataFrame:
    url = DENGUE_URL.format(yy=str(year)[-2:])
    sess = session or requests.Session()
    try:
        resp = sess.get(url, timeout=(10, 180))
    except requests.RequestException as exc:
        raise IngestionError(f"falha de rede ao baixar o dengue de {year}: {exc}") from exc
    if resp.status_code == 404:
        raise IngestionError(f"dengue {year} ainda não publicado (HTTP 404)")
    if resp.status_code >= 400:
        raise IngestionError(f"HTTP {resp.status_code} ao baixar o dengue de {year}")
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            raw = pd.read_csv(z.open(z.namelist()[0]), usecols=USECOLS, dtype=str, encoding="latin-1")
    except (zipfile.BadZipFile, ValueError, KeyError) as exc:
        raise IngestionError(f"arquivo de dengue {year} inválido: {exc}") from exc
    return aggregate_dengue(raw)


def _year_path(year: int, base: Path | None = None) -> Path:
    return (base or DATA_PROCESSED) / f"dengue_{year}.parquet"


def _is_fresh(path: Path, year: int, now: datetime) -> bool:
    if not path.exists():
        return False
    if year < now.year:
        return True
    return now - datetime.fromtimestamp(path.stat().st_mtime) < timedelta(days=DENGUE_MAX_AGE_DAYS)


def get_dengue(base: Path | None = None, session: requests.Session | None = None, force: bool = False,
               first_year: int = DENGUE_FIRST_YEAR, today: date | None = None) -> FetchResult:
    """Dengue por semana e UF, desde ``first_year``. Falha de um ano não derruba os outros (usa o cache desse ano)."""
    now = datetime.combine(today or date.today(), datetime.min.time()) if today else datetime.now()
    frames: list[pd.DataFrame] = []
    live = False
    problems: list[str] = []
    synced: datetime | None = None
    folder = base or DATA_PROCESSED
    folder.mkdir(parents=True, exist_ok=True)
    for year in range(first_year, now.year + 1):
        path = _year_path(year, base)
        if not force and _is_fresh(path, year, now):
            frames.append(pd.read_parquet(path))
            synced = max(synced or datetime.fromtimestamp(path.stat().st_mtime), datetime.fromtimestamp(path.stat().st_mtime))
            continue
        try:
            df = download_dengue_year(year, session)
            df.to_parquet(path, index=False)
            frames.append(df)
            live, synced = True, datetime.now()
        except IngestionError as exc:
            problems.append(f"{year}: {exc}")
            if path.exists():
                frames.append(pd.read_parquet(path))
    if not frames:
        return FetchResult(CACHE_KEY, pd.DataFrame(columns=COLUMNS), "missing", None, "; ".join(problems) or "sem dados de dengue")
    df = pd.concat(frames, ignore_index=True).sort_values(["date", "uf"]).reset_index(drop=True)
    mode = "live" if live else "cache"
    detail = ("cache válido (dengue)" if mode == "cache" and not problems else "; ".join(problems))
    return FetchResult(CACHE_KEY, df, mode, synced, detail)
