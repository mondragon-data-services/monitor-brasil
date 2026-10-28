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
from src.transformation.metrics import value_days_ago

MAX_POINTS = 60
MAX_TOOL_OUTPUT_CHARS = 16000
UF_METRICS = ("mvi_taxa", "mvi_total", "feminicidio_total", "estupro_vulneravel_total")

_EXPLAINER_OF_AREA = {"fiscal": ("inflacao", "divida_juros"), "trabalho": ("desemprego", "renda_endividamento", "recessao"),
                      "seguranca": ("mvi", "vulneraveis"), "educacao": ("ideb", "evasao")}
_EXPLAINER_OF_KEY = {
    "cambio_dolar": "cambio", "focus_ipca_ano_corrente": "cambio", "focus_ipca_proximo_ano": "cambio",
    "resultado_primario": "fiscal_resultado", "resultado_nominal": "fiscal_resultado", "juros_da_divida": "fiscal_resultado",
    "inadimplencia_pf": "credito", "juros_cartao_rotativo": "credito", "juros_cheque_especial": "credito",
    "salario_minimo": "salario_minimo", "salario_minimo_real": "salario_minimo",
    "pobreza": "pobreza", "pobreza_extrema": "pobreza", "gini": "pobreza", "inseguranca_alimentar": "fome", "fome_grave": "fome",
    "informalidade": "informalidade", "carteira_privado": "informalidade", "sem_carteira": "informalidade",
    "conta_propria": "informalidade", "caged_saldo_mensal": "informalidade", "caged_saldo_12m": "informalidade",
    "massa_rendimento": "informalidade", "desalento": "informalidade", "bolsa_familia_valor": "bolsa_familia",
    "dengue_casos_semana": "dengue", "vacina_sarampo": "vacinacao", "vacina_dpt": "vacinacao",
    "mortalidade_infancia": "mortalidade_infancia", "expectativa_vida": "saude_vida", "gasto_saude_pib": "saude_vida",
    "gasto_publico_saude_pib": "saude_vida", "leitos_sus_por_mil": "leitos", "leitos_existentes_por_mil": "leitos",
    "bolsa_familia_familias": "bolsa_familia", "bolsa_familia_beneficio_medio": "bolsa_familia",
    "bolsa_familia_familias_mensal": "bolsa_familia", "bolsa_familia_valor_medio_familia": "bolsa_familia", "leitos_sus_por_mil": "leitos", "leitos_existentes_por_mil": "leitos",
    "bolsa_familia_familias": "bolsa_familia", "bolsa_familia_beneficio_medio": "bolsa_familia",
    "ipca_12m": "inflacao", "ipca_alimentacao_12m": "inflacao", "selic_meta": "divida_juros", "juro_real": "divida_juros",
    "divida_bruta": "divida_juros", "divida_liquida": "divida_juros", "desocupacao": "desemprego",
    "subutilizacao": "desemprego", "rendimento_real": "renda_endividamento", "endividamento_familias": "renda_endividamento",
    "pib_4tri": "recessao", "pib_trimestral": "recessao", "ibcbr_12m": "recessao", "mvi_taxa": "mvi", "gasolina_brasil": "gasolina",
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
NOT_AVAILABLE = (
    "O painel NÃO tem: preço em reais dos alimentos (só variação do IPCA), carne moída (usa acém/patinho), filas de espera do SUS "
    "(cirurgias, consultas, exames — não há base nacional aberta), cobertura vacinal por estado depois de 2022 (o Ministério só "
    "publica num painel sem acesso automático), Bolsa Família mensal anterior aos últimos 24 meses processados, previsões próprias."
)
QUALITY_NOTE = (
    "Segurança pública: dados oficiais do Sinesp VDE (Ministério da Justiça) quando baixados; em 2015–2017 a cobertura dos "
    "estados pode ser incompleta (afeta sobretudo estupro). Ideb, abandono escolar e 'nem-nem' vêm de um consolidado de "
    "referência APROXIMADO (INEP/IBGE) — cite sempre essa ressalva ao usá-los. Cobertura vacinal por estado e leitos vêm do TabNet/DATASUS (Ministério "
    "da Saúde; vacinas só até 2022). Vacinação nacional, expectativa de vida e gasto em saúde "
    "vêm do Banco Mundial (OMS/UNICEF/ONU): FONTE SECUNDÁRIA, só nacional. Dengue (SINAN): as últimas 3 semanas ficam de fora por "
    "atraso de registro. " + NOT_AVAILABLE
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
    if freq == "semanal":
        return d.strftime("%Y-%m-%d")
    label = row.get("period_label") if hasattr(row, "get") else None
    if isinstance(label, str) and label:
        return label
    return d.strftime("%Y-%m")


def _signal(ind, change: float | None, last: float) -> str:
    if change is None or ind.higher_is_worse is None:
        return "sem_comparacao"
    # estável = variação desprezível: <1% do valor, ou <0,05 p.p. quando a unidade é percentual (o limite absoluto não vale para
    # índices de escala pequena, como o Gini 0–1)
    if (last and abs(change / last) < 0.01) or ("%" in ind.unit and abs(change) < 0.05):
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
    keys = [k for k, v in CATALOG.items() if (area == "tudo" and v.core) or area == v.area]
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
    headlines = ctx.chart_headlines(area)
    if area == "tudo":  # visão geral enxuta: manchetes curtas e no máximo 2 por área
        per_area: dict[str, int] = {}
        kept = []
        for h in headlines:
            if per_area.get(h["area"], 0) < 2:
                per_area[h["area"]] = per_area.get(h["area"], 0) + 1
                kept.append({**h, "titulo_e_subtitulo": h["titulo_e_subtitulo"].split(" — ")[0][:230]})
        headlines = kept
    out: dict[str, Any] = {"area": area, "indicadores": snaps, "manchetes_dos_graficos": headlines}
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


def get_fuel_by_uf(ctx: DataContext, uf: str | None = None, **_: Any) -> dict[str, Any]:
    """Gasolina comum (ANP): ranking dos estados na última semana, ou a série recente de uma UF contra o Brasil."""
    anp = ctx.anp
    if anp.empty:
        return {"erro": "preços de gasolina indisponíveis"}
    week = anp["date"].max()
    ufs = sorted(u for u in anp["uf"].unique() if u != "BR")
    br = anp[anp["uf"] == "BR"].sort_values("date")
    br_now = br[br["date"] == week]["value"]
    if uf:
        uf = uf.upper()
        if uf not in ufs:
            return {"erro": f"UF inválida. Use uma de: {', '.join(ufs)}"}
        own = anp[anp["uf"] == uf].sort_values("date")
        last_52 = own.tail(53)
        return {"unidade": "R$/litro", "produto": "gasolina comum", "uf": uf, "semana_encerrada_em": str(week.date()),
                "preco_atual": _num(own.iloc[-1]["value"]), "preco_brasil": _num(br_now.iloc[0]) if len(br_now) else None,
                "preco_ha_12_meses": _num(value_days_ago(own, 365)), "postos_pesquisados": _num(own.iloc[-1].get("postos"), 0),
                "ultimas_semanas": [{"semana": str(r.date.date()), "valor": _num(r.value)} for r in own.tail(8).itertuples()],
                "minimo_52_semanas": _num(last_52["value"].min()), "maximo_52_semanas": _num(last_52["value"].max())}
    snap = anp[(anp["date"] == week) & (anp["uf"] != "BR")].sort_values("value", ascending=False)
    return {"unidade": "R$/litro", "produto": "gasolina comum", "semana_encerrada_em": str(week.date()),
            "brasil": _num(br_now.iloc[0]) if len(br_now) else None, "brasil_ha_12_meses": _num(value_days_ago(br, 365)),
            "ranking_decrescente": [{"uf": r.uf, "preco": _num(r.value)} for r in snap.itertuples()]}


HEALTH_METRICS = ("dengue_incidencia", "dengue_casos", "mortalidade_infancia", "leitos_sus_por_mil", "leitos_existentes_por_mil",
                  "cobertura_triplice_viral", "cobertura_polio", "cobertura_penta")


def get_health_by_uf(ctx: DataContext, metric: str = "dengue_incidencia", uf: str | None = None, **_: Any) -> dict[str, Any]:
    """Saúde por estado: dengue (incidência ou casos no ano, até a última semana completa) ou mortalidade de menores de 5 anos."""
    from src.config import DENGUE_INCOMPLETE_WEEKS, UF_BY_IBGE_CODE
    from src.ingestion.sinesp_loader import UF_POPULATION
    from src.transformation.metrics import dengue_cumulative, dengue_incidence_by_uf, dengue_last_complete_week
    uf = uf.upper() if uf else None
    if metric in ("leitos_sus_por_mil", "leitos_existentes_por_mil"):
        from src.visualization.charts import beds_per_thousand
        if ctx.beds.empty:
            return {"erro": "dados de leitos indisponíveis"}
        col = "sus_mil" if metric == "leitos_sus_por_mil" else "existentes_mil"
        b = beds_per_thousand(ctx.beds)
        latest = b["date"].max()
        nat = b[(b["uf"] == "BR") & (b["date"] == latest)][col]
        base = {"metrica": metric, "referencia": f"{latest:%m/%Y}", "unidade": "leitos por mil habitantes (população do Censo 2022)",
                "brasil": _num(nat.iloc[0], 2) if len(nat) else None, "fonte": "CNES/Ministério da Saúde via TabNet"}
        if uf:
            own = b[b["uf"] == uf].sort_values("date")
            if own.empty or uf == "BR":
                return {"erro": f"UF inválida. Use uma de: {', '.join(sorted(u for u in b['uf'].unique() if u != 'BR'))}"}
            return {**base, "uf": uf, "valor": _num(own.iloc[-1][col], 2),
                    "serie": [{"periodo": f"{r.date:%Y-%m}", "valor": _num(getattr(r, col), 2)} for r in own.tail(8).itertuples()]}
        snap = b[(b["date"] == latest) & (b["uf"] != "BR")].sort_values(col, ascending=False)
        return {**base, "ranking_decrescente": [{"uf": r.uf, "valor": _num(getattr(r, col), 2)} for r in snap.itertuples()]}
    if metric.startswith("cobertura_"):
        vac = {"cobertura_triplice_viral": "triplice_viral", "cobertura_polio": "polio", "cobertura_penta": "penta"}[metric]
        if ctx.pni.empty:
            return {"erro": "cobertura vacinal por estado indisponível"}
        v = ctx.pni[ctx.pni["vacina"] == vac]
        year = v["date"].max()
        snap = v[v["date"] == year].sort_values("value")
        base = {"metrica": metric, "ano_do_dado": int(year.year), "unidade": "% de cobertura", "meta_pni": 95,
                "estados_que_atingiram_a_meta": int((snap["value"] >= 95).sum()), "total_de_estados": int(len(snap)),
                "observacao": "último ano publicado no TabNet/SI-PNI é 2022; valores acima de 100% refletem estimativas de população",
                "fonte": "SI-PNI/Ministério da Saúde via TabNet"}
        if uf:
            own = v[v["uf"] == uf].sort_values("date")
            if own.empty:
                return {"erro": f"UF inválida. Use uma de: {', '.join(sorted(v['uf'].unique()))}"}
            return {**base, "uf": uf, "valor": _num(own.iloc[-1]["value"], 0),
                    "serie": [{"ano": int(r.date.year), "valor": _num(r.value, 0)} for r in own.tail(8).itertuples()]}
        return {**base, "ranking_crescente": [{"uf": r.uf, "valor": _num(r.value, 0)} for r in snap.itertuples()]}
    if metric == "mortalidade_infancia":
        m = ctx.mortality
        if m.empty:
            return {"erro": "mortalidade na infância indisponível"}
        year = m["date"].max()
        snap = m[(m["date"] == year) & (m["geo_code"] != "1")].copy()
        snap["uf"] = snap["geo_code"].map(UF_BY_IBGE_CODE)
        nat = m[(m["geo_code"] == "1") & (m["date"] == year)]["value"]
        base = {"metrica": metric, "ano": int(year.year), "unidade": "óbitos de menores de 5 anos por mil nascidos vivos",
                "brasil": _num(nat.iloc[0], 1) if len(nat) else None, "fonte": "IBGE (SIDRA 6695)"}
        if uf:
            row = snap[snap["uf"] == uf]
            if row.empty:
                return {"erro": f"UF inválida. Use uma de: {', '.join(sorted(UF_BY_IBGE_CODE.values()))}"}
            hist = m[m["geo_code"] == row.iloc[0]["geo_code"]].sort_values("date").tail(8)
            return {**base, "uf": uf, "valor": _num(row.iloc[0]["value"], 1),
                    "serie": [{"ano": int(r.date.year), "valor": _num(r.value, 1)} for r in hist.itertuples()]}
        ranking = snap.sort_values("value", ascending=False)
        return {**base, "ranking_decrescente": [{"uf": r.uf, "valor": _num(r.value, 1)} for r in ranking.itertuples()]}
    if metric not in ("dengue_incidencia", "dengue_casos"):
        return {"erro": f"metrica inválida. Use uma de: {', '.join(HEALTH_METRICS)}"}
    d = ctx.dengue
    done = dengue_last_complete_week(d, DENGUE_INCOMPLETE_WEEKS) if not d.empty else None
    if done is None:
        return {"erro": "dados de dengue indisponíveis"}
    year, week = done
    inc = dengue_incidence_by_uf(d, year, week, UF_POPULATION)
    base = {"metrica": metric, "ano": year, "ate_a_semana_epidemiologica": week,
            "observacao": "casos prováveis do SINAN; as últimas semanas de registros são ignoradas por atraso de digitação"}
    if uf:
        if uf not in set(inc["uf"]):
            return {"erro": f"UF inválida. Use uma de: {', '.join(sorted(inc['uf']))}"}
        cur, prev = dengue_cumulative(d, year, week, uf), dengue_cumulative(d, year - 1, week, uf)
        row = inc[inc["uf"] == uf].iloc[0]
        return {**base, "uf": uf, "casos_no_ano": cur["casos"], "casos_mesmo_periodo_ano_anterior": prev["casos"],
                "obitos_confirmados_no_ano": cur["obitos"], "casos_graves_no_ano": cur["graves"],
                "incidencia_por_100_mil": _num(row["incidencia"], 0),
                "posicao_no_ranking_de_incidencia": int(list(inc["uf"]).index(uf)) + 1}
    br_cur, br_prev = dengue_cumulative(d, year, week), dengue_cumulative(d, year - 1, week)
    key = "incidencia_por_100_mil" if metric == "dengue_incidencia" else "casos"
    col = "incidencia" if metric == "dengue_incidencia" else "casos"
    ranking = inc.sort_values(col, ascending=False)
    return {**base, "brasil": {"casos_no_ano": br_cur["casos"], "casos_mesmo_periodo_ano_anterior": br_prev["casos"],
                               "obitos_confirmados_no_ano": br_cur["obitos"]},
            "ranking_decrescente": [{"uf": r.uf, key: _num(getattr(r, col), 0)} for r in ranking.itertuples()]}


def get_bolsa_familia_by_uf(ctx: DataContext, uf: str | None = None, **_: Any) -> dict[str, Any]:
    """Bolsa Família em dezembro: famílias (benefícios) por UF e por 100 habitantes; benefício médio nacional."""
    from src.ingestion.sinesp_loader import BRAZIL_POPULATION, UF_POPULATION
    fam = ctx.pbf_fam
    if fam.empty:
        return {"erro": "número de famílias do Bolsa Família indisponível"}
    year = fam["date"].max()
    snap = fam[(fam["date"] == year) & (fam["uf"] != "BR")].copy()
    snap["por_100"] = [v / UF_POPULATION[u] * 100 if u in UF_POPULATION else float("nan") for u, v in zip(snap["uf"], snap["value"], strict=True)]
    nat = fam[(fam["uf"] == "BR") & (fam["date"] == year)]["value"]
    prev = fam[(fam["uf"] == "BR") & (fam["date"] == fam[fam["uf"] == "BR"]["date"].sort_values().iloc[-2])]["value"] if len(fam[fam["uf"] == "BR"]) > 1 else []
    out: dict[str, Any] = {"referencia": f"dezembro de {year.year}", "unidade": "benefícios (famílias) pagos em dezembro",
                           "brasil": _num(nat.iloc[0], 0) if len(nat) else None, "brasil_ano_anterior": _num(prev.iloc[0], 0) if len(prev) else None,
                           "brasil_por_100_habitantes": _num(nat.iloc[0] / BRAZIL_POPULATION * 100, 1) if len(nat) else None,
                           "fonte": "Ministério do Desenvolvimento Social via Ipeadata (FAM_PBF/VAL_PBF)"}
    avg = ctx.series.get("bolsa_familia_beneficio_medio")
    if avg is not None and not avg.empty:
        out["beneficio_medio_nacional_reais"] = {"ano": int(avg.iloc[-1]["date"].year), "valor": _num(avg.iloc[-1]["value"], 0)}
        out["observacao"] = "o valor médio só existe até o último ano em que o Ipeadata publicou o valor total de dezembro"
    m = ctx.pbf_m
    monthly: dict[str, Any] | None = None
    if not m.empty:
        mm = m.assign(medio=m["valor"] / m["familias"])
        last_m = mm["date"].max()
        monthly = {"mes_mais_recente": f"{last_m:%Y-%m}", "unidade": "famílias com pagamento no mês e R$ por família",
                   "fonte": "Portal da Transparência/CGU (ETL do painel; famílias = favorecidos distintos)"}
        out["mensal"] = monthly
    if uf:
        uf = uf.upper()
        row = snap[snap["uf"] == uf]
        if row.empty:
            return {"erro": f"UF inválida. Use uma de: {', '.join(sorted(snap['uf']))}"}
        if monthly is not None:
            mine = mm[mm["uf"] == uf].sort_values("date")
            if not mine.empty:
                monthly.update({"familias": int(mine.iloc[-1]["familias"]), "valor_medio_por_familia_reais": _num(mine.iloc[-1]["medio"], 0),
                                "serie_mensal": [{"mes": f"{r.date:%Y-%m}", "familias": int(r.familias), "valor_medio": _num(r.medio, 0)} for r in mine.tail(12).itertuples()]})
        hist = fam[fam["uf"] == uf].sort_values("date").tail(6)
        return {**out, "uf": uf, "familias": _num(row.iloc[0]["value"], 0), "por_100_habitantes": _num(row.iloc[0]["por_100"], 1),
                "serie": [{"ano": int(r.date.year), "familias": _num(r.value, 0)} for r in hist.itertuples()]}
    if monthly is not None:
        latest = mm[(mm["date"] == last_m) & (mm["uf"] != "BR")].sort_values("medio", ascending=False)
        br_row = mm[(mm["date"] == last_m) & (mm["uf"] == "BR")]
        if len(br_row):
            monthly.update({"brasil_familias": int(br_row.iloc[0]["familias"]), "brasil_valor_medio_por_familia_reais": _num(br_row.iloc[0]["medio"], 0)})
        monthly["ranking_valor_medio_por_familia"] = [{"uf": r.uf, "valor_medio": _num(r.medio, 0)} for r in latest.itertuples()]
    ranking = snap.sort_values("por_100", ascending=False)
    out["ranking_por_100_habitantes"] = [{"uf": r.uf, "familias": _num(r.value, 0), "por_100_habitantes": _num(r.por_100, 1)} for r in ranking.itertuples()]
    return out


def get_market_expectations(ctx: DataContext, **_: Any) -> dict[str, Any]:
    """Boletim Focus: o que o mercado espera para IPCA, Selic, PIB e dólar (ano corrente e seguintes)."""
    f = ctx.focus
    if f.empty:
        return {"erro": "expectativas do Focus indisponíveis"}
    last = f["date"].max()
    snap = f[f["date"] == last]
    out: dict[str, Any] = {"boletim_de": str(last.date()), "unidades": {"IPCA": "%", "Selic": "% a.a. no fim do ano",
                                                                          "PIB Total": "% de crescimento", "Câmbio": "R$ por dólar no fim do ano"},
                           "meta_de_inflacao": "3,0% com tolerância de ±1,5 p.p. (teto 4,5%)", "expectativas": []}
    for ind in ("IPCA", "Selic", "PIB Total", "Câmbio"):
        for r in snap[snap["indicador"] == ind].sort_values("ano").itertuples():
            hist = f[(f["indicador"] == ind) & (f["ano"] == r.ano)][["date", "mediana"]].rename(columns={"mediana": "value"})
            out["expectativas"].append({"indicador": ind, "ano": int(r.ano), "mediana": _num(r.mediana),
                                        "minimo": _num(r.minimo), "maximo": _num(r.maximo), "respondentes": _num(r.n, 0),
                                        "mediana_ha_12_meses": _num(value_days_ago(hist, 365))})
    return out


def get_price_ranking(ctx: DataContext, **_: Any) -> dict[str, Any]:
    """Inflação do dia a dia: itens do IPCA ordenados pela alta em 12 meses + o que R$ 100 de dez/2019 viraram."""
    from src.config import IPCA_ITEMS
    from src.transformation.metrics import accumulated_12m, cumulative_index, split_items
    if ctx.items.empty:
        return {"erro": "inflação por item indisponível"}
    per_item = split_items(ctx.items)
    rows = []
    for key, d in per_item.items():
        acc = accumulated_12m(d).dropna()
        if acc.empty:
            continue
        rows.append({"item": IPCA_ITEMS[key].label, "chave": key, "acumulado_12m_pct": _num(acc.iloc[-1]["value"], 1),
                     "mes_mais_recente": acc.iloc[-1]["date"].strftime("%Y-%m"),
                     "variacao_no_mes_pct": _num(d.iloc[-1]["value"], 2),
                     "r100_de_dez2019_viraram": _num(cumulative_index(d)["value"].iloc[-1], 0)})
    geral = next((r for r in rows if r["chave"] == "geral"), None)
    items = sorted((r for r in rows if r["chave"] != "geral"), key=lambda r: -(r["acumulado_12m_pct"] or 0))
    for r in items:
        r.pop("chave")
    out: dict[str, Any] = {"ipca_geral_12m_pct": geral["acumulado_12m_pct"] if geral else None,
                           "ipca_geral_r100_viraram": geral["r100_de_dez2019_viraram"] if geral else None,
                           "itens_do_maior_para_o_menor_aumento": items,
                           "observacao": "O IBGE publica variação de preços, não o preço em reais dos alimentos. "
                                         "Carne moída não existe no IPCA: use acém/patinho como referência."}
    if "gasolina_brasil" in ctx.series and not ctx.series["gasolina_brasil"].empty:
        g = ctx.series["gasolina_brasil"]
        out["gasolina_preco_litro_brasil"] = {"semana": str(g.iloc[-1]["date"].date()), "valor": _num(g.iloc[-1]["value"]),
                                              "ha_12_meses": _num(value_days_ago(g, 365))}
    return out


def explain_topic(ctx: DataContext, topic: str, **_: Any) -> dict[str, Any]:
    if topic == "metodologia":
        return {"metodologia": METHODOLOGY}
    if topic == "qualidade_dos_dados":
        return {"qualidade": QUALITY_NOTE, "por_indicador": ctx.quality, "atualizacao_das_fontes": ctx.freshness}
    ind = CATALOG[topic]
    ex = EXPLAINERS.get(_EXPLAINER_OF_KEY.get(topic) or ("precos_itens" if topic.startswith("ipca_item_") else ""))
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
    _fn("get_fuel_by_uf", "Preço da gasolina comum por litro (ANP): ranking de todos os estados na última semana, ou a série "
        "recente de UMA UF (sigla de 2 letras) contra a média do Brasil. Use para 'preço da gasolina em SP/por estado'.",
        {"uf": {"type": ["string", "null"]}}, ["uf"]),
    _fn("get_health_by_uf", "Saúde por estado: dengue (incidência por 100 mil ou casos no ano, comparado ao mesmo período do ano "
        "anterior) ou mortalidade de menores de 5 anos. Informe a UF (sigla) para um estado ou null para o ranking.",
        {"metric": {"type": "string", "enum": list(HEALTH_METRICS)}, "uf": {"type": ["string", "null"]}}, ["metric", "uf"]),
    _fn("get_bolsa_familia_by_uf", "Bolsa Família em dezembro: número de famílias (benefícios) por estado e por 100 habitantes, evolução "
        "de um estado e benefício médio nacional. Use null em uf para o ranking.", {"uf": {"type": ["string", "null"]}}, ["uf"]),
    _fn("get_market_expectations", "O que o mercado espera (Boletim Focus) para IPCA, Selic, PIB e dólar no ano corrente e nos "
        "seguintes, com mínimo, máximo e o valor de 12 meses atrás. Use para 'o que esperam da inflação/juros/dólar/PIB?'.", {}, []),
    _fn("get_price_ranking", "Inflação do dia a dia: todos os itens (arroz, feijão, tomate, frango, carne, leite, café, pão, "
        "gasolina...) ordenados pela alta em 12 meses, quanto R$ 100 de dez/2019 viraram, e o preço da gasolina. Use para "
        "'qual alimento subiu mais?', 'inflação do prato' ou 'como estão os preços?'.", {}, []),
    _fn("explain_topic", "Explicação em linguagem simples de um indicador, da metodologia ou da qualidade dos dados.",
        {"topic": {"type": "string", "enum": [*_KEYS, "metodologia", "qualidade_dos_dados"]}}, ["topic"]),
]
TOOLS = {f.__name__: f for f in (list_indicators, get_overview, get_indicator_snapshot, get_time_series,
                                 get_security_by_uf, get_fuel_by_uf, get_health_by_uf, get_bolsa_familia_by_uf, get_market_expectations, get_price_ranking,
                                 explain_topic)}
_ENUMS: dict[str, dict[str, set]] = {
    "get_overview": {"area": {"tudo", *AREAS}}, "get_indicator_snapshot": {"indicator": set(_KEYS)},
    "get_time_series": {"indicator": set(_KEYS), "aggregate": {"annual_last", None}},
    "get_security_by_uf": {"metric": set(UF_METRICS)}, "get_health_by_uf": {"metric": set(HEALTH_METRICS)}, "explain_topic": {"topic": {*_KEYS, "metodologia", "qualidade_dos_dados"}},
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
