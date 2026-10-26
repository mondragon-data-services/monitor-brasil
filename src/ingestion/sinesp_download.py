"""Download direto das planilhas anuais do Sinesp VDE (Ministério da Justiça e Segurança Pública).

Fonte: ``https://www.gov.br/mj/.../estatistica/download/dnsp-base-de-dados/bancovde-{ano}.xlsx`` (≈ 25 MB por ano).
O servidor bloqueia requisições HEAD e clientes sem cabeçalhos de navegador (HTTP 403), por isso o GET envia
``User-Agent``, ``Accept-Language`` e ``Referer`` de navegador.

Cada ano é baixado para uma pasta temporária, reduzido a ``uf × evento × mês`` (poucos milhares de linhas) e gravado em
``data/processed/sinesp_vde_{ano}.parquet``; a planilha original é descartada. Depois todos os anos são combinados em
``data/processed/sinesp_vde.parquet`` — o arquivo que o restante do app já lê.
"""
from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from src.config import DATA_PROCESSED, SINESP_PARQUET
from src.ingestion.http_client import IngestionError
from src.ingestion.sinesp_loader import _event_class, standardize_sinesp

log = logging.getLogger(__name__)

VDE_URL_TEMPLATE = (
    "https://www.gov.br/mj/pt-br/assuntos/sua-seguranca/seguranca-publica/estatistica/download/"
    "dnsp-base-de-dados/bancovde-{year}.xlsx"
)
VDE_FIRST_YEAR = 2015
VDE_REFERER = "https://www.gov.br/mj/pt-br/assuntos/sua-seguranca/seguranca-publica/estatistica/dados-nacionais-1"
VDE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": VDE_REFERER,
}
CURRENT_YEAR_MAX_AGE = timedelta(days=7)  # o ano em curso é revisado com frequência

ProgressFn = Callable[[str, float], None]


@dataclass
class YearResult:
    year: int
    status: str  # "baixado" | "cache" | "indisponível" | "erro"
    rows: int = 0
    detail: str = ""


@dataclass
class SinesSyncReport:
    years: list[YearResult] = field(default_factory=list)
    combined_rows: int = 0

    @property
    def ok(self) -> bool:
        return self.combined_rows > 0


def year_parquet(year: int, base: Path | None = None) -> Path:
    return (base or DATA_PROCESSED) / f"sinesp_vde_{year}.parquet"


def download_year(year: int, dest: Path, session: requests.Session | None = None, timeout: tuple[float, float] = (10, 180)) -> Path:
    """Baixa ``bancovde-{ano}.xlsx`` por streaming para ``dest``; levanta ``IngestionError`` se 404/ inválido."""
    sess = session or requests.Session()
    url = VDE_URL_TEMPLATE.format(year=year)
    part = dest.with_suffix(".part")
    try:
        with sess.get(url, headers=VDE_HEADERS, timeout=timeout, stream=True) as resp:
            if resp.status_code == 404:
                raise IngestionError(f"planilha de {year} ainda não publicada (HTTP 404)")
            if resp.status_code >= 400:
                raise IngestionError(f"HTTP {resp.status_code} ao baixar {url}")
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
    except requests.RequestException as exc:
        raise IngestionError(f"falha de rede ao baixar {year}: {exc}") from exc
    with open(part, "rb") as fh:
        magic = fh.read(2)
    if magic != b"PK":  # .xlsx é um zip; fecha o arquivo antes de apagar (Windows)
        part.unlink(missing_ok=True)
        raise IngestionError(f"o arquivo de {year} não é um .xlsx válido")
    part.replace(dest)
    return dest


def read_vde_xlsx(path: Path) -> pd.DataFrame:
    """Lê a planilha (calamine é ~10× mais rápido que openpyxl; openpyxl é o fallback)."""
    try:
        return pd.read_excel(path, engine="calamine")
    except ImportError:
        log.warning("python-calamine ausente; usando openpyxl (lento para arquivos de 25 MB)")
        return pd.read_excel(path, engine="openpyxl")


def aggregate_vde(raw: pd.DataFrame) -> pd.DataFrame:
    """Reduz a planilha bruta a ``uf × evento × mês`` com os eventos relevantes, no esquema padronizado."""
    if "evento" not in raw.columns:
        raise IngestionError("planilha sem a coluna 'evento' — layout do Sinesp mudou?")
    keep = raw[raw["evento"].map(_event_class).notna()]
    if keep.empty:
        raise IngestionError("nenhum evento relevante na planilha")
    std = standardize_sinesp(keep)
    return std.groupby(["uf", "evento", "mes_ano"], as_index=False)["total_vitimas"].sum().assign(municipio="")[
        ["uf", "municipio", "evento", "mes_ano", "total_vitimas"]
    ]


def _is_fresh(path: Path, year: int, now: datetime) -> bool:
    if not path.exists():
        return False
    if year < now.year:  # anos fechados não mudam (revisões pontuais: use force=True)
        return True
    return now - datetime.fromtimestamp(path.stat().st_mtime) < CURRENT_YEAR_MAX_AGE


def combine_years(base: Path | None = None, out_path: Path | None = None) -> pd.DataFrame:
    """Junta os Parquet anuais em ``sinesp_vde.parquet``."""
    folder = base or DATA_PROCESSED
    frames = [pd.read_parquet(p) for p in sorted(folder.glob("sinesp_vde_[0-9][0-9][0-9][0-9].parquet"))]
    if not frames:
        return pd.DataFrame(columns=["uf", "municipio", "evento", "mes_ano", "total_vitimas"])
    df = pd.concat(frames, ignore_index=True)
    df = df[df["evento"].map(_event_class).notna()]  # descarta eventos de arquivos antigos sem uso no painel
    target = out_path or (SINESP_PARQUET if base is None else folder / "sinesp_vde.parquet")
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target, index=False)
    return df


def sync_sinesp_vde(
    years: list[int] | None = None,
    force: bool = False,
    base: Path | None = None,
    session: requests.Session | None = None,
    progress: ProgressFn | None = None,
) -> SinesSyncReport:
    """Baixa (se preciso) cada ano do Sinesp VDE, agrega e combina. Falha de um ano não derruba os demais."""
    now = datetime.now()
    wanted = years or list(range(VDE_FIRST_YEAR, date.today().year + 1))
    report = SinesSyncReport()
    sess = session or requests.Session()
    folder = base or DATA_PROCESSED
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sinesp_vde_") as tmp:
        for i, year in enumerate(wanted):
            def say(msg: str, frac: float = 0.0, _i: int = i) -> None:
                if progress:
                    progress(msg, (_i + frac) / len(wanted))

            target = year_parquet(year, folder)
            if not force and _is_fresh(target, year, now):
                report.years.append(YearResult(year, "cache", len(pd.read_parquet(target))))
                say(f"{year}: já em cache", 1.0)
                continue
            try:
                say(f"{year}: baixando (~25 MB)…", 0.05)
                xlsx = download_year(year, Path(tmp) / f"bancovde-{year}.xlsx", sess)
                say(f"{year}: lendo e agregando…", 0.5)
                agg = aggregate_vde(read_vde_xlsx(xlsx))
                agg.to_parquet(target, index=False)
                xlsx.unlink(missing_ok=True)
                report.years.append(YearResult(year, "baixado", len(agg)))
            except IngestionError as exc:
                status = "indisponível" if "404" in str(exc) else "erro"
                report.years.append(YearResult(year, status, detail=str(exc)))
                log.warning("Sinesp VDE %d: %s", year, exc)
            except Exception as exc:  # planilha corrompida/layout novo não pode derrubar o app
                report.years.append(YearResult(year, "erro", detail=f"{type(exc).__name__}: {exc}"))
                log.warning("Sinesp VDE %d falhou: %s", year, exc)
            say(f"{year}: concluído", 1.0)
    combined = combine_years(base)
    report.combined_rows = len(combined)
    return report
