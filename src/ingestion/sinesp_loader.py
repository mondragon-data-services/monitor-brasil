"""ETL de segurança pública (Sinesp VDE) e educação (INEP/IDEB) com fallback determinístico.

Fluxo:
1. Planilhas em ``data/raw/`` (ou upload na interface) são padronizadas e salvas em
   ``data/processed/sinesp_vde.parquet``.
2. Se não houver planilha, ``ensure_consolidated`` grava
   ``data/processed/seguranca_educacao_consolidado.parquet`` com a série de referência.

ATENÇÃO — qualidade dos dados do consolidado: os valores nacionais de MVI, feminicídios, Ideb, abandono
e nem-nem foram compilados de publicações do Anuário Brasileiro de Segurança Pública (FBSP), do INEP e do IBGE.
Todos recebem ``qualidade="aproximado"`` e a interface avisa isso ao cidadão. Para dados oficiais
auditáveis, use o upload do Sinesp VDE.
"""
from __future__ import annotations

import logging
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

import pandas as pd

from src.config import CONSOLIDATED_PARQUET, DATA_PROCESSED, DATA_RAW, SINESP_PARQUET
from src.contracts import validate_records
from src.ingestion.cache import FetchResult

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES: tuple[str, ...] = (".parquet", ".csv", ".xlsx")

# População residente por UF — Censo 2022 (IBGE), em habitantes
UF_POPULATION: dict[str, int] = {
    "AC": 830_018, "AL": 3_127_683, "AP": 733_759, "AM": 3_941_613, "BA": 14_141_626, "CE": 8_794_957,
    "DF": 2_817_381, "ES": 3_833_712, "GO": 7_056_495, "MA": 6_776_699, "MT": 3_658_649, "MS": 2_757_013,
    "MG": 20_539_989, "PA": 8_120_131, "PB": 3_974_687, "PR": 11_444_380, "PE": 9_058_931, "PI": 3_271_199,
    "RJ": 16_055_174, "RN": 3_302_729, "RS": 10_882_965, "RO": 1_581_196, "RR": 636_707, "SC": 7_610_361,
    "SP": 44_411_238, "SE": 2_210_004, "TO": 1_511_460,
}
BRAZIL_POPULATION: int = sum(UF_POPULATION.values())

_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "uf": ("uf", "sigla_uf", "estado", "unidade_federacao", "unidade_da_federacao"),
    "municipio": ("municipio", "cidade", "nome_municipio"),
    "evento": ("evento", "tipo_evento", "crime", "indicador", "ocorrencia"),
    "mes_ano": ("mes_ano", "data_referencia", "data", "mes", "periodo", "ano_mes", "ano"),
    "total_vitimas": ("total_vitimas", "vitimas", "total_peso", "peso", "total", "quantidade", "ocorrencias"),
}


def _norm(text: object) -> str:
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().strip()
    return "_".join(s.replace("-", " ").replace("/", " ").split())


def standardize_sinesp(df: pd.DataFrame) -> pd.DataFrame:
    """Padroniza colunas para ``uf, municipio, evento, mes_ano (datetime), total_vitimas (float)``."""
    renamed: dict[str, str] = {}
    normalized = {_norm(c): c for c in df.columns}
    for target, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized and normalized[alias] not in renamed:
                renamed[normalized[alias]] = target
                break
    out = df.rename(columns=renamed)
    missing = {"uf", "evento", "mes_ano", "total_vitimas"} - set(out.columns)
    if missing:
        raise ValueError(f"planilha fora do padrão Sinesp VDE — colunas ausentes: {sorted(missing)}")
    if "municipio" not in out.columns:
        out["municipio"] = ""
    out = out[["uf", "municipio", "evento", "mes_ano", "total_vitimas"]].copy()
    out["uf"] = out["uf"].astype(str).str.strip().str.upper().str[:2]
    out["evento"] = out["evento"].astype(str).str.strip()
    parsed = pd.to_datetime(out["mes_ano"], errors="coerce", dayfirst=True)
    # anos soltos (ex.: 2023) viram 01/01/2023
    years = pd.to_numeric(out["mes_ano"], errors="coerce")
    parsed = parsed.where(~years.between(1990, 2100), pd.to_datetime(years.astype("Int64").astype(str) + "-01-01", errors="coerce"))
    out["mes_ano"] = parsed
    out["total_vitimas"] = pd.to_numeric(out["total_vitimas"], errors="coerce")
    out = out.dropna(subset=["mes_ano", "total_vitimas"])
    out = out[out["uf"].isin(UF_POPULATION)]
    if out.empty:
        raise ValueError("nenhuma linha válida após a padronização do Sinesp")
    return out.reset_index(drop=True)


def read_table(source: str | Path | BinaryIO, name: str | None = None) -> pd.DataFrame:
    """Lê .parquet/.csv/.xlsx (caminho ou objeto de arquivo do upload do Streamlit)."""
    label = name or (source.name if hasattr(source, "name") else str(source))
    suffix = Path(label).suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(source)
    if suffix == ".xlsx":
        return pd.read_excel(source, engine="openpyxl")
    if suffix == ".csv":
        try:
            return pd.read_csv(source, sep=None, engine="python", encoding="utf-8")
        except UnicodeDecodeError:
            if hasattr(source, "seek"):
                source.seek(0)
            return pd.read_csv(source, sep=None, engine="python", encoding="latin-1")
    raise ValueError(f"formato não suportado: {suffix} (use {', '.join(SUPPORTED_SUFFIXES)})")


def process_upload(source: str | Path | BinaryIO, name: str | None = None, out_path: Path | None = None) -> pd.DataFrame:
    """Processa uma planilha Sinesp VDE, padroniza e salva em ``sinesp_vde.parquet``."""
    df = standardize_sinesp(read_table(source, name))
    path = out_path or SINESP_PARQUET
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def load_raw_folder(raw_dir: Path | None = None, out_path: Path | None = None) -> pd.DataFrame | None:
    """Consolida todas as planilhas válidas de ``data/raw``; ``None`` se a pasta estiver vazia."""
    folder = raw_dir or DATA_RAW
    frames: list[pd.DataFrame] = []
    for f in sorted(folder.glob("*")) if folder.exists() else []:
        if f.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            frames.append(standardize_sinesp(read_table(f)))
        except Exception as exc:
            log.warning("arquivo ignorado %s: %s", f.name, exc)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).drop_duplicates()
    path = out_path or SINESP_PARQUET
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def load_sinesp(path: Path | None = None) -> pd.DataFrame | None:
    p = path or SINESP_PARQUET
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception as exc:
        log.warning("sinesp_vde.parquet ilegível: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Indicadores derivados do Sinesp (dados oficiais enviados pelo usuário)
# --------------------------------------------------------------------------- #
_MVI_EVENTS: tuple[str, ...] = ("homicidio doloso", "latrocinio", "lesao corporal seguida de morte",
                                "morte por intervencao", "roubo seguido de morte")


def _event_class(evento: str) -> str | None:
    e = _norm(evento).replace("_", " ")
    if "tentativa" in e:
        return None
    if "feminicidio" in e:
        return "feminicidio_total"
    if "estupro de vulneravel" in e:
        return "estupro_vulneravel_total"
    if any(k in e for k in _MVI_EVENTS):
        return "mvi_total"
    return None


def sinesp_to_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega o Sinesp em indicadores anuais por UF e Brasil, no esquema do consolidado."""
    work = df.copy()
    work["indicador"] = work["evento"].map(_event_class)
    work = work.dropna(subset=["indicador"])
    if work.empty:
        return pd.DataFrame(columns=["indicador", "uf", "ano", "valor", "unidade", "fonte", "qualidade"])
    work["ano"] = work["mes_ano"].dt.year
    by_uf = work.groupby(["indicador", "uf", "ano"], as_index=False)["total_vitimas"].sum().rename(columns={"total_vitimas": "valor"})
    br = by_uf.groupby(["indicador", "ano"], as_index=False)["valor"].sum().assign(uf="BR")
    totals = pd.concat([by_uf, br], ignore_index=True)

    rows = [totals.assign(unidade="vítimas")]
    rate = totals[totals["indicador"] == "mvi_total"].copy()
    rate["valor"] = rate.apply(lambda r: r["valor"] / (BRAZIL_POPULATION if r["uf"] == "BR" else UF_POPULATION[r["uf"]]) * 100_000, axis=1)
    rows.append(rate.assign(indicador="mvi_taxa", unidade="por 100 mil hab."))
    out = pd.concat(rows, ignore_index=True)
    out["fonte"] = "Sinesp VDE (planilha enviada)"
    out["qualidade"] = "oficial"
    return out[["indicador", "uf", "ano", "valor", "unidade", "fonte", "qualidade"]]


# --------------------------------------------------------------------------- #
# Consolidado determinístico (fallback offline)
# --------------------------------------------------------------------------- #
_FBSP = "Anuário Brasileiro de Segurança Pública (FBSP) / Sinesp VDE"
_INEP = "INEP — Ideb"
_CENSO = "INEP — Censo Escolar"
_PNAD = "IBGE — PNAD Contínua Educação"


def _series(indicador: str, values: dict[int, float], unidade: str, fonte: str, uf: str = "BR") -> list[dict]:
    return [
        {"indicador": indicador, "uf": uf, "ano": y, "valor": float(v), "unidade": unidade, "fonte": fonte,
         "qualidade": "aproximado"}
        for y, v in values.items()
    ]


def consolidated_rows() -> list[dict]:
    """Séries de referência 2016–2024 (aproximadas) — ver aviso no topo do módulo."""
    rows: list[dict] = []
    rows += _series("mvi_total", {2016: 61619, 2017: 64079, 2018: 57341, 2019: 47742, 2020: 50033, 2021: 47847,
                                  2022: 47398, 2023: 46328, 2024: 44127}, "vítimas", _FBSP)
    rows += _series("mvi_taxa", {2016: 29.9, 2017: 30.9, 2018: 27.5, 2019: 22.7, 2020: 23.6, 2021: 22.4,
                                 2022: 23.3, 2023: 22.8, 2024: 20.8}, "por 100 mil hab.", _FBSP)
    rows += _series("feminicidio_total", {2016: 929, 2017: 1075, 2018: 1229, 2019: 1326, 2020: 1350, 2021: 1341,
                                          2022: 1437, 2023: 1467, 2024: 1492}, "vítimas", _FBSP)
    rows += _series("feminicidio_taxa", {2016: 0.9, 2017: 1.0, 2018: 1.1, 2019: 1.2, 2020: 1.2, 2021: 1.2,
                                         2022: 1.4, 2023: 1.4, 2024: 1.4}, "por 100 mil mulheres", _FBSP)
    rows += _series("estupro_vulneravel_total", {2019: 48000, 2020: 44000, 2021: 49000, 2022: 56800, 2023: 61000,
                                                 2024: 67000}, "vítimas", _FBSP)
    # Foto por UF (taxa de MVI por 100 mil hab., ~2023) — ordem de grandeza
    uf_mvi = {"AP": 45, "BA": 46, "PE": 36, "SE": 36, "RN": 34, "PA": 35, "AM": 33, "AL": 32, "CE": 30, "RR": 30,
              "RJ": 28, "AC": 27, "MA": 27, "PB": 27, "RO": 26, "GO": 25, "TO": 23, "ES": 22, "MS": 22, "MT": 22,
              "PI": 20, "PR": 19, "RS": 18, "MG": 14, "DF": 13, "SC": 9, "SP": 8}
    for uf, v in uf_mvi.items():
        rows += _series("mvi_taxa", {2023: v}, "por 100 mil hab.", _FBSP, uf=uf)
    # Educação
    anos_ideb = [2015, 2017, 2019, 2021, 2023]
    for ind, vals in {
        "ideb_anos_iniciais": [5.5, 5.8, 5.9, 5.8, 6.0],
        "ideb_anos_finais": [4.5, 4.7, 4.9, 5.1, 5.0],
        "ideb_ensino_medio": [3.7, 3.8, 4.2, 4.2, 4.3],
        "ideb_meta_anos_iniciais": [5.2, 5.5, 5.7, 6.0, 6.2],
        "ideb_meta_anos_finais": [4.4, 4.7, 5.0, 5.2, 5.5],
        "ideb_meta_ensino_medio": [4.3, 4.7, 5.0, 5.2, 5.4],
    }.items():
        rows += _series(ind, dict(zip(anos_ideb, vals, strict=True)), "nota (0–10)", _INEP)
    rows += _series("abandono_ensino_medio", {2016: 6.8, 2017: 6.1, 2018: 6.1, 2019: 5.7, 2020: 2.3, 2021: 3.5,
                                              2022: 5.0, 2023: 4.7}, "%", _CENSO)
    rows += _series("nem_nem", {2016: 23.5, 2017: 24.0, 2018: 23.0, 2019: 22.4, 2020: 25.5, 2021: 25.8,
                                2022: 22.3, 2023: 20.5, 2024: 19.8}, "% dos jovens de 15 a 29 anos", _PNAD)
    return rows


def build_consolidated_fallback(path: Path | None = None) -> pd.DataFrame:
    """Gera (determinístico) e salva o consolidado de segurança/educação."""
    records = validate_records(consolidated_rows())
    df = pd.DataFrame([r.model_dump() for r in records])
    target = path or CONSOLIDATED_PARQUET
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target, index=False)
    return df


def ensure_consolidated(path: Path | None = None, raw_dir: Path | None = None) -> pd.DataFrame:
    """Garante o consolidado em disco. Cria automaticamente quando ``data/raw`` está vazio."""
    target = path or CONSOLIDATED_PARQUET
    if target.exists():
        try:
            return pd.read_parquet(target)
        except Exception as exc:
            log.warning("consolidado corrompido, regenerando: %s", exc)
    return build_consolidated_fallback(target)


def _refresh_from_raw_if_newer(raw_dir: Path | None, sinesp_path: Path | None) -> pd.DataFrame | None:
    """Reprocessa ``data/raw`` só se houver planilha mais nova que o Parquet padronizado."""
    folder = raw_dir or DATA_RAW
    target = sinesp_path or SINESP_PARQUET
    raw_files = [f for f in folder.glob("*") if f.suffix.lower() in SUPPORTED_SUFFIXES] if folder.exists() else []
    if raw_files and (not target.exists() or max(f.stat().st_mtime for f in raw_files) > target.stat().st_mtime):
        fresh = load_raw_folder(folder, target)
        if fresh is not None:
            return fresh
    return load_sinesp(target)


def get_security_education(sinesp_path: Path | None = None, consolidated_path: Path | None = None,
                           raw_dir: Path | None = None) -> FetchResult:
    """Dataset final de segurança/educação.

    Sinesp (oficial, de ``data/raw`` ou upload) sobrepõe os indicadores que cobre; o restante vem do
    consolidado de referência. ``mode`` = ``cache`` com Sinesp oficial, ``fallback`` só com consolidado.
    """
    base = ensure_consolidated(consolidated_path, raw_dir)
    sinesp = _refresh_from_raw_if_newer(raw_dir, sinesp_path)
    if sinesp is None or sinesp.empty:
        return FetchResult("seguranca_educacao", base, "fallback", datetime.fromtimestamp(
            (consolidated_path or CONSOLIDATED_PARQUET).stat().st_mtime), "consolidado de referência (aproximado)")
    derived = sinesp_to_indicators(sinesp)
    if derived.empty:
        return FetchResult("seguranca_educacao", base, "fallback", None, "Sinesp sem eventos reconhecidos")
    overridden = set(derived["indicador"])
    merged = pd.concat([base[~base["indicador"].isin(overridden)], derived], ignore_index=True)
    return FetchResult("seguranca_educacao", merged, "cache",
                       datetime.fromtimestamp((sinesp_path or SINESP_PARQUET).stat().st_mtime),
                       "Sinesp VDE oficial + consolidado para o restante")
