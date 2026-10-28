"""Funções puras e testáveis de estatística econômica (sem Streamlit, sem I/O)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

RecessionStatus = Literal["Alerta de Recessão Técnica", "Desaceleração", "Expansão", "Sem dados"]


def accumulated_12m(df: pd.DataFrame, value_col: str = "value") -> pd.DataFrame:
    """Acumulado em 12 meses por juros compostos: ``(Π(1 + taxa_i/100) − 1) × 100``.

    Exige 12 observações mensais consecutivas; as 11 primeiras linhas ficam ``NaN``.
    """
    out = df.sort_values("date").reset_index(drop=True).copy()
    factor = 1.0 + out[value_col] / 100.0
    out[value_col] = (factor.rolling(12).apply(np.prod, raw=True) - 1.0) * 100.0
    return out


def real_interest_fisher(selic_aa: float | pd.Series, ipca_12m: float | pd.Series) -> float | pd.Series:
    """Juro real ex-post (Fisher): ``((1 + Selic/100) / (1 + IPCA12m/100) − 1) × 100``."""
    return ((1.0 + selic_aa / 100.0) / (1.0 + ipca_12m / 100.0) - 1.0) * 100.0


def rate_per_100k(total: float | pd.Series, population: float | pd.Series) -> float | pd.Series:
    """Taxa padronizada por 100 mil habitantes."""
    return total / population * 100_000.0


def rolling_mean(df: pd.DataFrame, window: int = 3, value_col: str = "value") -> pd.Series:
    """Média móvel de ``window`` períodos."""
    return df.sort_values("date")[value_col].rolling(window).mean()


def yoy_change_pp(df: pd.DataFrame, periods: int, value_col: str = "value") -> pd.Series:
    """Variação em pontos percentuais contra o mesmo período do ano anterior.

    ``periods`` = 12 (série mensal), 4 (trimestral), 1 (anual).
    """
    return df.sort_values("date")[value_col].diff(periods)


def monthly_last(df: pd.DataFrame, value_col: str = "value") -> pd.DataFrame:
    """Reduz série diária ao último valor de cada mês (data = 1º dia do mês)."""
    tmp = df.sort_values("date").copy()
    tmp["date"] = tmp["date"].dt.to_period("M").dt.to_timestamp()
    return tmp.groupby("date", as_index=False).last()[["date", value_col]]


def build_real_interest(selic: pd.DataFrame, ipca_monthly: pd.DataFrame) -> pd.DataFrame:
    """Combina Selic (mensal) e IPCA 12m e devolve ``date, selic, ipca_12m, juro_real``."""
    s = monthly_last(selic).rename(columns={"value": "selic"})
    i = accumulated_12m(ipca_monthly).rename(columns={"value": "ipca_12m"})
    merged = s.merge(i[["date", "ipca_12m"]], on="date", how="inner").dropna()
    merged["juro_real"] = real_interest_fisher(merged["selic"], merged["ipca_12m"])
    return merged.reset_index(drop=True)


def detect_recession(values: list[float] | pd.Series, consecutive: int = 2) -> RecessionStatus:
    """Classifica a conjuntura a partir de variações trimestrais (PIB) ou semestre mensal (IBC-Br).

    * ``Alerta de Recessão Técnica``: as últimas ``consecutive`` variações são todas negativas;
    * ``Desaceleração``: a última é negativa, ou o ritmo caiu e ficou abaixo de 0,3;
    * ``Expansão``: demais casos.
    """
    vals = [float(v) for v in pd.Series(values).dropna()]
    if len(vals) < consecutive:
        return "Sem dados"
    if all(v < 0 for v in vals[-consecutive:]):
        return "Alerta de Recessão Técnica"
    if vals[-1] < 0 or (len(vals) >= 2 and vals[-1] < vals[-2] and vals[-1] < 0.3):
        return "Desaceleração"
    return "Expansão"


def ibcbr_monthly_changes(ibcbr: pd.DataFrame) -> pd.Series:
    """Variação % mês a mês do IBC-Br dessazonalizado."""
    return ibcbr.sort_values("date")["value"].pct_change() * 100.0


def period_start(option: str | None, today: pd.Timestamp | None = None) -> pd.Timestamp | None:
    """Data inicial do recorte: ``"2y"`` = últimos 2 anos; ``"AAAA-MM-DD"`` = a partir da data; ``None`` = tudo."""
    if option is None:
        return None
    now = today or pd.Timestamp.today().normalize()
    return now - pd.DateOffset(years=2) if option == "2y" else pd.Timestamp(option)


def filter_period(df: pd.DataFrame, option: str | None, today: pd.Timestamp | None = None,
                  date_col: str = "date") -> pd.DataFrame:
    """Aplica ``period_start`` a um DataFrame com coluna de data."""
    start = period_start(option, today)
    if start is None or df.empty:
        return df
    return df[df[date_col] >= start]


@dataclass(frozen=True)
class Delta:
    """Último valor e variação contra a observação ``periods`` períodos antes."""

    latest: float
    previous: float
    change: float
    date: pd.Timestamp


def latest_with_delta(df: pd.DataFrame, periods: int = 12, value_col: str = "value") -> Delta | None:
    """Último valor válido e sua variação absoluta (p.p. ou unidade) contra ``periods`` períodos atrás."""
    clean = df.dropna(subset=[value_col]).sort_values("date")
    if len(clean) <= periods:
        if clean.empty:
            return None
        row = clean.iloc[-1]
        return Delta(float(row[value_col]), float("nan"), float("nan"), row["date"])
    last, prev = clean.iloc[-1], clean.iloc[-1 - periods]
    return Delta(float(last[value_col]), float(prev[value_col]),
                 float(last[value_col] - prev[value_col]), last["date"])


# --------------------------------------------------------------------------- #
# Preços do dia a dia (IPCA por item e gasolina)
# --------------------------------------------------------------------------- #
def split_items(items: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """DataFrame longo ``date, item, value`` -> um DataFrame ``date, value`` (variação mensal %) por item."""
    if items is None or items.empty:
        return {}
    return {k: g[["date", "value"]].sort_values("date").reset_index(drop=True) for k, g in items.groupby("item")}


def cumulative_index(monthly_var: pd.DataFrame, base: float = 100.0) -> pd.DataFrame:
    """Número-índice a partir de variações mensais (%): ``base × Π(1 + v/100)``; a base é o mês anterior à 1ª variação."""
    out = monthly_var.sort_values("date").reset_index(drop=True).copy()
    out["value"] = base * (1.0 + out["value"] / 100.0).cumprod()
    return out


def value_days_ago(df: pd.DataFrame, days: int = 365, tolerance_days: int = 10, value_col: str = "value") -> float | None:
    """Valor da observação mais próxima de ``days`` dias antes da última (série semanal/irregular); ``None`` se não houver."""
    clean = df.dropna(subset=[value_col]).sort_values("date")
    if clean.empty:
        return None
    target = clean["date"].iloc[-1] - pd.Timedelta(days=days)
    idx = (clean["date"] - target).abs().idxmin()
    row = clean.loc[idx]
    return float(row[value_col]) if abs((row["date"] - target).days) <= tolerance_days else None


def real_minimum_wage(min_wage: pd.DataFrame, ipca_monthly: pd.DataFrame) -> pd.DataFrame:
    """Salário mínimo em reais de hoje: ``nominal × índice_IPCA(último mês) / índice_IPCA(mês)``.

    Devolve ``date, nominal, real`` mensal. O índice nasce das variações mensais do IPCA (``cumulative_index``); o salário
    nominal é propagado para frente (ffill) entre reajustes. Meses sem IPCA ficam de fora.
    """
    idx = cumulative_index(ipca_monthly)[["date", "value"]].rename(columns={"value": "idx"})
    sm = min_wage.sort_values("date")[["date", "value"]].rename(columns={"value": "nominal"})
    merged = pd.merge_asof(idx.sort_values("date"), sm, on="date", direction="backward").dropna(subset=["nominal"])
    if merged.empty:
        return pd.DataFrame(columns=["date", "nominal", "real"])
    merged["real"] = merged["nominal"] * merged["idx"].iloc[-1] / merged["idx"]
    return merged[["date", "nominal", "real"]].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Dengue (SINAN): semanas completas, acumulados e incidência
# --------------------------------------------------------------------------- #
def dengue_national(dengue: pd.DataFrame, uf: str = "BR") -> pd.DataFrame:
    """Casos/graves/óbitos por semana para o Brasil (soma das UFs) ou para uma UF."""
    if dengue is None or dengue.empty:
        return pd.DataFrame(columns=["date", "ano", "semana", "casos", "graves", "obitos"])
    base = dengue if uf == "BR" else dengue[dengue["uf"] == uf]
    return (base.groupby(["date", "ano", "semana"], as_index=False)[["casos", "graves", "obitos"]].sum()
            .sort_values("date").reset_index(drop=True))


def dengue_last_complete_week(dengue: pd.DataFrame, trim_weeks: int = 3) -> tuple[int, int] | None:
    """(ano, semana) da última semana considerada completa: descarta as ``trim_weeks`` mais recentes (atraso de digitação)."""
    if dengue is None or dengue.empty:
        return None
    nat = dengue_national(dengue)
    keys = list(zip(nat["ano"], nat["semana"], strict=True))
    if not keys:
        return None
    idx = max(0, len(keys) - 1 - trim_weeks)
    return int(keys[idx][0]), int(keys[idx][1])


def dengue_cumulative(dengue: pd.DataFrame, year: int, upto_week: int, uf: str = "BR") -> dict[str, int]:
    """Total de casos, graves e óbitos do ano até a semana epidemiológica ``upto_week`` (inclusive)."""
    nat = dengue_national(dengue, uf)
    sel = nat[(nat["ano"] == year) & (nat["semana"] <= upto_week)]
    return {"casos": int(sel["casos"].sum()), "graves": int(sel["graves"].sum()), "obitos": int(sel["obitos"].sum())}


def dengue_incidence_by_uf(dengue: pd.DataFrame, year: int, upto_week: int, population: dict[str, int]) -> pd.DataFrame:
    """Casos acumulados e incidência por 100 mil habitantes por UF (``uf, casos, obitos, incidencia``)."""
    if dengue is None or dengue.empty:
        return pd.DataFrame(columns=["uf", "casos", "obitos", "incidencia"])
    sel = dengue[(dengue["ano"] == year) & (dengue["semana"] <= upto_week)]
    out = sel.groupby("uf", as_index=False)[["casos", "obitos"]].sum()
    out["incidencia"] = [rate_per_100k(c, population[u]) if u in population else float("nan") for u, c in zip(out["uf"], out["casos"], strict=True)]
    return out.dropna(subset=["incidencia"]).sort_values("incidencia", ascending=False).reset_index(drop=True)


def rolling_sum(df: pd.DataFrame, window: int = 12, value_col: str = "value") -> pd.DataFrame:
    """Soma móvel (ex.: saldo de vagas em 12 meses); as primeiras ``window-1`` linhas são descartadas."""
    out = df.sort_values("date").reset_index(drop=True).copy()
    out[value_col] = out[value_col].rolling(window).sum()
    return out.dropna(subset=[value_col]).reset_index(drop=True)
