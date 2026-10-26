"""Catálogo de indicadores e contexto de dados do Agente Monitora Brasil.

O agente NUNCA lê arquivos, rede ou texto livre: ele só enxerga os indicadores registrados aqui, já limpos e
calculados (acumulado 12m, juro real, taxas), exatamente como alimentam os gráficos do painel.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from src.ingestion.cache import FetchResult
from src.transformation.metrics import accumulated_12m, build_real_interest, detect_recession
from src.visualization import charts

AREAS: dict[str, str] = {
    "fiscal": "Inflação, juros e risco fiscal",
    "trabalho": "Emprego, renda e atividade econômica",
    "seguranca": "Segurança pública e vulneráveis",
    "educacao": "Educação",
}


@dataclass(frozen=True)
class Indicator:
    key: str
    label: str
    unit: str
    area: str
    freq: str            # mensal | trimestral | trimestre_movel | anual
    yoy_periods: int     # defasagem (em observações) que equivale a "12 meses atrás"
    higher_is_worse: bool | None
    source: str
    description: str


def _ind(*a) -> Indicator:
    return Indicator(*a)


CATALOG: dict[str, Indicator] = {
    i.key: i
    for i in (
        _ind("ipca_12m", "IPCA acumulado em 12 meses", "%", "fiscal", "mensal", 12, True,
             "Banco Central (SGS 433)", "Inflação oficial; acumulado por juros compostos. Meta: 3,0% ± 1,5 p.p."),
        _ind("ipca_alimentacao_12m", "IPCA Alimentação e bebidas acumulado em 12 meses", "%", "fiscal", "mensal", 12, True,
             "Banco Central (SGS 1635)", "Inflação do supermercado e da alimentação, acumulada em 12 meses."),
        _ind("selic_meta", "Meta da taxa Selic", "% a.a.", "fiscal", "mensal", 12, True,
             "Banco Central (SGS 432)", "Juro básico da economia (último valor de cada mês)."),
        _ind("juro_real", "Juro real ex-post (Selic vs IPCA 12m)", "% a.a.", "fiscal", "mensal", 12, True,
             "Cálculo do painel (Fisher) sobre BCB 432 e 433", "((1+Selic)/(1+IPCA12m) − 1). Mede o custo real do dinheiro."),
        _ind("divida_bruta", "Dívida Bruta do Governo Geral", "% do PIB", "fiscal", "mensal", 12, True,
             "Banco Central (SGS 13762)", "Tudo que o governo geral deve, em proporção do PIB."),
        _ind("divida_liquida", "Dívida Líquida do Setor Público", "% do PIB", "fiscal", "mensal", 12, True,
             "Banco Central (SGS 4513)", "Dívida do setor público descontados os ativos."),
        _ind("desocupacao", "Taxa de desocupação (desemprego)", "%", "trabalho", "trimestre_movel", 12, True,
             "IBGE PNAD Contínua (SIDRA 6381)", "Pessoas que procuram trabalho e não encontram."),
        _ind("subutilizacao", "Taxa composta de subutilização da força de trabalho", "%", "trabalho", "trimestre_movel", 12, True,
             "IBGE PNAD Contínua (SIDRA 6441)", "Desocupação + subocupação + desalento."),
        _ind("rendimento_real", "Rendimento médio real habitual (todos os trabalhos)", "R$", "trabalho", "trimestre_movel", 12, False,
             "IBGE PNAD Contínua (SIDRA 6390)", "Renda média do trabalhador corrigida pela inflação."),
        _ind("endividamento_familias", "Endividamento das famílias com o Sistema Financeiro Nacional", "% da renda", "trabalho",
             "mensal", 12, True, "Banco Central (SGS 29037)", "Parcela da renda das famílias comprometida com dívidas."),
        _ind("pib_4tri", "PIB acumulado em 4 trimestres", "%", "trabalho", "trimestral", 4, False,
             "IBGE Contas Nacionais (SIDRA 5932)", "Crescimento do PIB nos últimos 4 trimestres contra os 4 anteriores."),
        _ind("pib_trimestral", "PIB trimestre contra trimestre anterior (dessazonalizado)", "%", "trabalho", "trimestral", 4, False,
             "IBGE Contas Nacionais (SIDRA 5932)", "Variação do PIB em relação ao trimestre imediatamente anterior."),
        _ind("ibcbr_12m", "IBC-Br variação em 12 meses", "%", "trabalho", "mensal", 12, False,
             "Banco Central (SGS 24364), cálculo do painel", "Proxy mensal do PIB; variação do índice dessazonalizado em 12 meses."),
        _ind("mvi_taxa", "Mortes violentas intencionais por 100 mil habitantes", "por 100 mil hab.", "seguranca", "anual", 1, True,
             "Sinesp VDE / Ministério da Justiça", "Homicídio doloso + latrocínio + lesão corporal seguida de morte + "
             "morte por intervenção de agente do Estado."),
        _ind("feminicidio_total", "Feminicídios (vítimas)", "vítimas", "seguranca", "anual", 1, True,
             "Sinesp VDE / Ministério da Justiça", "Registros de feminicídio no ano."),
        _ind("estupro_vulneravel_total", "Estupro de vulnerável (vítimas)", "vítimas", "seguranca", "anual", 1, True,
             "Sinesp VDE / Ministério da Justiça", "Vítimas menores de 14 anos ou sem condição de consentir."),
        _ind("ideb_anos_iniciais", "Ideb — anos iniciais do fundamental", "nota 0–10", "educacao", "anual", 1, False,
             "INEP", "Nota nacional (aprendizado + aprovação)."),
        _ind("ideb_anos_finais", "Ideb — anos finais do fundamental", "nota 0–10", "educacao", "anual", 1, False,
             "INEP", "Nota nacional (aprendizado + aprovação)."),
        _ind("ideb_ensino_medio", "Ideb — ensino médio", "nota 0–10", "educacao", "anual", 1, False,
             "INEP", "Nota nacional (aprendizado + aprovação)."),
        _ind("abandono_ensino_medio", "Taxa de abandono no ensino médio", "%", "educacao", "anual", 1, True,
             "INEP Censo Escolar", "Alunos que deixam a escola durante o ano."),
        _ind("nem_nem", "Jovens de 15 a 29 anos que não estudam nem trabalham", "%", "educacao", "anual", 1, True,
             "IBGE PNAD Contínua Educação", "Proporção de jovens 'nem-nem'."),
    )
}

# Indicadores de segurança/educação que vêm do consolidado anual (coluna "indicador")
_ANNUAL_FROM_SEC = [k for k, v in CATALOG.items() if v.freq == "anual"]


@dataclass
class DataContext:
    """Séries prontas (colunas ``date``/``value``), o consolidado por UF e metadados de frescor."""

    series: dict[str, pd.DataFrame] = field(default_factory=dict)
    quality: dict[str, str] = field(default_factory=dict)       # indicador -> oficial | aproximado
    sec: pd.DataFrame = field(default_factory=pd.DataFrame)       # consolidado de segurança/educação (inclui UFs)
    freshness: dict[str, str] = field(default_factory=dict)       # fonte -> "ao vivo em ..." | "cache de ..."
    raw: dict[str, pd.DataFrame] = field(default_factory=dict)    # séries brutas usadas pelos gráficos

    def has(self, key: str) -> bool:
        return key in self.series and not self.series[key].empty

    # -- manchetes dos gráficos (mesmos títulos que o cidadão vê) ----------------------------------------------
    def chart_headlines(self, area: str | None = None) -> list[dict[str, str]]:
        r, sec = self.raw, self.sec
        out: list[tuple[str, str, object]] = []
        try:
            out.append(("fiscal", "Inflação (IPCA vs alimentos)", charts.inflation_chart(r["ipca_mensal"], r["ipca_alimentacao"])))
            out.append(("fiscal", "Dívida bruta, Selic e juro real", charts.debt_chart(r["divida_bruta"], r["real_rate"])))
            out.append(("trabalho", "Desemprego e subutilização", charts.labor_chart(r["desocupacao"], r["subutilizacao"])))
            out.append(("trabalho", "Renda e endividamento das famílias",
                        charts.income_debt_chart(r["rendimento_real"], r["endividamento_familias"])))
            rec_fig, status = charts.recession_chart(r["pib_4tri"], r["pib_trimestral"], r["ibcbr"])
            out.append(("trabalho", f"PIB e IBC-Br (status: {status})", rec_fig))
            if not sec.empty:
                out.append(("seguranca", "Mortes violentas (Brasil)", charts.mvi_chart(sec, "BR")))
                out.append(("seguranca", "Crimes contra vulneráveis", charts.vulnerable_chart(sec)))
                out.append(("seguranca", "Ranking de mortes violentas por UF", charts.uf_ranking_chart(sec, "mvi_taxa", None)))
                out.append(("educacao", "Ideb vs metas", charts.ideb_chart(sec)))
                out.append(("educacao", "Abandono escolar e nem-nem", charts.dropout_chart(sec)))
        except KeyError:
            pass
        rows = []
        for a, name, fig in out:
            if area not in (None, "tudo", a):
                continue
            title = _plain(getattr(fig.layout.title, "text", "") or "")
            if title and not title.startswith("Dados indisponíveis"):
                rows.append({"area": a, "grafico": name, "titulo_e_subtitulo": title})
        return rows

    def recession_status(self) -> str:
        pq = self.raw.get("pib_trimestral")
        return detect_recession(pq["value"]) if pq is not None and not pq.empty else "Sem dados"


def _plain(html: str) -> str:
    """HTML do título do Plotly -> texto. Quebras DENTRO do título viram espaço; título e subtítulo ficam separados por ' — '."""
    text = re.sub(r"</b>\s*<br\s*/?>", " — ", html)
    text = re.sub(r"<br\s*/?>", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def _annual_series(sec: pd.DataFrame, key: str, uf: str = "BR") -> pd.DataFrame:
    sub = sec[(sec["indicador"] == key) & (sec["uf"] == uf)].sort_values("ano")
    return pd.DataFrame({"date": pd.to_datetime(sub["ano"].astype(int).astype(str) + "-01-01"), "value": sub["valor"].astype(float)})


def build_context(results: dict[str, FetchResult]) -> DataContext:
    """Monta o contexto a partir dos resultados já carregados pelo app (sem novas chamadas de rede)."""

    def frame(key: str) -> pd.DataFrame:
        res = results.get(key)
        return res.df[[c for c in ("date", "value", "period_label") if c in res.df.columns]] if res is not None and res.ok else pd.DataFrame(columns=["date", "value"])

    ctx = DataContext()
    ipca, food = frame("ipca_mensal"), frame("ipca_alimentacao")
    ipca12 = accumulated_12m(ipca).dropna() if not ipca.empty else ipca
    food12 = accumulated_12m(food).dropna() if not food.empty else food
    selic = frame("selic_meta")
    real = build_real_interest(selic, ipca) if not selic.empty and not ipca.empty else pd.DataFrame()
    ibc = frame("ibcbr")
    ibc_yoy = ibc.assign(value=ibc["value"].pct_change(12) * 100).dropna() if not ibc.empty else ibc

    from src.transformation.metrics import monthly_last
    ctx.series = {
        "ipca_12m": ipca12, "ipca_alimentacao_12m": food12,
        "selic_meta": monthly_last(selic) if not selic.empty else selic,
        "juro_real": real[["date", "juro_real"]].rename(columns={"juro_real": "value"}) if not real.empty else pd.DataFrame(),
        "divida_bruta": frame("divida_bruta"), "divida_liquida": frame("divida_liquida"),
        "desocupacao": frame("desocupacao"), "subutilizacao": frame("subutilizacao"),
        "rendimento_real": frame("rendimento_real"), "endividamento_familias": frame("endividamento_familias"),
        "pib_4tri": frame("pib_4tri"), "pib_trimestral": frame("pib_trimestral"), "ibcbr_12m": ibc_yoy,
    }
    sec_res = results.get("seguranca_educacao")
    ctx.sec = sec_res.df if sec_res is not None and sec_res.ok else pd.DataFrame()
    if not ctx.sec.empty:
        for key in _ANNUAL_FROM_SEC:
            ctx.series[key] = _annual_series(ctx.sec, key)
            q = ctx.sec[(ctx.sec["indicador"] == key) & (ctx.sec["uf"] == "BR")]["qualidade"]
            ctx.quality[key] = "oficial" if len(q) and (q == "oficial").all() else "aproximado"
    for key in CATALOG:
        ctx.quality.setdefault(key, "oficial")
    ctx.raw = {
        "ipca_mensal": ipca, "ipca_alimentacao": food, "divida_bruta": frame("divida_bruta"), "real_rate": real,
        "desocupacao": frame("desocupacao"), "subutilizacao": frame("subutilizacao"),
        "rendimento_real": frame("rendimento_real"), "endividamento_familias": frame("endividamento_familias"),
        "pib_4tri": frame("pib_4tri"), "pib_trimestral": frame("pib_trimestral"), "ibcbr": ibc,
    }
    for key, res in results.items():
        if res.synced_at is not None:
            label = {"live": "ao vivo", "cache": "cache local", "fallback": "consolidado de referência"}.get(res.mode, res.mode)
            ctx.freshness[key] = f"{label} em {res.synced_at:%d/%m/%Y %H:%M}"
    return ctx


def freshness_note(ctx: DataContext) -> str:
    stamps = [v for v in ctx.freshness.values()]
    return stamps[0] if stamps else f"carregado em {datetime.now():%d/%m/%Y %H:%M}"
