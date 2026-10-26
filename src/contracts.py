"""Contratos de dados (Pydantic) para séries temporais e status das fontes."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

SourceMode = Literal["live", "cache", "fallback", "missing"]


class SeriesPoint(BaseModel):
    """Um ponto de série temporal já limpo."""

    model_config = ConfigDict(frozen=True)

    date: date
    value: float = Field(allow_inf_nan=False)


class SeriesMeta(BaseModel):
    """Metadados de uma série ingerida."""

    key: str
    name: str
    unit: str
    source: str
    source_url: str


class SourceStatus(BaseModel):
    """Estado de uma fonte: veio da API ao vivo, do cache Parquet ou está indisponível."""

    key: str
    label: str
    mode: SourceMode
    synced_at: datetime | None = None
    detail: str = ""

    @field_validator("label")
    @classmethod
    def _label_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("label vazio")
        return v


class SecurityRecord(BaseModel):
    """Linha padronizada do consolidado de segurança pública / educação."""

    indicador: str
    uf: str = "BR"
    ano: int = Field(ge=1990, le=2100)
    valor: float
    unidade: str
    fonte: str
    qualidade: Literal["oficial", "aproximado"] = "aproximado"


_POINTS = TypeAdapter(list[SeriesPoint])


def validate_series(df: pd.DataFrame) -> pd.DataFrame:
    """Valida o contrato ``date`` (datetime) + ``value`` (float) e normaliza a série.

    Remove nulos/duplicados, ordena por data e levanta ``ValueError`` se o contrato
    for violado (colunas ausentes ou nenhuma linha válida).
    """
    missing = {"date", "value"} - set(df.columns)
    if missing:
        raise ValueError(f"colunas ausentes no contrato da série: {sorted(missing)}")

    clean = df[["date", "value"]].copy()
    clean["date"] = pd.to_datetime(clean["date"], errors="coerce")
    clean["value"] = pd.to_numeric(clean["value"], errors="coerce")
    clean = clean.dropna().drop_duplicates(subset="date", keep="last").sort_values("date")
    if clean.empty:
        raise ValueError("série sem nenhum ponto válido")

    # Validação linha a linha (tipos e valores finitos)
    _POINTS.validate_python(
        [{"date": d.date(), "value": float(v)} for d, v in zip(clean["date"], clean["value"], strict=True)]
    )
    return clean.reset_index(drop=True)


def validate_records(rows: list[dict]) -> list[SecurityRecord]:
    """Valida registros do consolidado de segurança/educação."""
    return TypeAdapter(list[SecurityRecord]).validate_python(rows)
