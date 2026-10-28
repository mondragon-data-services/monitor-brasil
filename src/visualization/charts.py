"""Componentes gráficos com Data Storytelling embutido (títulos ativos calculados dos dados)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from src.config import IPCA_ITEMS, PALETTE
from src.transformation.metrics import (
    accumulated_12m,
    cumulative_index,
    detect_recession,
    latest_with_delta,
    split_items,
    value_days_ago,
    yoy_change_pp,
)
from src.visualization.theme import (
    add_events,
    add_inflation_band,
    base_layout,
    br,
    br_money,
    br_pct,
    br_pp,
    empty_figure,
)


def _has(*frames: pd.DataFrame | None) -> bool:
    return all(f is not None and not f.empty for f in frames)


def _line(x, y, name: str, color: str, fmt: str, width: float = 2.6, dash: str | None = None, **kw) -> go.Scatter:
    return go.Scatter(x=x, y=y, name=name, mode="lines", line=dict(color=color, width=width, dash=dash),
                      hovertemplate=f"{name}: {fmt}<extra></extra>", **kw)


# --------------------------------------------------------------------------- #
# Aba 1 — Inflação, juros e risco fiscal
# --------------------------------------------------------------------------- #
def inflation_chart(ipca_m: pd.DataFrame, food_m: pd.DataFrame, start: pd.Timestamp | None = None,
                    show_events: bool = False) -> go.Figure:
    if not _has(ipca_m, food_m):
        return empty_figure("IPCA ou Alimentação indisponível.")
    ipca = accumulated_12m(ipca_m).dropna()
    food = accumulated_12m(food_m).dropna()
    if start is not None:
        ipca, food = ipca[ipca["date"] >= start], food[food["date"] >= start]
    if ipca.empty or food.empty:
        return empty_figure("Sem dados no período selecionado.")
    i_last, f_last = ipca.iloc[-1]["value"], food.iloc[-1]["value"]
    gap = f_last - i_last
    verb = "pressionando" if gap > 0 else "aliviando"
    title = (f"Inflação de alimentos acumula {br_pct(f_last, 2)} em 12 meses, {verb} "
             f"{br(abs(gap), 1)} p.p. {'acima' if gap > 0 else 'abaixo'} do IPCA geral")
    status = ("acima do teto" if i_last > 4.5 else "dentro da faixa de tolerância" if i_last >= 1.5 else "abaixo do piso")
    subtitle = f"IPCA geral em {br_pct(i_last, 2)} ({status} da meta de 3,0% ± 1,5 p.p.)"
    fig = go.Figure()
    add_inflation_band(fig)
    fig.add_trace(_line(ipca["date"], ipca["value"], "IPCA geral (12m)", PALETTE.institutional, "%{y:.2f}%"))
    fig.add_trace(_line(food["date"], food["value"], "Alimentação (12m)", PALETTE.vulnerable, "%{y:.2f}%"))
    fig.update_yaxes(ticksuffix="%")
    if show_events:
        add_events(fig, ipca["date"].min(), ipca["date"].max())
    return base_layout(fig, title, subtitle)


def debt_chart(debt: pd.DataFrame, real_rate: pd.DataFrame, start: pd.Timestamp | None = None,
               show_events: bool = False) -> go.Figure:
    if not _has(debt, real_rate):
        return empty_figure("Dívida ou Selic indisponível.")
    d = debt.copy()
    r = real_rate.copy()
    if start is not None:
        d, r = d[d["date"] >= start], r[r["date"] >= start]
    if d.empty or r.empty:
        return empty_figure("Sem dados no período selecionado.")
    delta = latest_with_delta(debt, 12)
    last = d.iloc[-1]["value"]
    change = f"{br_pp(delta.change)} nos últimos 12 meses" if delta and delta.change == delta.change else "variação 12m indisponível"
    title = f"Dívida bruta atinge {br_pct(last, 1)} do PIB, com variação de {change}"
    subtitle = (f"Selic em {br_pct(r.iloc[-1]['selic'], 2)} a.a. e juro real de {br_pct(r.iloc[-1]['juro_real'], 1)} a.a.: "
                "quanto maior, mais caro rolar a dívida")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d["date"], y=d["value"], name="Dívida bruta (% do PIB)", mode="lines",
                             fill="tozeroy", line=dict(color=PALETTE.alert, width=2.4),
                             fillcolor="rgba(217,56,30,0.14)", yaxis="y",
                             hovertemplate="Dívida bruta: %{y:.1f}% do PIB<extra></extra>"))
    fig.add_trace(go.Scatter(x=r["date"], y=r["selic"], name="Selic (% a.a.)", mode="lines",
                             line=dict(color=PALETTE.neutral, width=2.2), yaxis="y2",
                             hovertemplate="Selic: %{y:.2f}% a.a.<extra></extra>"))
    fig.add_trace(go.Scatter(x=r["date"], y=r["juro_real"], name="Juro real (% a.a.)", mode="lines",
                             line=dict(color=PALETTE.institutional, width=2.2, dash="dash"), yaxis="y2",
                             hovertemplate="Juro real: %{y:.1f}% a.a.<extra></extra>"))
    fig.update_layout(
        yaxis=dict(title="% do PIB", ticksuffix="%", range=[max(0, d["value"].min() - 8), d["value"].max() + 4]),
        yaxis2=dict(title="% ao ano", overlaying="y", side="right", showgrid=False, ticksuffix="%"),
    )
    if show_events:
        add_events(fig, d["date"].min(), d["date"].max())
    return base_layout(fig, title, subtitle)


# --------------------------------------------------------------------------- #
# Aba 2 — Emprego, renda e recessão
# --------------------------------------------------------------------------- #
def labor_chart(unemp: pd.DataFrame, underuse: pd.DataFrame, show_events: bool = False) -> go.Figure:
    if not _has(unemp, underuse):
        return empty_figure("Dados de desocupação/subutilização indisponíveis.")
    u, s = unemp.sort_values("date"), underuse.sort_values("date")
    u_last, s_last = u.iloc[-1]["value"], s.iloc[-1]["value"]
    du = latest_with_delta(u, 12)
    ref = f"({br_pp(du.change)} em 12 meses)" if du and du.change == du.change else ""
    title = (f"Desemprego em {br_pct(u_last)} {ref}; contando desalentados e subocupados, "
             f"{br_pct(s_last)} da força de trabalho está subutilizada").replace("  ", " ")
    subtitle = f"Trimestre móvel encerrado em {u.iloc[-1].get('period_label', u.iloc[-1]['date'].strftime('%m/%Y'))}"
    fig = go.Figure()
    fig.add_trace(go.Bar(x=s["date"], y=s["value"], name="Subutilização (desocupados + desalentados + subocupados)",
                         marker_color="rgba(217,119,6,0.55)", hovertemplate="Subutilização: %{y:.1f}%<extra></extra>"))
    fig.add_trace(_line(u["date"], u["value"], "Desocupação (desemprego aberto)", PALETTE.alert, "%{y:.1f}%", width=3))
    fig.update_yaxes(ticksuffix="%", rangemode="tozero")
    fig.update_layout(barmode="overlay")
    if show_events:
        add_events(fig, u["date"].min(), u["date"].max())
    return base_layout(fig, title, subtitle)


def income_debt_chart(income: pd.DataFrame, household_debt: pd.DataFrame, show_events: bool = False) -> go.Figure:
    if not _has(income, household_debt):
        return empty_figure("Rendimento ou endividamento indisponível.")
    i, d = income.sort_values("date"), household_debt.sort_values("date")
    d = d[d["date"] >= i["date"].min() - pd.DateOffset(months=1)]
    di = latest_with_delta(i, 12)
    dd = latest_with_delta(d, 12)
    inc_txt = (f"{'sobe' if di.change > 0 else 'cai'} {br(abs(di.change / di.previous * 100), 1)}% em 12 meses"
               if di and di.change == di.change else "variação 12m indisponível")
    title = (f"Renda real do trabalhador chega a {br_money(i.iloc[-1]['value'])} e {inc_txt}, "
             f"com {br_pct(d.iloc[-1]['value'])} da renda das famílias comprometida em dívidas")
    sub = (f"Endividamento {br_pp(dd.change)} em 12 meses" if dd and dd.change == dd.change else "")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=i["date"], y=i["value"], name="Rendimento médio real (R$)", marker_color="rgba(43,108,176,0.55)",
                         hovertemplate="Rendimento: R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=d["date"], y=d["value"], name="Endividamento das famílias (%)", mode="lines",
                             line=dict(color=PALETTE.alert, width=2.6), yaxis="y2",
                             hovertemplate="Endividamento: %{y:.1f}%<extra></extra>"))
    fig.update_layout(
        yaxis=dict(title="R$", tickprefix="R$ ", range=[max(0, i["value"].min() * 0.85), i["value"].max() * 1.05]),
        yaxis2=dict(title="% da renda", overlaying="y", side="right", showgrid=False, ticksuffix="%"),
    )
    if show_events:
        add_events(fig, i["date"].min(), i["date"].max())
    return base_layout(fig, title, sub)


def recession_chart(pib_4q: pd.DataFrame, pib_qoq: pd.DataFrame, ibcbr: pd.DataFrame) -> tuple[go.Figure, str]:
    """Devolve a figura e o selo de status (Expansão / Desaceleração / Alerta de Recessão Técnica)."""
    if not _has(pib_4q):
        return empty_figure("PIB indisponível."), "Sem dados"
    p = pib_4q.sort_values("date")
    status = detect_recession(pib_qoq["value"]) if _has(pib_qoq) else "Sem dados"
    colors = [PALETTE.good if v >= 0 else PALETTE.alert for v in p["value"]]
    last = p.iloc[-1]
    title = (f"PIB cresce {br_pct(last['value'])} em 4 trimestres; "
             f"status da economia: {status.lower() if status != 'Sem dados' else 'indefinido'}")
    q_txt = ""
    if _has(pib_qoq):
        q = pib_qoq.sort_values("date")
        q_txt = f"Último trimestre contra o anterior: {br_pct(q.iloc[-1]['value'], 1, signed=True)}"
    fig = go.Figure()
    fig.add_trace(go.Bar(x=p["date"], y=p["value"], name="PIB — acumulado em 4 trimestres", marker_color=colors,
                         hovertemplate="PIB 4 trimestres: %{y:.1f}%<extra></extra>", opacity=0.85))
    if _has(ibcbr):
        ib = ibcbr.sort_values("date").copy()
        ib["yoy"] = ib["value"].pct_change(12) * 100
        ib = ib.dropna(subset=["yoy"])
        ib = ib[ib["date"] >= p["date"].min()]
        if not ib.empty:
            fig.add_trace(_line(ib["date"], ib["yoy"], "IBC-Br — variação em 12 meses", PALETTE.institutional, "%{y:.1f}%"))
    fig.add_hline(y=0, line_color=PALETTE.neutral, line_width=1)
    fig.update_yaxes(ticksuffix="%")
    return base_layout(fig, title, q_txt), status


# --------------------------------------------------------------------------- #
# Aba 3 — Segurança pública
# --------------------------------------------------------------------------- #
def _indicator(df: pd.DataFrame, name: str, uf: str = "BR") -> pd.DataFrame:
    return df[(df["indicador"] == name) & (df["uf"] == uf)].sort_values("ano")


def mvi_chart(sec: pd.DataFrame, uf: str = "BR") -> go.Figure:
    nat = _indicator(sec, "mvi_taxa", "BR")
    if nat.empty:
        return empty_figure("Série de MVI indisponível.")
    own = _indicator(sec, "mvi_taxa", uf) if uf != "BR" else nat
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=nat["ano"], y=nat["valor"], name="Brasil", mode="lines+markers",
                             line=dict(color=PALETTE.neutral, width=2.6 if uf == "BR" else 1.8,
                                       dash=None if uf == "BR" else "dash"),
                             hovertemplate="Brasil: %{y:.1f} por 100 mil<extra></extra>"))
    last_nat, first_nat = nat.iloc[-1], nat.iloc[0]
    if uf == "BR":
        change = (last_nat["valor"] / first_nat["valor"] - 1) * 100
        peak = nat.loc[nat["valor"].idxmax()]
        title = (f"Mortes violentas caem para {br(last_nat['valor'])} por 100 mil habitantes em {int(last_nat['ano'])}, "
                 f"{br(abs(change), 0)}% {'abaixo' if change < 0 else 'acima'} de {int(first_nat['ano'])}")
        sub = f"Pico da série: {br(peak['valor'])} em {int(peak['ano'])}"
        fig.data[0].line.color = PALETTE.good if change < 0 else PALETTE.alert
    else:
        if own.empty:
            return empty_figure(f"Sem série de MVI para {uf}.")
        fig.add_trace(go.Scatter(x=own["ano"], y=own["valor"], name=uf, mode="lines+markers",
                                 line=dict(color=PALETTE.alert, width=3),
                                 hovertemplate=f"{uf}: %{{y:.1f}} por 100 mil<extra></extra>"))
        o = own.iloc[-1]
        ratio = o["valor"] / nat[nat["ano"] == o["ano"]]["valor"].iloc[0]
        title = (f"{uf} registra {br(o['valor'])} mortes violentas por 100 mil habitantes em {int(o['ano'])}, "
                 f"{br(ratio, 1)}× a taxa do Brasil")
        sub = ("Apenas um ano de referência disponível para a UF — envie a planilha Sinesp VDE para ver a série completa"
               if len(own) == 1 else "")
    fig.update_xaxes(dtick=1)
    fig.update_yaxes(rangemode="tozero", ticksuffix="")
    fig.update_layout(hovermode="x unified")
    return base_layout(fig, title, sub)


def vulnerable_chart(sec: pd.DataFrame) -> go.Figure:
    fem = _indicator(sec, "feminicidio_total")
    est = _indicator(sec, "estupro_vulneravel_total")
    if fem.empty and est.empty:
        return empty_figure("Séries de feminicídio e estupro de vulnerável indisponíveis.")
    f_last = fem.iloc[-1] if not fem.empty else None
    e_last = est.iloc[-1] if not est.empty else None
    parts = []
    if f_last is not None:
        parts.append(f"{br(f_last['valor'], 0)} feminicídios em {int(f_last['ano'])}")
    if e_last is not None:
        parts.append(f"{br(e_last['valor'], 0)} vítimas de estupro de vulnerável em {int(e_last['ano'])}")
    title = "Crimes contra vulneráveis seguem em patamar alto: " + " e ".join(parts)
    fig = go.Figure()
    if not est.empty:
        fig.add_trace(go.Bar(x=est["ano"], y=est["valor"], name="Estupro de vulnerável (vítimas)",
                             marker_color="rgba(217,119,6,0.7)", hovertemplate="Estupro de vulnerável: %{y:,.0f}<extra></extra>"))
    if not fem.empty:
        fig.add_trace(go.Scatter(x=fem["ano"], y=fem["valor"], name="Feminicídios (vítimas)", mode="lines+markers",
                                 yaxis="y2", line=dict(color=PALETTE.alert, width=3),
                                 hovertemplate="Feminicídios: %{y:,.0f}<extra></extra>"))
    fig.update_layout(
        yaxis=dict(title="Estupro de vulnerável", rangemode="tozero"),
        yaxis2=dict(title="Feminicídios", overlaying="y", side="right", showgrid=False, rangemode="tozero"),
    )
    fig.update_xaxes(dtick=1)
    return base_layout(fig, title, "Registros policiais: parte do aumento pode refletir mais denúncias", wrap=52)


def uf_ranking_chart(sec: pd.DataFrame, indicator: str = "mvi_taxa", highlight: str | None = None) -> go.Figure:
    snap = sec[(sec["indicador"] == indicator) & (sec["uf"] != "BR")]
    if snap.empty:
        return empty_figure("Comparativo por UF indisponível.")
    year = int(snap["ano"].max())
    snap = snap[snap["ano"] == year].sort_values("valor")
    nat = _indicator(sec, indicator, "BR")
    nat_val = nat[nat["ano"] == year]["valor"]
    colors = [PALETTE.alert if u == highlight else PALETTE.vulnerable if v >= snap["valor"].median() else "rgba(74,85,104,0.55)"
              for u, v in zip(snap["uf"], snap["valor"], strict=True)]
    top = snap.iloc[-1]
    low = snap.iloc[0]
    title = (f"{top['uf']} lidera com {br(top['valor'])} mortes violentas por 100 mil habitantes, "
             f"{br(top['valor'] / low['valor'], 0)}× a taxa de {low['uf']} ({br(low['valor'])})")
    fig = go.Figure(go.Bar(x=snap["valor"], y=snap["uf"], orientation="h", marker_color=colors,
                           hovertemplate="%{y}: %{x:.1f} por 100 mil<extra></extra>"))
    if not nat_val.empty:
        fig.add_vline(x=float(nat_val.iloc[0]), line_dash="dash", line_color=PALETTE.institutional,
                      annotation_text=f"Brasil {br(nat_val.iloc[0])}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid)
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Comparativo entre estados — {year}", height=720, wrap=52)


# --------------------------------------------------------------------------- #
# Aba 4 — Educação
# --------------------------------------------------------------------------- #
def ideb_chart(sec: pd.DataFrame) -> go.Figure:
    levels = [("ideb_anos_iniciais", "Anos iniciais", PALETTE.good),
              ("ideb_anos_finais", "Anos finais", PALETTE.institutional),
              ("ideb_ensino_medio", "Ensino médio", PALETTE.alert)]
    fig = go.Figure()
    gaps: list[tuple[str, float]] = []
    for key, label, color in levels:
        real, meta = _indicator(sec, key), _indicator(sec, key.replace("ideb_", "ideb_meta_"))
        if real.empty:
            continue
        fig.add_trace(go.Scatter(x=real["ano"], y=real["valor"], name=f"{label} (observado)", mode="lines+markers",
                                 line=dict(color=color, width=2.8), hovertemplate=f"{label}: %{{y:.1f}}<extra></extra>"))
        if not meta.empty:
            fig.add_trace(go.Scatter(x=meta["ano"], y=meta["valor"], name=f"{label} (meta)", mode="lines",
                                     line=dict(color=color, width=1.4, dash="dot"),
                                     hovertemplate=f"Meta {label}: %{{y:.1f}}<extra></extra>"))
            m = meta[meta["ano"] == real.iloc[-1]["ano"]]
            if not m.empty:
                gaps.append((label, real.iloc[-1]["valor"] - m.iloc[0]["valor"]))
    if not fig.data:
        return empty_figure("Série do Ideb indisponível.")
    below = [g for g in gaps if g[1] < 0]
    year = int(_indicator(sec, "ideb_ensino_medio").iloc[-1]["ano"]) if not _indicator(sec, "ideb_ensino_medio").empty else ""
    if below:
        worst = min(below, key=lambda g: g[1])
        title = (f"Ideb fica abaixo da meta em {len(below)} de {len(gaps)} etapas em {year}; "
                 f"maior distância: {worst[0].lower()} ({br(worst[1])} ponto)")
    else:
        title = f"Ideb cumpre as metas em todas as etapas em {year}"
    fig.update_xaxes(dtick=2)
    fig.update_yaxes(range=[3, 7], title="Nota Ideb (0–10)")
    return base_layout(fig, title, "Linhas pontilhadas = metas oficiais do PNE")


def dropout_chart(sec: pd.DataFrame) -> go.Figure:
    ab, nn = _indicator(sec, "abandono_ensino_medio"), _indicator(sec, "nem_nem")
    if ab.empty and nn.empty:
        return empty_figure("Séries de abandono e nem-nem indisponíveis.")
    parts = []
    if not ab.empty:
        parts.append(f"abandono no ensino médio em {br(ab.iloc[-1]['valor'])}% ({int(ab.iloc[-1]['ano'])})")
    if not nn.empty:
        parts.append(f"{br(nn.iloc[-1]['valor'])}% dos jovens de 15 a 29 anos nem estudam nem trabalham ({int(nn.iloc[-1]['ano'])})")
    title = "Educação e futuro: " + "; ".join(parts)
    fig = go.Figure()
    if not nn.empty:
        fig.add_trace(go.Bar(x=nn["ano"], y=nn["valor"], name="Jovens 'nem-nem' (%)", marker_color="rgba(217,119,6,0.6)",
                             hovertemplate="Nem-nem: %{y:.1f}%<extra></extra>"))
    if not ab.empty:
        fig.add_trace(go.Scatter(x=ab["ano"], y=ab["valor"], name="Abandono no ensino médio (%)", mode="lines+markers",
                                 line=dict(color=PALETTE.alert, width=3), hovertemplate="Abandono: %{y:.1f}%<extra></extra>"))
    fig.update_yaxes(ticksuffix="%", rangemode="tozero")
    fig.update_xaxes(dtick=1)
    return base_layout(fig, title, "2020 e 2021 refletem o efeito da pandemia nos dados escolares")


def yoy_text(df: pd.DataFrame, periods: int) -> str:
    """Texto curto de variação p.p. contra o mesmo período do ano anterior."""
    diff = yoy_change_pp(df, periods).dropna()
    return br_pp(float(diff.iloc[-1])) if not diff.empty else "—"


# --------------------------------------------------------------------------- #
# Preços do dia a dia — gasolina (ANP) e itens do IPCA (IBGE)
# --------------------------------------------------------------------------- #
_ITEM_COLORS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#8C6D31", "#4A5568"]  # daltonismo-safe


def _gas(anp: pd.DataFrame, uf: str) -> pd.DataFrame:
    return anp[anp["uf"] == uf].sort_values("date")


def gasoline_chart(anp: pd.DataFrame, uf: str = "BR", start: pd.Timestamp | None = None, show_events: bool = False) -> go.Figure:
    br_all = _gas(anp, "BR") if not anp.empty else anp
    if br_all.empty:
        return empty_figure("Preço da gasolina indisponível.")
    br_df = br_all[br_all["date"] >= start] if start is not None else br_all
    last = br_all.iloc[-1]
    ago = value_days_ago(br_all, 365)
    chg = f", {br_pct((last['value'] / ago - 1) * 100, 1, signed=True)} em 12 meses" if ago else ""
    sub = f"Média semanal dos postos pesquisados pela ANP — semana encerrada em {last['date']:%d/%m/%Y}"
    fig = go.Figure()
    if uf == "BR":
        title = f"Gasolina comum custa {br_money(last['value'])} por litro no Brasil{chg}"
    else:
        own_all = _gas(anp, uf)
        if own_all.empty:
            return empty_figure(f"Sem preços de gasolina para {uf}.")
        o = own_all.iloc[-1]
        diff = o["value"] - last["value"]
        title = (f"Em {uf} a gasolina custa {br_money(o['value'])} o litro, {br_money(abs(diff))} "
                 f"{'acima' if diff > 0 else 'abaixo'} da média do Brasil ({br_money(last['value'])})")
        own = own_all[own_all["date"] >= start] if start is not None else own_all
        fig.add_trace(_line(own["date"], own["value"], uf, PALETTE.alert, "R$ %{y:.2f}", width=3))
    fig.add_trace(_line(br_df["date"], br_df["value"], "Brasil", PALETTE.institutional if uf == "BR" else PALETTE.neutral,
                        "R$ %{y:.2f}", width=3 if uf == "BR" else 2, dash=None if uf == "BR" else "dash"))
    fig.update_yaxes(tickprefix="R$ ", tickformat=".2f")
    if show_events and not br_df.empty:
        add_events(fig, br_df["date"].min(), br_df["date"].max())
    return base_layout(fig, title, sub)


def gasoline_ranking_chart(anp: pd.DataFrame, highlight: str | None = None) -> go.Figure:
    states = anp[anp["uf"] != "BR"] if not anp.empty else anp
    if states.empty:
        return empty_figure("Comparativo por estado indisponível.")
    week = states["date"].max()
    snap = states[states["date"] == week].sort_values("value")
    nat = _gas(anp, "BR")
    nat_val = nat[nat["date"] == week]["value"]
    ref = float(nat_val.iloc[0]) if len(nat_val) else float(snap["value"].median())
    top, low = snap.iloc[-1], snap.iloc[0]
    colors = [PALETTE.alert if u == highlight else PALETTE.vulnerable if v >= ref else "rgba(74,85,104,0.55)"
              for u, v in zip(snap["uf"], snap["value"], strict=True)]
    title = (f"Gasolina é mais cara no {top['uf']} ({br_money(top['value'])}) e mais barata no {low['uf']} "
             f"({br_money(low['value'])}): {br_money(top['value'] - low['value'])} de diferença por litro")
    fig = go.Figure(go.Bar(x=snap["value"], y=snap["uf"], orientation="h", marker_color=colors,
                           hovertemplate="%{y}: R$ %{x:.2f} por litro<extra></extra>"))
    if len(nat_val):
        fig.add_vline(x=ref, line_dash="dash", line_color=PALETTE.institutional,
                      annotation_text=f"Brasil {br_money(ref)}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid, tickprefix="R$ ", tickformat=".2f",
                     range=[max(0, snap["value"].min() - 0.6), snap["value"].max() + 0.3])
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Preço médio de revenda por estado — semana encerrada em {week:%d/%m/%Y}", height=720, wrap=52)


def _items_last_12m(items: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    rows = []
    last_date = None
    for key, df in split_items(items).items():
        acc = accumulated_12m(df).dropna()
        if not acc.empty:
            rows.append({"key": key, "label": IPCA_ITEMS[key].label, "group": IPCA_ITEMS[key].group,
                         "value": acc.iloc[-1]["value"]})
            last_date = acc.iloc[-1]["date"] if last_date is None else max(last_date, acc.iloc[-1]["date"])
    return pd.DataFrame(rows), last_date


def items_inflation_chart(items: pd.DataFrame) -> go.Figure:
    table, last_date = _items_last_12m(items)
    if table.empty or "geral" not in set(table["key"]):
        return empty_figure("Inflação por item indisponível.")
    geral = float(table[table["key"] == "geral"]["value"].iloc[0])
    bars = table[table["key"] != "geral"].sort_values("value")
    foods = bars[bars["group"] != "referencia"]
    above = int((foods["value"] > geral).sum())
    top = foods.iloc[-1]
    colors = [PALETTE.neutral if g == "referencia" else PALETTE.alert if v > geral else PALETTE.good
              for g, v in zip(bars["group"], bars["value"], strict=True)]
    title = (f"{top['label']} lidera a alta em 12 meses ({br_pct(top['value'], 1, signed=True)}); "
             f"{above} de {len(foods)} itens sobem mais que o IPCA geral ({br_pct(geral, 2)})")
    fig = go.Figure(go.Bar(x=bars["value"], y=bars["label"], orientation="h", marker_color=colors,
                           hovertemplate="%{y}: %{x:.1f}% em 12 meses<extra></extra>"))
    fig.add_vline(x=geral, line_dash="dash", line_color=PALETTE.institutional,
                  annotation_text=f"IPCA geral {br_pct(geral, 2)}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid, ticksuffix="%", zeroline=True, zerolinecolor=PALETTE.neutral)
    fig.update_yaxes(showgrid=False)
    mes = f"{last_date:%m/%Y}" if last_date is not None else ""
    return base_layout(fig, title, f"Inflação acumulada em 12 meses até {mes}. Vermelho = acima do IPCA geral · verde = abaixo",
                       height=560)


def real_cost_chart(items: pd.DataFrame) -> go.Figure:
    rows = []
    for key, df in split_items(items).items():
        if not df.empty:
            rows.append({"key": key, "label": IPCA_ITEMS[key].label, "group": IPCA_ITEMS[key].group,
                         "value": float(cumulative_index(df)["value"].iloc[-1]), "date": df["date"].max()})
    table = pd.DataFrame(rows)
    if table.empty or "geral" not in set(table["key"]):
        return empty_figure("Custo acumulado por item indisponível.")
    geral = float(table[table["key"] == "geral"]["value"].iloc[0])
    bars = table[table["key"] != "geral"].sort_values("value")
    foods = bars[bars["group"] != "referencia"]
    top, low = foods.iloc[-1], foods.iloc[0]
    colors = [PALETTE.neutral if g == "referencia" else PALETTE.alert if v > geral else PALETTE.good
              for g, v in zip(bars["group"], bars["value"], strict=True)]
    title = (f"O que custava R$ 100 em dez/2019 hoje custa R$ {br(geral, 0)} no IPCA geral; "
             f"{top['label'].lower()} foi o que mais subiu (R$ {br(top['value'], 0)}) e {low['label'].lower()} o que menos (R$ {br(low['value'], 0)})")
    fig = go.Figure(go.Bar(x=bars["value"], y=bars["label"], orientation="h", marker_color=colors,
                           hovertemplate="%{y}: R$ 100 viraram R$ %{x:.0f}<extra></extra>"))
    fig.add_vline(x=geral, line_dash="dash", line_color=PALETTE.institutional,
                  annotation_text=f"IPCA geral: R$ {br(geral, 0)}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid, tickprefix="R$ ", range=[90, bars["value"].max() * 1.06])
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Valor hoje de uma compra que custava R$ 100 em dezembro de 2019 (até {table['date'].max():%m/%Y})",
                       height=560, wrap=85)


def items_timeline_chart(items: pd.DataFrame, keys: list[str], start: pd.Timestamp | None = None,
                         show_events: bool = False) -> go.Figure:
    per_item = split_items(items)
    if not per_item or "geral" not in per_item:
        return empty_figure("Série de inflação por item indisponível.")
    fig = go.Figure()
    summary: list[tuple[str, float]] = []
    chosen = [k for k in keys if k in per_item and k != "geral"]
    for i, key in enumerate(chosen):
        acc = accumulated_12m(per_item[key]).dropna()
        acc = acc[acc["date"] >= start] if start is not None else acc
        if acc.empty:
            continue
        fig.add_trace(_line(acc["date"], acc["value"], IPCA_ITEMS[key].label, _ITEM_COLORS[i % len(_ITEM_COLORS)], "%{y:.1f}%"))
        summary.append((IPCA_ITEMS[key].label, float(acc.iloc[-1]["value"])))
    g = accumulated_12m(per_item["geral"]).dropna()
    g = g[g["date"] >= start] if start is not None else g
    if g.empty or not summary:
        return empty_figure("Selecione itens com dados no período.")
    fig.add_trace(_line(g["date"], g["value"], "IPCA geral", PALETTE.neutral, "%{y:.1f}%", width=2.2, dash="dash"))
    fig.add_hline(y=0, line_color=PALETTE.grid, line_width=1)
    top = max(summary, key=lambda t: t[1])
    title = (f"{top[0]} tem a maior alta entre os itens escolhidos: {br_pct(top[1], 1, signed=True)} em 12 meses "
             f"(IPCA geral: {br_pct(float(g.iloc[-1]['value']), 2)})")
    fig.update_yaxes(ticksuffix="%")
    if show_events:
        add_events(fig, g["date"].min(), g["date"].max())
    return base_layout(fig, title, "Inflação acumulada em 12 meses de cada item (juros compostos sobre o IPCA mensal do IBGE)")
