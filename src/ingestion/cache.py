"""Camada 2 do cache híbrido: Parquet em disco + resultado tipado de cada busca."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import DATA_PROCESSED
from src.contracts import SourceMode

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchResult:
    """Resultado de uma busca: dados + origem (API ao vivo, cache em disco ou indisponível)."""

    key: str
    df: pd.DataFrame
    mode: SourceMode
    synced_at: datetime | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.df.empty


def parquet_path(key: str, base: Path | None = None) -> Path:
    return (base or DATA_PROCESSED) / f"{key}.parquet"


def save_parquet(df: pd.DataFrame, key: str, base: Path | None = None) -> Path:
    path = parquet_path(key, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def load_parquet(key: str, base: Path | None = None) -> tuple[pd.DataFrame, datetime] | None:
    """Lê o Parquet mais recente da série; devolve ``None`` se não existir ou estiver corrompido."""
    path = parquet_path(key, base)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception as exc:  # arquivo corrompido não pode derrubar o app
        log.warning("parquet ilegível %s: %s", path, exc)
        return None
    return df, datetime.fromtimestamp(path.stat().st_mtime)


def with_disk_fallback(key: str, fetch, base: Path | None = None) -> FetchResult:
    """Executa ``fetch()`` (que devolve DataFrame); persiste em Parquet se OK, senão usa o disco."""
    try:
        df = fetch()
        save_parquet(df, key, base)
        return FetchResult(key, df, "live", datetime.now())
    except Exception as exc:
        log.warning("API indisponível para %s: %s", key, exc)
        cached = load_parquet(key, base)
        if cached is not None:
            df, synced = cached
            return FetchResult(key, df, "cache", synced, f"API indisponível ({type(exc).__name__}); usando cache local")
        return FetchResult(key, pd.DataFrame(), "missing", None, f"sem API e sem cache: {exc}")
