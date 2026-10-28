"""Bolsa Família mensal por estado: ETL do arquivo de pagamentos do Portal da Transparência (CGU).

Fonte: ``https://portaldatransparencia.gov.br/download-de-dados/novo-bolsa-familia/{AAAAMM}`` (zip de ≈ 330 MB; o CSV tem ≈ 2 GB
e ≈ 19 milhões de linhas, uma por pagamento). Cada mês é baixado, agregado por UF e descartado; guardamos só
``data/processed/pbf_{AAAAMM}.parquet`` (poucas linhas) e o combinado ``pbf_mensal.parquet``.

Definições (validadas contra o Ipeadata ``VAL_PBF12``: o valor total de ago/2026 bate com R$ 12,763 bilhões):
* ``familias`` = favorecidos distintos (NIS) com pagamento na competência (inclui parcelas retroativas);
* ``pagamentos`` = linhas do arquivo; ``valor`` = soma de ``VALOR PARCELA`` em reais;
* valor médio por família = ``valor / familias`` (inclui parcelas retroativas pagas no mês).

O app NÃO baixa isso no carregamento (são ~45 s e 330 MB por mês): use ``python scripts/sync_data.py --bolsa-familia``.
"""
from __future__ import annotations

import logging
import tempfile
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from src.config import DATA_PROCESSED
from src.ingestion.cache import FetchResult
from src.ingestion.http_client import IngestionError

log = logging.getLogger(__name__)
URL = "https://portaldatransparencia.gov.br/download-de-dados/novo-bolsa-familia/{ym}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9"}
FIRST_MONTH = (2023, 3)         # o Novo Bolsa Família começa em março/2023
DEFAULT_MONTHS = 24
USECOLS = ["MÊS COMPETÊNCIA", "UF", "NIS FAVORECIDO", "VALOR PARCELA"]
COLUMNS = ["date", "uf", "familias", "pagamentos", "valor"]
CACHE_KEY = "pbf_mensal"

ProgressFn = Callable[[str, float], None]


def month_path(ym: str, base: Path | None = None) -> Path:
    return (base or DATA_PROCESSED) / f"pbf_{ym}.parquet"


def recent_months(count: int, today: date | None = None) -> list[str]:
    """Os ``count`` meses mais recentes (AAAAMM) até o mês corrente, do mais antigo ao mais novo, sem passar de ``FIRST_MONTH``."""
    now = today or date.today()
    out = []
    y, m = now.year, now.month
    while len(out) < count and (y, m) >= FIRST_MONTH:
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def aggregate_payments(chunks) -> pd.DataFrame:
    """Agrega pedaços do CSV (como texto) em ``date, uf, familias, pagamentos, valor`` (inclui a linha ``BR``)."""
    nis: dict[str, list[np.ndarray]] = {}
    rows: dict[str, int] = {}
    value: dict[str, float] = {}
    months: set[str] = set()
    for chunk in chunks:
        missing = set(USECOLS) - set(chunk.columns)
        if missing:
            raise IngestionError(f"arquivo do Bolsa Família sem as colunas {sorted(missing)} — layout mudou?")
        chunk = chunk.copy()
        chunk["v"] = chunk["VALOR PARCELA"].str.replace(".", "", regex=False).str.replace(",", ".", regex=False).astype(float)
        chunk["nis"] = pd.to_numeric(chunk["NIS FAVORECIDO"], errors="coerce").fillna(0).astype("int64")
        months.update(chunk["MÊS COMPETÊNCIA"].dropna().unique())
        for uf, g in chunk.groupby("UF"):
            nis.setdefault(uf, []).append(g["nis"].to_numpy())
            rows[uf] = rows.get(uf, 0) + len(g)
            value[uf] = value.get(uf, 0.0) + float(g["v"].sum())
    if not nis or len(months) != 1:
        raise IngestionError(f"arquivo do Bolsa Família inesperado (competências: {sorted(months)[:3]})")
    ym = next(iter(months))
    date_ = pd.Timestamp(int(ym[:4]), int(ym[4:6]), 1)
    out = [{"date": date_, "uf": uf, "familias": int(len(np.unique(np.concatenate(arrs)))), "pagamentos": rows[uf], "valor": value[uf]}
           for uf, arrs in nis.items()]
    all_nis = np.unique(np.concatenate([a for arrs in nis.values() for a in arrs]))
    out.append({"date": date_, "uf": "BR", "familias": int(len(all_nis)), "pagamentos": sum(rows.values()), "valor": sum(value.values())})
    return pd.DataFrame(out)[COLUMNS]


def download_month(ym: str, dest: Path, session: requests.Session | None = None) -> Path:
    sess = session or requests.Session()
    part = dest.with_suffix(".part")
    try:
        with sess.get(URL.format(ym=ym), headers=HEADERS, timeout=(10, 600), stream=True) as resp:
            if resp.status_code in (403, 404):
                raise IngestionError(f"competência {ym} ainda não publicada (HTTP {resp.status_code})")
            if resp.status_code >= 400:
                raise IngestionError(f"HTTP {resp.status_code} ao baixar o Bolsa Família de {ym}")
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
    except requests.RequestException as exc:
        raise IngestionError(f"falha de rede ao baixar o Bolsa Família de {ym}: {exc}") from exc
    with open(part, "rb") as fh:
        magic = fh.read(2)
    if magic != b"PK":
        part.unlink(missing_ok=True)
        raise IngestionError(f"o arquivo de {ym} não é um zip válido")
    part.replace(dest)
    return dest


def aggregate_zip(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as fh:
        chunks = pd.read_csv(fh, sep=";", encoding="latin-1", usecols=USECOLS, dtype=str, chunksize=2_000_000)
        return aggregate_payments(chunks)


def combine(base: Path | None = None) -> pd.DataFrame:
    folder = base or DATA_PROCESSED
    frames = [pd.read_parquet(p) for p in sorted(folder.glob("pbf_[0-9][0-9][0-9][0-9][0-9][0-9].parquet"))]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True).sort_values(["uf", "date"]).reset_index(drop=True)
    df.to_parquet(folder / f"{CACHE_KEY}.parquet", index=False)
    return df


def sync_bolsa_familia(months: list[str] | None = None, force: bool = False, base: Path | None = None,
                       session: requests.Session | None = None, progress: ProgressFn | None = None) -> list[tuple[str, str]]:
    """Baixa e agrega os meses pedidos (padrão: os últimos 24). Meses já agregados não são refeitos. Devolve ``[(AAAAMM, status)]``."""
    wanted = months or recent_months(DEFAULT_MONTHS)
    folder = base or DATA_PROCESSED
    folder.mkdir(parents=True, exist_ok=True)
    report: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="pbf_") as tmp:
        for i, ym in enumerate(wanted):
            target = month_path(ym, base)
            say = (lambda msg, _i=i: progress(msg, _i / len(wanted))) if progress else (lambda msg: None)
            if target.exists() and not force:
                report.append((ym, "cache"))
                continue
            try:
                say(f"{ym}: baixando (~330 MB)…")
                zpath = download_month(ym, Path(tmp) / f"{ym}.zip", session)
                say(f"{ym}: agregando…")
                aggregate_zip(zpath).to_parquet(target, index=False)
                zpath.unlink(missing_ok=True)
                report.append((ym, "baixado"))
            except IngestionError as exc:
                report.append((ym, "indisponível" if "ainda não publicada" in str(exc) else "erro"))
                log.warning("Bolsa Família %s: %s", ym, exc)
            except Exception as exc:  # arquivo corrompido/layout novo não derruba os outros meses
                report.append((ym, "erro"))
                log.warning("Bolsa Família %s falhou: %s: %s", ym, type(exc).__name__, exc)
    combine(base)
    return report


def get_bolsa_familia_mensal(base: Path | None = None) -> FetchResult:
    """Lê o agregado mensal do disco (sem rede). ``missing`` se o ETL ainda não rodou."""
    path = (base or DATA_PROCESSED) / f"{CACHE_KEY}.parquet"
    if not path.exists():
        return FetchResult(CACHE_KEY, pd.DataFrame(columns=COLUMNS), "missing", None, "rode: python scripts/sync_data.py --bolsa-familia")
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        return FetchResult(CACHE_KEY, pd.DataFrame(columns=COLUMNS), "missing", None, f"parquet ilegível: {exc}")
    return FetchResult(CACHE_KEY, df, "cache", datetime.fromtimestamp(path.stat().st_mtime), "cache válido (ETL mensal do Portal da Transparência)")
