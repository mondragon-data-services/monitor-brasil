"""Ferramentas somente-leitura do agente (function calling) sobre o catálogo fixo de indicadores.

Por que isto e não RAG vetorial: os dados são séries numéricas estruturadas e pequenas. Recuperar "trechos parecidos"
por embeddings perde precisão (datas, defasagens, variações) e convida o modelo a inventar números. Aqui o modelo apenas
ESCOLHE qual consulta fazer; os números (último valor, variação em 12 meses, mínimo/máximo, séries) são calculados em
código e devolvidos prontos. As ferramentas aceitam só valores de listas fechadas (enum) — nenhum texto livre chega aos
dados, o que também fecha a porta para injeção via planilhas enviadas pelo usuário.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from src.agent.context import AREAS, CATALOG, DataContext
from src.config import EXPLAINERS

MAX_POINTS = 60
MAX_TOOL_OUTPUT_CHARS = 12000
UF_METRICS = ("mvi_taxa", "mvi_total", "feminicidio_total", "estupro_vulneravel_total")

_EXPLAINER_OF_AREA = {"fiscal": ("inflacao", "divida_juros"), "trabalho": ("desemprego", "renda_endividamento", "recessao"),
                      "seguranca": ("mvi", "vulneraveis"), "educacao": ("ideb", "evasao")}
_EXPLAINER_OF_KEY = {
    "ipca_12m": "inflacao", "ipca_alimentacao_12m": "inflacao", "selic_meta": "divida_juros", "juro_real": "divida_juros",
    "divida_bruta": "divida_juros", "divida_liquida": "divida_juros", "desocupacao": "desemprego",
    "subutilizacao": "desemprego", "rendimento_real": "renda_endividamento", "endividamento_familias": "renda_endividamento",
    "pib_4tri": "recessao", "pib_trimestral": "recessao", "ibcbr_12m": "recessao", "mvi_taxa": "mvi",
    "feminicidio_total": "vulneraveis", "estupro_vulneravel_total": "vulneraveis", "ideb_anos_iniciais": "ideb",
    "ideb_anos_finais": "ideb", "ideb_ensino_medio": "ideb", "abandono_ensino_medio": "evasao", "nem_nem": "evasao",
}

METHODOLOGY = (
    "Inflação acumulada em 12 meses = produto dos fatores mensais (juros compostos), não soma simples. "
    "Juro real ex-post = (1+Selic)/(1+IPCA 12m) − 1 (equação de Fisher). "
    "Mortes violentas (MVI) = homicídio doloso + latrocínio + lesão corporal seguida de morte + morte por intervenção de "
    "agente do Estado, por 100 mil habitantes (população do Censo 2022 em todos os anos). "
    "Séries de segurança só usam anos completos (12 meses). "
    "Recessão técnica = dois trimestres seguidos de queda do PIB (variação trimestre contra trimestre anterior, dessazonalizada). "
    "PNAD Contínua usa trimestres móveis (ex.: jun-jul-ago). Variação de 12 meses compara com a observação 12 meses antes."
)
QUALITY_NOTE = (
    "Segurança pública: dados oficiais do Sinesp VDE (Ministério da Justiça) quando baixados; em 2015–2017 a cobertura dos "
    "estados pode ser incompleta (afeta sobretudo estupro). Ideb, abandono escolar e 'nem-nem' vêm de um consolidado de "
    "referência APROXIMADO (INEP/IBGE) — cite sempre essa ressalva ao usá-los."
)


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _num(x: float, nd: int = 2) -> float | None:
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), nd)


def _when(row: pd.Series, freq: str) -> str:
    d: pd.Timestamp = row["date"]
    if freq == "anual":
        return str(d.year)
    label = row.get("period_label") if hasattr(row, "get") else None
    if isinstance(label, str) and label:
        return label
    return d.strftime("%Y-%m")


def _signal(ind, change: float | None, last: float) -> str:
    if change is None or ind.higher_is_worse is None:
        return "sem_comparacao"
    if abs(change) < 0.05 or (last and abs(change / last) < 0.01):
        return "estavel"
    worse = (change > 0) == ind.higher_is_worse
    return "piora" if worse else "melhora"


def _snapshot(ctx: DataContext, key: str) -> dict[str, Any]:
    ind = CATALOG[key]
    df = ctx.series.get(key)
    if df is None or df.empty:
        return {"indicador": key, "erro": "sem dados disponíveis"}
    df = df.dropna(subset=["value"]).sort_values("date")
    last = df.iloc[-1]
    out: dict[str, Any] = {
        "indicador": key, "nome": ind.label, "unidade": ind.unit, "frequencia": ind.freq, "fonte": ind.source,
        "qualidade_dos_dados": ctx.quality.get(key, "oficial"),
        "ultimo": {"periodo": _when(last, ind.freq), "valor": _num(last["value"])},
        "serie_desde": _when(df.iloc[0], ind.freq), "n_observacoes": int(len(df)),
    }
    if len(df) > ind.yoy_periods:
        prev = df.iloc[-1 - ind.yoy_periods]
        change = float(last["value"] - prev["value"])
        out["ha_12_meses"] = {"periodo": _when(prev, ind.freq), "valor": _num(prev["value"]),
                              "variacao_absoluta": _num(change),
                              "variacao_percentual": _num(change / prev["value"] * 100, 1) if prev["value"] else None}
        out["leitura_12m"] = _signal(ind, change, float(last["value"]))
        out["observacao_variacao"] = ("variacao_absoluta está na mesma unidade do indicador (p.p. quando a unidade é %)"
                                      if "%" in ind.unit else "variacao_absoluta na unidade do indicador")
    lo, hi = df.loc[df["value"].idxmin()], df.loc[df["value"].idxmax()]
    out["minimo_da_serie"] = {"periodo": _when(lo, ind.freq), "valor": _num(lo["value"])}
    out["maximo_da_serie"] = {"periodo": _when(hi, ind.freq), "valor": _num(hi["value"])}
    out["ultimas_6"] = [{"periodo": _when(r, ind.freq), "valor": _num(r["value"])} for _, r in df.tail(6).iterrows()]
    return out


def _parse_bound(text: str | None, end: bool) -> pd.Timestamp | None:
    if not text:
        return None
    t = text.strip()
    try:
        if len(t) == 4:
            return pd.Timestamp(f"{t}-12-31" if end else f"{t}-01-01")
        return pd.Timestamp(f"{t}-28") if end and len(t) == 7 else pd.Timestamp(t if len(t) > 7 else f"{t}-01")
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Ferramentas
# --------------------------------------------------------------------------- #
def list_indicators(ctx: DataContext, **_: Any) -> dict[str, Any]:
    return {
        "areas": AREAS,
        "indicadores": [{"indicador": k, "nome": v.label, "unidade": v.unit, "area": v.area, "frequencia": v.freq,
                         "tem_dados": ctx.has(k)} for k, v in CATALOG.items()],
        "metricas_por_uf": list(UF_METRICS),
    }


def get_overview(ctx: DataContext, area: str = "tudo", **_: Any) -> dict[str, Any]:
    keys = [k for k, v in CATALOG.items() if area in ("tudo", v.area)]
    snaps = []
    for k in keys:
        s = _snapshot(ctx, k)
        if "erro" in s:
            snaps.append(s)
            continue
        prev = s.get("ha_12_meses") or {}
        snaps.append({"indicador": k, "nome": s["nome"], "unidade": s["unidade"], "periodo": s["ultimo"]["periodo"],
                      "valor": s["ultimo"]["valor"], "valor_ha_12_meses": prev.get("valor"),
                      "variacao_12m": prev.get("variacao_absoluta"), "leitura_12m": s.get("leitura_12m"),
                      "qualidade_dos_dados": s["qualidade_dos_dados"]})
    out: dict[str, Any] = {"area": area, "indicadores": snaps, "manchetes_dos_graficos": ctx.chart_headlines(area)}
    if area in ("tudo", "trabalho"):
        out["status_recessao_tecnica"] = ctx.recession_status()
    return out


def get_indicator_snapshot(ctx: DataContext, indicator: str, **_: Any) -> dict[str, Any]:
    return _snapshot(ctx, indicator)


def get_time_series(ctx: DataContext, indicator: str, start: str | None = None, end: str | None = None,
                    aggregate: str | None = None, **_: Any) -> dict[str, Any]:
    ind = CATALOG[indicator]
    df = ctx.series.get(indicator)
    if df is None or df.empty:
        return {"indicador": indicator, "erro": "sem dados disponíveis"}
    df = df.dropna(subset=["value"]).sort_values("date")
    lo, hi = _parse_bound(start, False), _parse_bound(end, True)
    if lo is not None:
        df = df[df["date"] >= lo]
    if hi is not None:
        df = df[df["date"] <= hi]
    note = None
    if (aggregate == "annual_last" or len(df) > MAX_POINTS) and ind.freq != "anual":
        df = df.groupby(df["date"].dt.year, as_index=False).last()
        note = "agregado: último valor de cada ano" + (" (automático: série longa demais)" if aggregate != "annual_last" else "")
    if len(df) > MAX_POINTS:
        df = df.tail(MAX_POINTS)
        note = (note or "") + f"; mostrando só os últimos {MAX_POINTS} pontos"
    return {"indicador": indicator, "nome": ind.label, "unidade": ind.unit, "frequencia": ind.freq, "nota": note,
            "qualidade_dos_dados": ctx.quality.get(indicator, "oficial"),
            "pontos": [{"periodo": _when(r, ind.freq), "valor": _num(r["value"])} for _, r in df.iterrows()]}


def get_security_by_uf(ctx: DataContext, metric: str = "mvi_taxa", uf: str | None = None, year: int | None = None,
                       **_: Any) -> dict[str, Any]:
    sec = ctx.sec
    if sec.empty:
        return {"erro": "dados de segurança indisponíveis"}
    ufs = sorted(sec["uf"].unique())
    base = sec[sec["indicador"] == metric]
    if uf:
        uf = uf.upper()
        if uf not in ufs or uf == "BR":
            return {"erro": f"UF inválida. Use uma de: {', '.join(u for u in ufs if u != 'BR')}"}
        s = base[base["uf"] == uf].sort_values("ano")
        nat = base[base["uf"] == "BR"].set_index("ano")["valor"]
        return {"metrica": metric, "uf": uf, "unidade": CATALOG[metric].unit if metric in CATALOG else "",
                "serie": [{"ano": int(r.ano), "valor": _num(r.valor), "brasil": _num(nat.get(r.ano))} for r in s.itertuples()]}
    years = sorted(base[base["uf"] != "BR"]["ano"].unique())  # só anos que têm estados
    y = int(year) if year in years else int(years[-1]) if years else None
    snap = base[(base["ano"] == y) & (base["uf"] != "BR")].sort_values("valor", ascending=False)
    nat = base[(base["ano"] == y) & (base["uf"] == "BR")]["valor"]
    return {"metrica": metric, "ano": y, "brasil": _num(nat.iloc[0]) if len(nat) else None,
            "unidade": CATALOG[metric].unit if metric in CATALOG else "",
            "ranking_decrescente": [{"uf": r.uf, "valor": _num(r.valor)} for r in snap.itertuples()]}


def explain_topic(ctx: DataContext, topic: str, **_: Any) -> dict[str, Any]:
    if topic == "metodologia":
        return {"metodologia": METHODOLOGY}
    if topic == "qualidade_dos_dados":
        return {"qualidade": QUALITY_NOTE, "por_indicador": ctx.quality, "atualizacao_das_fontes": ctx.freshness}
    ind = CATALOG[topic]
    ex = EXPLAINERS.get(_EXPLAINER_OF_KEY.get(topic, ""))
    out: dict[str, Any] = {"indicador": topic, "nome": ind.label, "unidade": ind.unit, "descricao": ind.description,
                           "fonte": ind.source}
    if ex:
        out.update({"o_que_e": ex.what, "por_que_importa": ex.why, "fonte_auditavel": ex.source_label})
    return out


# --------------------------------------------------------------------------- #
# Esquemas (strict) e despachante
# --------------------------------------------------------------------------- #
_KEYS = list(CATALOG)


def _fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "name": name, "description": desc, "strict": True,
            "parameters": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}


_NULLABLE_STR = {"type": ["string", "null"]}
TOOL_SCHEMAS: list[dict[str, Any]] = [
    _fn("list_indicators", "Lista todos os indicadores disponíveis no painel, suas unidades e áreas.", {}, []),
    _fn("get_overview", "Visão geral de uma área (fiscal, trabalho, seguranca, educacao) ou de tudo: valores atuais, "
        "variação em 12 meses e as manchetes dos gráficos. Use para perguntas amplas como 'como está a segurança pública?'.",
        {"area": {"type": "string", "enum": ["tudo", *AREAS]}}, ["area"]),
    _fn("get_indicator_snapshot", "Valor mais recente de UM indicador, variação em 12 meses, mínimo e máximo da série e "
        "últimas 6 observações. Use para perguntas pontuais ('qual a dívida bruta hoje?').",
        {"indicator": {"type": "string", "enum": _KEYS}}, ["indicator"]),
    _fn("get_time_series", "Série histórica de um indicador, opcionalmente recortada (start/end como 'AAAA' ou 'AAAA-MM') "
        "e agregada por ano. Máximo de 60 pontos.",
        {"indicator": {"type": "string", "enum": _KEYS}, "start": _NULLABLE_STR, "end": _NULLABLE_STR,
         "aggregate": {"type": ["string", "null"], "enum": ["annual_last", None]}}, ["indicator", "start", "end", "aggregate"]),
    _fn("get_security_by_uf", "Segurança pública por estado (UF): ranking de um ano ou série de uma UF contra o Brasil.",
        {"metric": {"type": "string", "enum": list(UF_METRICS)}, "uf": {"type": ["string", "null"]},
         "year": {"type": ["integer", "null"]}}, ["metric", "uf", "year"]),
    _fn("explain_topic", "Explicação em linguagem simples de um indicador, da metodologia ou da qualidade dos dados.",
        {"topic": {"type": "string", "enum": [*_KEYS, "metodologia", "qualidade_dos_dados"]}}, ["topic"]),
]
TOOLS = {f.__name__: f for f in (list_indicators, get_overview, get_indicator_snapshot, get_time_series,
                                 get_security_by_uf, explain_topic)}
_ENUMS: dict[str, dict[str, set]] = {
    "get_overview": {"area": {"tudo", *AREAS}}, "get_indicator_snapshot": {"indicator": set(_KEYS)},
    "get_time_series": {"indicator": set(_KEYS), "aggregate": {"annual_last", None}},
    "get_security_by_uf": {"metric": set(UF_METRICS)}, "explain_topic": {"topic": {*_KEYS, "metodologia", "qualidade_dos_dados"}},
}


def run_tool(ctx: DataContext, name: str, raw_args: str | dict | None) -> str:
    """Executa uma ferramenta com validação estrita; sempre devolve JSON (erros viram ``{"erro": ...}``)."""
    fn = TOOLS.get(name)
    if fn is None:
        return json.dumps({"erro": "ferramenta desconhecida"})
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
        if not isinstance(args, dict):
            raise ValueError("argumentos inválidos")
        for field, allowed in _ENUMS.get(name, {}).items():
            if field in args and args[field] not in allowed:
                return json.dumps({"erro": f"valor inválido para '{field}'", "valores_validos": sorted(x for x in allowed if x)})
        if "year" in args and args["year"] is not None:
            args["year"] = int(args["year"])
        for f in ("start", "end", "uf"):
            if args.get(f) is not None:
                args[f] = str(args[f])[:10]
        result = fn(ctx, **args)
    except Exception as exc:  # nunca vaza stack/detalhes para o modelo
        return json.dumps({"erro": f"falha ao consultar os dados ({type(exc).__name__})"})
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:  # JSON sempre válido: peça um recorte menor em vez de truncar no meio
        return json.dumps({"erro": "resultado grande demais; consulte um indicador ou período específico"})
    return text
