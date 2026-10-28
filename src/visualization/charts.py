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


def items_inflation_chart(items: pd.DataFrame, groups: tuple[str, ...] | None = None) -> go.Figure:
    table, last_date = _items_last_12m(items)
    if table.empty or "geral" not in set(table["key"]):
        return empty_figure("Inflação por item indisponível.")
    geral = float(table[table["key"] == "geral"]["value"].iloc[0])
    bars = table[table["key"] != "geral"].sort_values("value")
    if groups:
        bars = bars[bars["group"].isin(groups)]
    if bars.empty:
        return empty_figure("Sem itens para exibir.")
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


def real_cost_chart(items: pd.DataFrame, groups: tuple[str, ...] | None = None) -> go.Figure:
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
    if groups:
        bars = bars[bars["group"].isin(groups)]
    if bars.empty:
        return empty_figure("Sem itens para exibir.")
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


# --------------------------------------------------------------------------- #
# Dólar, expectativas (Focus), resultado fiscal, crédito das famílias, salário mínimo
# --------------------------------------------------------------------------- #
def _focus_series(focus: pd.DataFrame, indicator: str, year: int) -> pd.DataFrame:
    if focus is None or focus.empty:
        return pd.DataFrame(columns=["date", "value"])
    sub = focus[(focus["indicador"] == indicator) & (focus["ano"] == year)].sort_values("date")
    return pd.DataFrame({"date": sub["date"], "value": sub["mediana"]})


def dollar_chart(dolar: pd.DataFrame, focus: pd.DataFrame | None = None, start: pd.Timestamp | None = None,
                 show_events: bool = False) -> go.Figure:
    if dolar is None or dolar.empty:
        return empty_figure("Cotação do dólar indisponível.")
    d_all = dolar.sort_values("date")
    d = d_all[d_all["date"] >= start] if start is not None else d_all
    last = d_all.iloc[-1]
    ago = value_days_ago(d_all, 365)
    chg = f" ({br_pct((last['value'] / ago - 1) * 100, 1, signed=True)} em 12 meses)" if ago else ""
    year = int(last["date"].year)
    exp = _focus_series(focus, "Câmbio", year)
    exp_txt = f"; o mercado espera {br_money(exp.iloc[-1]['value'])} no fim de {year}" if not exp.empty else ""
    fig = go.Figure()
    fig.add_trace(_line(d["date"], d["value"], "Dólar (venda)", PALETTE.institutional, "R$ %{y:.2f}", width=2))
    fig.update_yaxes(tickprefix="R$ ", tickformat=".2f")
    if show_events and not d.empty:
        add_events(fig, d["date"].min(), d["date"].max())
    return base_layout(fig, f"Dólar fecha a {br_money(last['value'])}{chg}{exp_txt}",
                       f"Cotação diária de venda — Banco Central (último dado: {last['date']:%d/%m/%Y})")


def focus_ipca_chart(focus: pd.DataFrame, start: pd.Timestamp | None = None) -> go.Figure:
    if focus is None or focus.empty or "IPCA" not in set(focus["indicador"]):
        return empty_figure("Expectativas do Focus indisponíveis.")
    ipca = focus[focus["indicador"] == "IPCA"]
    last_day = ipca["date"].max()
    years = sorted(int(y) for y in ipca[ipca["date"] == last_day]["ano"].unique())[:2]
    fig = go.Figure()
    add_inflation_band(fig)
    colors = [PALETTE.alert, PALETTE.institutional]
    now_vals: list[tuple[int, float, float | None]] = []
    for i, y in enumerate(years):
        s = _focus_series(focus, "IPCA", y)
        s_view = s[s["date"] >= start] if start is not None else s
        if s_view.empty:
            continue
        fig.add_trace(_line(s_view["date"], s_view["value"], f"Expectativa para {y}", colors[i % 2], "%{y:.2f}%", width=2.8))
        now_vals.append((y, float(s.iloc[-1]["value"]), value_days_ago(s, 365)))
    if not now_vals:
        return empty_figure("Expectativas do Focus indisponíveis no período.")
    y0, v0, ago0 = now_vals[0]
    rel = ("acima do teto da meta (4,5%)" if v0 > 4.5 else "dentro da faixa da meta" if v0 >= 1.5 else "abaixo da meta")
    prev = f", contra {br_pct(ago0, 2)} um ano atrás" if ago0 is not None else ""
    nxt = f"; para {now_vals[1][0]}, {br_pct(now_vals[1][1], 2)}" if len(now_vals) > 1 else ""
    fig.update_yaxes(ticksuffix="%")
    return base_layout(fig, f"Mercado espera IPCA de {br_pct(v0, 2)} em {y0}, {rel}{prev}{nxt}",
                       f"Mediana das projeções do Boletim Focus (Banco Central), atualizada em {last_day:%d/%m/%Y}")


def focus_snapshot(focus: pd.DataFrame) -> pd.DataFrame:
    """Última mediana por indicador e ano (para os cartões de expectativas)."""
    if focus is None or focus.empty:
        return pd.DataFrame(columns=["indicador", "ano", "mediana", "date"])
    last = focus["date"].max()
    return focus[focus["date"] == last][["indicador", "ano", "mediana", "date"]].sort_values(["indicador", "ano"]).reset_index(drop=True)


def fiscal_result_chart(primary: pd.DataFrame, nominal: pd.DataFrame, start: pd.Timestamp | None = None,
                        show_events: bool = False) -> go.Figure:
    if primary is None or nominal is None or primary.empty or nominal.empty:
        return empty_figure("Resultado fiscal indisponível.")
    m = primary.rename(columns={"value": "primario"}).merge(nominal.rename(columns={"value": "nominal"}), on="date", how="inner")
    m["juros"] = m["nominal"] - m["primario"]
    if start is not None:
        m = m[m["date"] >= start]
    if m.empty:
        return empty_figure("Sem dados no período selecionado.")
    last = m.iloc[-1]
    if last["primario"] > 0:
        detail = (f"{br_pct(last['juros'], 1)} do PIB são só juros da dívida e {br_pct(last['primario'], 1)} é déficit primário")
    else:
        detail = (f"há superávit primário de {br_pct(abs(last['primario']), 1)} do PIB, mas os juros ({br_pct(last['juros'], 1)}) "
                  "levam ao déficit")
    title = f"Setor público fecha 12 meses com déficit nominal de {br_pct(last['nominal'], 1)} do PIB: {detail}"
    fig = go.Figure()
    fig.add_trace(_line(m["date"], m["nominal"], "Déficit nominal (primário + juros)", PALETTE.alert, "%{y:.1f}% do PIB", width=3))
    fig.add_trace(_line(m["date"], m["juros"], "Juros da dívida", PALETTE.vulnerable, "%{y:.1f}% do PIB"))
    fig.add_trace(_line(m["date"], m["primario"], "Resultado primário", PALETTE.institutional, "%{y:.1f}% do PIB", dash="dash"))
    fig.add_hline(y=0, line_color=PALETTE.neutral, line_width=1)
    fig.update_yaxes(ticksuffix="%")
    if show_events:
        add_events(fig, m["date"].min(), m["date"].max())
    return base_layout(fig, title, f"Acumulado em 12 meses, % do PIB. Positivo = déficit · negativo = superávit (dados até {last['date']:%m/%Y})")


def credit_chart(default_pf: pd.DataFrame, revolving: pd.DataFrame, overdraft: pd.DataFrame | None = None,
                 start: pd.Timestamp | None = None, show_events: bool = False) -> go.Figure:
    if default_pf is None or revolving is None or default_pf.empty or revolving.empty:
        return empty_figure("Dados de crédito indisponíveis.")
    inad = default_pf.sort_values("date")
    rot = revolving.sort_values("date")
    if start is not None:
        inad, rot = inad[inad["date"] >= start], rot[rot["date"] >= start]
    if inad.empty or rot.empty:
        return empty_figure("Sem dados no período selecionado.")
    ago = latest_with_delta(default_pf, 12)
    chg = f" ({br_pp(ago.change)} em 12 meses)" if ago and ago.change == ago.change else ""
    title = (f"Inadimplência das famílias está em {br_pct(inad.iloc[-1]['value'], 1)}{chg}; "
             f"o rotativo do cartão cobra {br_pct(rot.iloc[-1]['value'], 0)} ao ano")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=inad["date"], y=inad["value"], name="Inadimplência (% do crédito)", mode="lines",
                             line=dict(color=PALETTE.alert, width=3), hovertemplate="Inadimplência: %{y:.1f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=rot["date"], y=rot["value"], name="Juros do cartão rotativo (% a.a.)", mode="lines", yaxis="y2",
                             line=dict(color=PALETTE.vulnerable, width=2.4), hovertemplate="Rotativo: %{y:.0f}% a.a.<extra></extra>"))
    if overdraft is not None and not overdraft.empty:
        ov = overdraft.sort_values("date")
        ov = ov[ov["date"] >= start] if start is not None else ov
        fig.add_trace(go.Scatter(x=ov["date"], y=ov["value"], name="Juros do cheque especial (% a.a.)", mode="lines", yaxis="y2",
                                 line=dict(color=PALETTE.neutral, width=2, dash="dash"),
                                 hovertemplate="Cheque especial: %{y:.0f}% a.a.<extra></extra>"))
    fig.update_layout(yaxis=dict(title="Inadimplência", ticksuffix="%", rangemode="tozero"),
                      yaxis2=dict(title="Juros ao ano", overlaying="y", side="right", showgrid=False, ticksuffix="%", rangemode="tozero"))
    if show_events:
        add_events(fig, inad["date"].min(), inad["date"].max())
    return base_layout(fig, title, "Pessoas físicas — Banco Central. Inadimplência = atraso acima de 90 dias")


def min_wage_chart(min_wage: pd.DataFrame, ipca_monthly: pd.DataFrame, start: pd.Timestamp | None = None) -> go.Figure:
    from src.transformation.metrics import real_minimum_wage
    if min_wage is None or ipca_monthly is None or min_wage.empty or ipca_monthly.empty:
        return empty_figure("Salário mínimo indisponível.")
    m = real_minimum_wage(min_wage, ipca_monthly)
    ref = m[m["date"] >= (start if start is not None else pd.Timestamp("2019-01-01"))]
    if ref.empty:
        return empty_figure("Sem dados no período selecionado.")
    first, last = ref.iloc[0], ref.iloc[-1]
    change = (last["real"] / first["real"] - 1) * 100
    word = "mais" if change >= 0 else "menos"
    title = (f"Salário mínimo de {br_money(last['nominal'])} tem {br(abs(change), 1)}% {word} poder de compra "
             f"que em {first['date']:%m/%Y}, já descontada a inflação")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ref["date"], y=ref["nominal"], name="Valor nominal (R$)", mode="lines", line_shape="hv",
                             line=dict(color=PALETTE.neutral, width=2, dash="dash"), hovertemplate="Nominal: R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=ref["date"], y=ref["real"], name="Em reais de hoje (corrigido pelo IPCA)", mode="lines",
                             line=dict(color=PALETTE.good if change >= 0 else PALETTE.alert, width=3),
                             hovertemplate="Em reais de hoje: R$ %{y:,.2f}<extra></extra>"))
    fig.update_yaxes(tickprefix="R$ ", rangemode="tozero")
    return base_layout(fig, title, f"Valores corrigidos pelo IPCA até {last['date']:%m/%Y}. Linha tracejada = valor da época")


# --------------------------------------------------------------------------- #
# Pobreza, desigualdade, fome, qualidade do emprego e Bolsa Família
# --------------------------------------------------------------------------- #
def _from(df: pd.DataFrame, start: pd.Timestamp | None) -> pd.DataFrame:
    return df[df["date"] >= start] if start is not None and not df.empty else df


def _ok(*frames: pd.DataFrame | None) -> bool:
    return all(f is not None and not f.empty for f in frames)


def poverty_chart(poverty: pd.DataFrame, extreme: pd.DataFrame, start: pd.Timestamp | None = None) -> go.Figure:
    if not _ok(poverty, extreme):
        return empty_figure("Dados de pobreza indisponíveis.")
    p = _from(poverty.sort_values("date"), start or pd.Timestamp("2012-01-01"))
    e = _from(extreme.sort_values("date"), start or pd.Timestamp("2012-01-01"))
    pl, el = p.iloc[-1], e.iloc[-1]
    prev = p.iloc[-2]["value"] if len(p) > 1 else None
    chg = f" ({br_pp(pl['value'] - prev)} contra {p.iloc[-2]['date'].year})" if prev is not None else ""
    low = " — o menor nível da série" if pl["value"] <= p["value"].min() else ""
    title = (f"Pobreza atinge {br_pct(pl['value'], 1)} da população em {pl['date'].year}{chg}{low}; "
             f"extrema pobreza, {br_pct(el['value'], 1)}")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=p["date"], y=p["value"], name="Pobreza (menos de US$ 8,30/dia)", mode="lines+markers",
                             line=dict(color=PALETTE.vulnerable, width=3), hovertemplate="Pobreza: %{y:.1f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=e["date"], y=e["value"], name="Extrema pobreza (menos de US$ 3,00/dia)", mode="lines+markers",
                             line=dict(color=PALETTE.alert, width=3), hovertemplate="Extrema pobreza: %{y:.1f}%<extra></extra>"))
    fig.update_yaxes(ticksuffix="%", rangemode="tozero")
    return base_layout(fig, title, "% da população, em dólares de poder de compra (PPC) — PNAD Contínua/IBGE via Ipeadata")


def gini_chart(gini: pd.DataFrame) -> go.Figure:
    if not _ok(gini):
        return empty_figure("Índice de Gini indisponível.")
    g = gini.sort_values("date")
    last = g.iloc[-1]
    prev = g.iloc[-2] if len(g) > 1 else None
    low = g.loc[g["value"].idxmin()]
    move = ""
    if prev is not None:
        move = f", {'acima' if last['value'] > prev['value'] else 'abaixo'} de {br(prev['value'], 3)} em {prev['date'].year}"
    title = (f"Desigualdade de renda (Gini) fica em {br(last['value'], 3)} em {last['date'].year}{move}; "
             f"o menor valor da série foi {br(low['value'], 3)} em {low['date'].year}")
    fig = go.Figure(go.Scatter(x=g["date"], y=g["value"], mode="lines+markers", line=dict(color=PALETTE.institutional, width=3),
                               hovertemplate="Gini: %{y:.3f}<extra></extra>"))
    fig.update_yaxes(range=[max(0.0, g["value"].min() - 0.03), g["value"].max() + 0.02], tickformat=".2f")
    return base_layout(fig, title, "Índice de Gini da renda domiciliar per capita: 0 = igualdade total, 1 = desigualdade máxima (IBGE)")


def food_insecurity_chart(food: pd.DataFrame) -> go.Figure:
    if not _ok(food):
        return empty_figure("Dados de segurança alimentar indisponíveis.")
    from src.config import SEGURANCA_ALIMENTAR_LABEL as LABEL
    pivot = food.pivot_table(index="date", columns="cat_code", values="value", aggfunc="last").sort_index()
    need = {"109098", "109099", "109100", "109101", "109102"}
    if not need <= set(pivot.columns):
        return empty_figure("Categorias de segurança alimentar incompletas.")
    last, first = pivot.iloc[-1], pivot.iloc[0]
    y1, y0 = pivot.index[-1].year, pivot.index[0].year
    cmp_txt = (f", contra {br_pct(first['109099'], 1)} e {br_pct(first['109102'], 1)} em {y0}" if len(pivot) > 1 else "")
    title = (f"{br_pct(last['109099'], 1)} dos domicílios convivem com insegurança alimentar em {y1}; "
             f"{br_pct(last['109102'], 1)} passam fome (grave){cmp_txt}")
    fig = go.Figure()
    for code, color in (("109102", PALETTE.alert), ("109101", PALETTE.vulnerable), ("109100", "#F6C26B"), ("109098", PALETTE.good)):
        fig.add_trace(go.Bar(x=[str(d.year) for d in pivot.index], y=pivot[code], name=LABEL[code], marker_color=color,
                             hovertemplate=f"{LABEL[code]}: %{{y:.1f}}%<extra></extra>"))
    fig.update_layout(barmode="stack")
    fig.update_yaxes(ticksuffix="%", range=[0, 100])
    return base_layout(fig, title, "% dos domicílios por situação de segurança alimentar — PNAD Contínua/IBGE")


def informality_chart(informality: pd.DataFrame, start: pd.Timestamp | None = None) -> go.Figure:
    if not _ok(informality):
        return empty_figure("Taxa de informalidade indisponível.")
    d = informality.sort_values("date")
    delta = latest_with_delta(d, 12)
    chg = f" ({br_pp(delta.change)} em 12 meses)" if delta and delta.change == delta.change else ""
    v = _from(d, start)
    title = f"Informalidade atinge {br_pct(d.iloc[-1]['value'], 1)} dos trabalhadores ocupados{chg}"
    fig = go.Figure(go.Scatter(x=v["date"], y=v["value"], mode="lines", line=dict(color=PALETTE.vulnerable, width=3),
                               hovertemplate="Informalidade: %{y:.1f}%<extra></extra>"))
    fig.update_yaxes(ticksuffix="%", range=[max(0, v["value"].min() - 3), v["value"].max() + 2])
    return base_layout(fig, title, f"Trabalhadores sem carteira, sem CNPJ ou sem registro — trimestre móvel até "
                                   f"{d.iloc[-1].get('period_label', d.iloc[-1]['date'].strftime('%m/%Y'))}")


def job_quality_chart(position: pd.DataFrame, start: pd.Timestamp | None = None) -> go.Figure:
    if not _ok(position):
        return empty_figure("Dados de posição na ocupação indisponíveis.")
    wide = position.pivot_table(index="date", columns="cat_code", values="value", aggfunc="last").sort_index() / 1000.0  # milhões
    need = {"31722", "31723", "31726", "96171"}
    if not need <= set(wide.columns):
        return empty_figure("Categorias de ocupação incompletas.")
    wide["sem_carteira"] = wide["31723"] + wide["31726"]
    v = wide[wide.index >= start] if start is not None else wide
    last = wide.iloc[-1]
    title = (f"{br(last['31722'], 1)} milhões de empregados têm carteira no setor privado; {br(last['sem_carteira'], 1)} milhões "
             f"trabalham sem carteira e {br(last['96171'], 1)} milhões por conta própria")
    fig = go.Figure()
    for col, name, color in (("31722", "Setor privado com carteira", PALETTE.good), ("sem_carteira", "Sem carteira (privado + doméstico)", PALETTE.alert),
                             ("96171", "Conta própria", PALETTE.vulnerable)):
        fig.add_trace(go.Scatter(x=v.index, y=v[col], name=name, mode="lines", line=dict(color=color, width=2.8),
                                 hovertemplate=f"{name}: %{{y:.1f}} milhões<extra></extra>"))
    fig.update_yaxes(ticksuffix=" mi", rangemode="tozero")
    return base_layout(fig, title, "Pessoas ocupadas, em milhões — PNAD Contínua (trimestre móvel)")


def caged_chart(balance: pd.DataFrame, start: pd.Timestamp | None = None) -> go.Figure:
    from src.transformation.metrics import rolling_sum
    if not _ok(balance):
        return empty_figure("Saldo do Caged indisponível.")
    b = balance.sort_values("date")
    roll = rolling_sum(b, 12)
    last = b.iloc[-1]
    r12 = roll.iloc[-1]["value"] if not roll.empty else None
    verb = "abre" if (r12 or 0) >= 0 else "fecha"
    r12_txt = f"Brasil {verb} {br(abs(r12) / 1000, 0)} mil vagas com carteira em 12 meses; " if r12 is not None else ""
    word = "criou" if last["value"] >= 0 else "perdeu"
    title = f"{r12_txt}em {last['date']:%m/%Y} o saldo foi de {br(last['value'], 0, signed=True)} vagas ({word} empregos formais)"
    v = _from(b, start)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=v["date"], y=v["value"], name="Saldo do mês (vagas)", marker_color=[PALETTE.good if x >= 0 else PALETTE.alert for x in v["value"]],
                         hovertemplate="Saldo: %{y:,.0f} vagas<extra></extra>", opacity=0.8))
    rv = _from(roll, start)
    if not rv.empty:
        fig.add_trace(go.Scatter(x=rv["date"], y=rv["value"], name="Acumulado em 12 meses", mode="lines", yaxis="y2",
                                 line=dict(color=PALETTE.institutional, width=3), hovertemplate="12 meses: %{y:,.0f} vagas<extra></extra>"))
    fig.update_layout(yaxis=dict(title="Saldo mensal"), yaxis2=dict(title="Acumulado em 12 meses", overlaying="y", side="right", showgrid=False))
    return base_layout(fig, title, "Saldo de admissões menos desligamentos com carteira assinada — Novo Caged, com ajuste (Ministério do Trabalho via Ipeadata)")


def income_mass_chart(mass: pd.DataFrame, discouraged: pd.DataFrame, start: pd.Timestamp | None = None) -> go.Figure:
    if not _ok(mass, discouraged):
        return empty_figure("Massa de rendimento ou desalento indisponível.")
    m, d = mass.sort_values("date"), discouraged.sort_values("date")
    dm = latest_with_delta(m, 12)
    chg = f" ({br_pct(dm.change / dm.previous * 100, 1, signed=True)} em 12 meses)" if dm and dm.change == dm.change else ""
    title = (f"Massa de rendimento do trabalho chega a R$ {br(m.iloc[-1]['value'] / 1000, 1)} bilhões por mês{chg}; "
             f"desalentados são {br_pct(d.iloc[-1]['value'], 1)} da força de trabalho")
    mv, dv = _from(m, start), _from(d, start)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=mv["date"], y=mv["value"] / 1000, name="Massa de rendimento real (R$ bilhões/mês)", mode="lines",
                             line=dict(color=PALETTE.institutional, width=3), hovertemplate="R$ %{y:.1f} bi<extra></extra>"))
    fig.add_trace(go.Scatter(x=dv["date"], y=dv["value"], name="Desalentados (%)", mode="lines", yaxis="y2",
                             line=dict(color=PALETTE.alert, width=2.4, dash="dash"), hovertemplate="Desalento: %{y:.1f}%<extra></extra>"))
    fig.update_layout(yaxis=dict(title="R$ bilhões", tickprefix="R$ "), yaxis2=dict(title="% desalentados", overlaying="y", side="right",
                                                                                       showgrid=False, ticksuffix="%", rangemode="tozero"))
    return base_layout(fig, title, "Massa de rendimento real habitual de todos os trabalhos e pessoas que desistiram de procurar emprego — PNAD Contínua")


def bolsa_familia_chart(benefit: pd.DataFrame, start: pd.Timestamp | None = None) -> go.Figure:
    if not _ok(benefit):
        return empty_figure("Valor do Bolsa Família indisponível.")
    b = benefit.sort_values("date")
    delta = latest_with_delta(b, 12)
    chg = f" ({br_pct(delta.change / delta.previous * 100, 1, signed=True)} em 12 meses)" if delta and delta.change == delta.change else ""
    last = b.iloc[-1]
    v = _from(b, start)
    title = f"Bolsa Família paga R$ {br(last['value'] / 1e6, 1)} bilhões em {last['date']:%m/%Y}{chg}"
    fig = go.Figure(go.Bar(x=v["date"], y=v["value"] / 1e6, marker_color="rgba(43,108,176,0.7)",
                           hovertemplate="R$ %{y:.1f} bilhões<extra></extra>"))
    fig.update_yaxes(tickprefix="R$ ", ticksuffix=" bi", rangemode="tozero")
    return base_layout(fig, title, "Valor total pago por mês pelo governo federal (não inclui o número de famílias) — Ministério do Desenvolvimento Social")


# --------------------------------------------------------------------------- #
# Saúde: dengue, vacinação, mortalidade na infância, expectativa de vida
# --------------------------------------------------------------------------- #
def dengue_weekly_chart(dengue: pd.DataFrame, uf: str = "BR", trim_weeks: int = 3, years: int = 4) -> go.Figure:
    from src.transformation.metrics import dengue_cumulative, dengue_last_complete_week, dengue_national
    if not _ok(dengue):
        return empty_figure("Dados de dengue indisponíveis.")
    nat = dengue_national(dengue, uf)
    done = dengue_last_complete_week(dengue, trim_weeks)
    if nat.empty or done is None:
        return empty_figure(f"Sem casos de dengue para {uf}.")
    year, week = done
    cur = dengue_cumulative(dengue, year, week, uf)
    prev = dengue_cumulative(dengue, year - 1, week, uf)
    pct = (cur["casos"] / prev["casos"] - 1) * 100 if prev["casos"] else None
    cmp_txt = (f", {br(abs(pct), 0)}% {'a mais' if pct > 0 else 'a menos'} que no mesmo período de {year - 1}" if pct is not None else "")
    where = "no Brasil" if uf == "BR" else f"em {uf}"
    title = f"Dengue {where}: {br(cur['casos'], 0)} casos prováveis em {year} até a semana {week}{cmp_txt}"
    palette = [PALETTE.alert, PALETTE.institutional, PALETTE.vulnerable, PALETTE.neutral]
    fig = go.Figure()
    for i, y in enumerate(sorted(nat["ano"].unique(), reverse=True)[:years]):
        d = nat[nat["ano"] == y]
        if y == year:
            d = d[d["semana"] <= week]
        fig.add_trace(go.Scatter(x=d["semana"], y=d["casos"], name=str(y), mode="lines", line=dict(color=palette[i % 4], width=3.2 if y == year else 1.8),
                                 hovertemplate=f"{y}: %{{y:,.0f}} casos<extra></extra>"))
    fig.update_xaxes(title="Semana epidemiológica", dtick=4)
    fig.update_yaxes(rangemode="tozero")
    deaths = f" · {br(cur['obitos'], 0)} mortes confirmadas em {year}" if cur["obitos"] else ""
    return base_layout(fig, title, f"Casos prováveis por semana (SINAN). As últimas {trim_weeks} semanas ficam ocultas por atraso de registro{deaths}")


def dengue_uf_chart(dengue: pd.DataFrame, highlight: str | None = None, trim_weeks: int = 3) -> go.Figure:
    from src.ingestion.sinesp_loader import BRAZIL_POPULATION, UF_POPULATION
    from src.transformation.metrics import dengue_cumulative, dengue_incidence_by_uf, dengue_last_complete_week
    if not _ok(dengue):
        return empty_figure("Dados de dengue indisponíveis.")
    done = dengue_last_complete_week(dengue, trim_weeks)
    if done is None:
        return empty_figure("Dados de dengue indisponíveis.")
    year, week = done
    inc = dengue_incidence_by_uf(dengue, year, week, UF_POPULATION)
    if inc.empty:
        return empty_figure("Incidência por estado indisponível.")
    br_inc = dengue_cumulative(dengue, year, week)["casos"] / BRAZIL_POPULATION * 100_000
    bars = inc.sort_values("incidencia")
    top = inc.iloc[0]
    colors = [PALETTE.alert if u == highlight else PALETTE.vulnerable if v >= br_inc else "rgba(74,85,104,0.55)"
              for u, v in zip(bars["uf"], bars["incidencia"], strict=True)]
    title = (f"{top['uf']} tem a maior incidência de dengue em {year}: {br(top['incidencia'], 0)} casos por 100 mil habitantes "
             f"(Brasil: {br(br_inc, 0)})")
    fig = go.Figure(go.Bar(x=bars["incidencia"], y=bars["uf"], orientation="h", marker_color=colors,
                           hovertemplate="%{y}: %{x:,.0f} casos por 100 mil<extra></extra>"))
    fig.add_vline(x=br_inc, line_dash="dash", line_color=PALETTE.institutional, annotation_text=f"Brasil {br(br_inc, 0)}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid)
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Casos prováveis acumulados até a semana {week} de {year}, por 100 mil habitantes (população do Censo 2022)",
                       height=720, wrap=52)


def vaccine_chart(measles: pd.DataFrame, dpt: pd.DataFrame | None = None, start: pd.Timestamp | None = None) -> go.Figure:
    from src.config import VACCINE_TARGET
    if not _ok(measles):
        return empty_figure("Cobertura vacinal indisponível.")
    m = _from(measles.sort_values("date"), start or pd.Timestamp("2000-01-01"))
    last = m.iloc[-1]
    peak = m.loc[m["value"].idxmax()]
    rel = "abaixo" if last["value"] < VACCINE_TARGET else "acima"
    title = (f"Cobertura da vacina contra sarampo está em {br_pct(last['value'], 0)} em {last['date'].year}, {rel} da meta de "
             f"{br(VACCINE_TARGET, 0)}%; o pico foi {br_pct(peak['value'], 0)} em {peak['date'].year}")
    fig = go.Figure()
    fig.add_hrect(y0=VACCINE_TARGET, y1=105, fillcolor="rgba(27,158,119,0.10)", line_width=0, layer="below")
    fig.add_hline(y=VACCINE_TARGET, line_dash="dash", line_color=PALETTE.good, annotation_text="Meta 95%", annotation_position="bottom right")
    fig.add_trace(go.Scatter(x=m["date"], y=m["value"], name="Sarampo (tríplice viral)", mode="lines+markers",
                             line=dict(color=PALETTE.institutional, width=3), hovertemplate="Sarampo: %{y:.0f}%<extra></extra>"))
    if dpt is not None and not dpt.empty:
        d = _from(dpt.sort_values("date"), start or pd.Timestamp("2000-01-01"))
        fig.add_trace(go.Scatter(x=d["date"], y=d["value"], name="DTP (difteria, tétano e coqueluche)", mode="lines+markers",
                                 line=dict(color=PALETTE.vulnerable, width=2.4, dash="dot"), hovertemplate="DTP: %{y:.0f}%<extra></extra>"))
    fig.update_yaxes(ticksuffix="%", range=[min(60, m["value"].min() - 5), 102])
    return base_layout(fig, title, "% de crianças de 12 a 23 meses vacinadas — OMS/UNICEF via Banco Mundial (fonte secundária)")


def child_mortality_chart(mortality: pd.DataFrame, uf_code: str = "1") -> go.Figure:
    if not _ok(mortality):
        return empty_figure("Mortalidade na infância indisponível.")
    nat = mortality[mortality["geo_code"] == "1"].sort_values("date")
    if nat.empty:
        return empty_figure("Mortalidade na infância indisponível.")
    last, first = nat.iloc[-1], nat.iloc[0]
    fig = go.Figure()
    if uf_code != "1":
        own = mortality[mortality["geo_code"] == uf_code].sort_values("date")
        if own.empty:
            return empty_figure("Sem dados para o estado escolhido.")
        o = own.iloc[-1]
        title = (f"Em {o['geo_name']} morrem {br(o['value'], 1)} crianças menores de 5 anos a cada mil nascidas vivas "
                 f"({o['date'].year}), contra {br(last['value'], 1)} no Brasil")
        fig.add_trace(_line(own["date"], own["value"], o["geo_name"], PALETTE.alert, "%{y:.1f} por mil", width=3))
        fig.add_trace(_line(nat["date"], nat["value"], "Brasil", PALETTE.neutral, "%{y:.1f} por mil", width=2, dash="dash"))
    else:
        drop = (last["value"] / first["value"] - 1) * 100
        title = (f"Mortalidade de crianças menores de 5 anos é de {br(last['value'], 1)} por mil nascidas vivas em {last['date'].year}, "
                 f"{br(abs(drop), 0)}% {'abaixo' if drop < 0 else 'acima'} de {first['date'].year}")
        fig.add_trace(_line(nat["date"], nat["value"], "Brasil", PALETTE.good if drop < 0 else PALETTE.alert, "%{y:.1f} por mil", width=3))
    fig.update_yaxes(rangemode="tozero")
    return base_layout(fig, title, "Óbitos de menores de 5 anos por mil nascidos vivos — estimativas do IBGE (ODS 3.2.1)")


def child_mortality_ranking_chart(mortality: pd.DataFrame, highlight_code: str | None = None) -> go.Figure:
    if not _ok(mortality):
        return empty_figure("Mortalidade na infância indisponível.")
    states = mortality[mortality["geo_code"] != "1"]
    if states.empty:
        return empty_figure("Comparativo por estado indisponível.")
    year = states["date"].max()
    snap = states[states["date"] == year].sort_values("value")
    nat = mortality[(mortality["geo_code"] == "1") & (mortality["date"] == year)]["value"]
    ref = float(nat.iloc[0]) if len(nat) else float(snap["value"].median())
    top, low = snap.iloc[-1], snap.iloc[0]
    colors = [PALETTE.alert if c == highlight_code else PALETTE.vulnerable if v >= ref else "rgba(74,85,104,0.55)"
              for c, v in zip(snap["geo_code"], snap["value"], strict=True)]
    title = (f"{top['geo_name']} tem a maior mortalidade de crianças menores de 5 anos ({br(top['value'], 1)} por mil) "
             f"e {low['geo_name']} a menor ({br(low['value'], 1)})")
    fig = go.Figure(go.Bar(x=snap["value"], y=snap["geo_name"], orientation="h", marker_color=colors,
                           hovertemplate="%{y}: %{x:.1f} por mil<extra></extra>"))
    fig.add_vline(x=ref, line_dash="dash", line_color=PALETTE.institutional, annotation_text=f"Brasil {br(ref, 1)}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid)
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Óbitos por mil nascidos vivos em {year.year} — IBGE", height=720, wrap=52)


def life_expectancy_chart(life: pd.DataFrame) -> go.Figure:
    if not _ok(life):
        return empty_figure("Expectativa de vida indisponível.")
    d = life.sort_values("date")
    d = d[d["date"] >= pd.Timestamp("2000-01-01")]
    last, first = d.iloc[-1], d.iloc[0]
    gain = last["value"] - first["value"]
    title = (f"Brasileiros vivem em média {br(last['value'], 1)} anos ({last['date'].year}), "
             f"{br(abs(gain), 1)} anos {'a mais' if gain >= 0 else 'a menos'} que em {first['date'].year}")
    fig = go.Figure(go.Scatter(x=d["date"], y=d["value"], mode="lines+markers", line=dict(color=PALETTE.good, width=3),
                               hovertemplate="%{y:.1f} anos<extra></extra>"))
    fig.update_yaxes(ticksuffix=" anos", range=[d["value"].min() - 1, d["value"].max() + 1])
    return base_layout(fig, title, "Expectativa de vida ao nascer — ONU/OMS via Banco Mundial (fonte secundária)")


# --------------------------------------------------------------------------- #
# Leitos do SUS (CNES), cobertura vacinal por estado (SI-PNI) e Bolsa Família (famílias)
# --------------------------------------------------------------------------- #
VACCINE_LABEL = {"triplice_viral": "tríplice viral (1ª dose)", "polio": "poliomielite", "penta": "pentavalente"}


def _population(uf: str) -> float:
    from src.ingestion.sinesp_loader import BRAZIL_POPULATION, UF_POPULATION
    return float(BRAZIL_POPULATION if uf == "BR" else UF_POPULATION[uf])


def beds_per_thousand(beds: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta ``existentes_mil`` e ``sus_mil`` (leitos por mil habitantes; população do Censo 2022 em todos os anos)."""
    from src.ingestion.sinesp_loader import UF_POPULATION
    out = beds[beds["uf"].isin(["BR", *UF_POPULATION])].copy()
    pop = out["uf"].map(_population)
    out["existentes_mil"] = out["existentes"] / pop * 1000
    out["sus_mil"] = out["sus"] / pop * 1000
    return out.dropna(subset=["sus_mil"])


def beds_chart(beds: pd.DataFrame, uf: str = "BR") -> go.Figure:
    if beds is None or beds.empty:
        return empty_figure("Dados de leitos indisponíveis.")
    b = beds_per_thousand(beds)
    own = b[b["uf"] == uf].sort_values("date")
    if own.empty:
        return empty_figure(f"Sem leitos para {uf}.")
    last, first = own.iloc[-1], own.iloc[0]
    where = "O SUS tem" if uf == "BR" else f"Em {uf}, o SUS tem"
    chg = last["sus_mil"] - first["sus_mil"]
    title = (f"{where} {br(last['sus_mil'], 2)} leitos hospitalares por mil habitantes ({last['date']:%m/%Y}), "
             f"{br(abs(chg), 2)} {'a mais' if chg >= 0 else 'a menos'} que em {first['date'].year}; no total (com privados) são {br(last['existentes_mil'], 2)}")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=own["date"], y=own["sus_mil"], name="Leitos SUS", mode="lines+markers", line=dict(color=PALETTE.institutional, width=3),
                             hovertemplate="SUS: %{y:.2f} por mil<extra></extra>"))
    fig.add_trace(go.Scatter(x=own["date"], y=own["existentes_mil"], name="Leitos existentes (SUS + privados)", mode="lines+markers",
                             line=dict(color=PALETTE.neutral, width=2, dash="dash"), hovertemplate="Total: %{y:.2f} por mil<extra></extra>"))
    if uf != "BR":
        nat = b[b["uf"] == "BR"].sort_values("date")
        fig.add_trace(go.Scatter(x=nat["date"], y=nat["sus_mil"], name="Brasil (SUS)", mode="lines", line=dict(color=PALETTE.vulnerable, width=1.8, dash="dot"),
                                 hovertemplate="Brasil SUS: %{y:.2f} por mil<extra></extra>"))
    fig.update_yaxes(rangemode="tozero", ticksuffix=" /mil")
    return base_layout(fig, title, "Leitos hospitalares por mil habitantes (dezembro de cada ano e o mês mais recente) — CNES/Ministério da Saúde; população do Censo 2022 em todos os anos")


def beds_ranking_chart(beds: pd.DataFrame, highlight: str | None = None) -> go.Figure:
    if beds is None or beds.empty:
        return empty_figure("Dados de leitos indisponíveis.")
    b = beds_per_thousand(beds)
    week = b["date"].max()
    snap = b[(b["date"] == week) & (b["uf"] != "BR")].sort_values("sus_mil")
    if snap.empty:
        return empty_figure("Comparativo por estado indisponível.")
    nat = b[(b["date"] == week) & (b["uf"] == "BR")]["sus_mil"]
    ref = float(nat.iloc[0]) if len(nat) else float(snap["sus_mil"].median())
    top, low = snap.iloc[-1], snap.iloc[0]
    colors = [PALETTE.alert if u == highlight else PALETTE.good if v >= ref else PALETTE.vulnerable for u, v in zip(snap["uf"], snap["sus_mil"], strict=True)]
    title = (f"{top['uf']} tem a maior oferta de leitos SUS ({br(top['sus_mil'], 2)} por mil habitantes) e {low['uf']} a menor ({br(low['sus_mil'], 2)})")
    fig = go.Figure(go.Bar(x=snap["sus_mil"], y=snap["uf"], orientation="h", marker_color=colors, hovertemplate="%{y}: %{x:.2f} leitos SUS por mil<extra></extra>"))
    fig.add_vline(x=ref, line_dash="dash", line_color=PALETTE.institutional, annotation_text=f"Brasil {br(ref, 2)}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid)
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Leitos SUS por mil habitantes em {week:%m/%Y} — CNES/Ministério da Saúde", height=720, wrap=52)


def vaccine_uf_chart(pni: pd.DataFrame, vaccine: str = "triplice_viral", highlight: str | None = None) -> go.Figure:
    from src.config import VACCINE_TARGET
    if pni is None or pni.empty:
        return empty_figure("Cobertura vacinal por estado indisponível.")
    v = pni[pni["vacina"] == vaccine]
    if v.empty:
        return empty_figure("Vacina sem dados.")
    year = v["date"].max()
    snap = v[v["date"] == year].sort_values("value")
    label = VACCINE_LABEL.get(vaccine, vaccine)
    ok = int((snap["value"] >= VACCINE_TARGET).sum())
    worst = snap.iloc[0]
    who = (f"Nenhum dos {len(snap)} estados atingiu" if ok == 0 else f"Só {ok} de {len(snap)} estados atingiu" if ok == 1
           else f"Só {ok} de {len(snap)} estados atingiram")
    title = (f"{who} a meta de {br(VACCINE_TARGET, 0)}% de cobertura da vacina {label} em {year.year}; "
             f"o pior é {worst['uf']} ({br(worst['value'], 0)}%)")
    colors = [PALETTE.alert if u == highlight else PALETTE.good if x >= VACCINE_TARGET else PALETTE.vulnerable for u, x in zip(snap["uf"], snap["value"], strict=True)]
    fig = go.Figure(go.Bar(x=snap["value"], y=snap["uf"], orientation="h", marker_color=colors, hovertemplate="%{y}: %{x:.0f}%<extra></extra>"))
    fig.add_vline(x=VACCINE_TARGET, line_dash="dash", line_color=PALETTE.good, annotation_text="Meta 95%", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid, ticksuffix="%")
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Cobertura vacinal por estado em {year.year} (último ano publicado no TabNet/SI-PNI; valores acima de 100% refletem estimativas de população)",
                       height=720, wrap=52)


def vaccine_uf_series_chart(pni: pd.DataFrame, vaccine: str = "triplice_viral", uf: str = "SP") -> go.Figure:
    from src.config import VACCINE_TARGET
    if pni is None or pni.empty:
        return empty_figure("Cobertura vacinal por estado indisponível.")
    s = pni[(pni["vacina"] == vaccine) & (pni["uf"] == uf)].sort_values("date")
    s = s[s["date"] >= pd.Timestamp("2006-01-01")]
    if s.empty:
        return empty_figure("Sem dados para o estado escolhido.")
    last, peak = s.iloc[-1], s.loc[s["value"].idxmax()]
    label = VACCINE_LABEL.get(vaccine, vaccine)
    rel = "abaixo" if last["value"] < VACCINE_TARGET else "acima"
    title = (f"Cobertura da vacina {label} em {uf} está em {br(last['value'], 0)}% em {last['date'].year}, {rel} da meta de {br(VACCINE_TARGET, 0)}%; "
             f"o pico foi {br(peak['value'], 0)}% em {peak['date'].year}")
    fig = go.Figure()
    fig.add_hline(y=VACCINE_TARGET, line_dash="dash", line_color=PALETTE.good, annotation_text="Meta 95%", annotation_position="bottom right")
    fig.add_trace(go.Scatter(x=s["date"], y=s["value"], mode="lines+markers", line=dict(color=PALETTE.institutional, width=3), name=uf,
                             hovertemplate="%{y:.0f}%<extra></extra>"))
    fig.update_yaxes(ticksuffix="%", rangemode="tozero")
    return base_layout(fig, title, "Cobertura vacinal anual — SI-PNI/Ministério da Saúde via TabNet (dados até 2022)")


def pbf_chart(families: pd.DataFrame, value_dec: pd.DataFrame | None = None) -> go.Figure:
    if families is None or families.empty:
        return empty_figure("Número de famílias do Bolsa Família indisponível.")
    f = families[families["uf"] == "BR"].sort_values("date")
    f = f[f["date"] >= pd.Timestamp("2010-01-01")]
    if f.empty:
        return empty_figure("Número de famílias do Bolsa Família indisponível.")
    last = f.iloc[-1]
    prev = f.iloc[-2] if len(f) > 1 else None
    chg = f", {br(abs((last['value'] / prev['value'] - 1) * 100), 1)}% {'a mais' if last['value'] >= prev['value'] else 'a menos'} que em dez/{prev['date'].year}" if prev is not None else ""
    avg_txt = ""
    avg = None
    if value_dec is not None and not value_dec.empty:
        v = value_dec[value_dec["uf"] == "BR"].sort_values("date")
        avg = f.merge(v, on=["date", "uf"], suffixes=("_fam", "_val"))
        avg["value"] = avg["value_val"] * 1000 / avg["value_fam"]  # valor em R$ mil -> R$ por benefício
        if not avg.empty:
            avg_txt = f"; benefício médio de {br_money(avg.iloc[-1]['value'])} em dez/{avg.iloc[-1]['date'].year}"
    title = f"Bolsa Família chega a {br(last['value'] / 1e6, 1)} milhões de famílias em dez/{last['date'].year}{chg}{avg_txt}"
    fig = go.Figure()
    fig.add_trace(go.Bar(x=f["date"], y=f["value"] / 1e6, name="Famílias beneficiadas (milhões)", marker_color="rgba(43,108,176,0.7)",
                         hovertemplate="%{y:.1f} milhões de famílias<extra></extra>"))
    if avg is not None and not avg.empty:
        fig.add_trace(go.Scatter(x=avg["date"], y=avg["value"], name="Benefício médio (R$)", mode="lines+markers", yaxis="y2",
                                 line=dict(color=PALETTE.vulnerable, width=3), hovertemplate="R$ %{y:.0f}<extra></extra>"))
    fig.update_layout(yaxis=dict(title="Famílias (milhões)", rangemode="tozero"),
                      yaxis2=dict(title="Benefício médio", overlaying="y", side="right", showgrid=False, tickprefix="R$ ", rangemode="tozero"))
    return base_layout(fig, title, "Número de benefícios pagos em dezembro de cada ano — Ministério do Desenvolvimento Social via Ipeadata")


def pbf_uf_chart(families: pd.DataFrame, highlight: str | None = None) -> go.Figure:
    if families is None or families.empty:
        return empty_figure("Bolsa Família por estado indisponível.")
    states = families[families["uf"] != "BR"]
    if states.empty:
        return empty_figure("Bolsa Família por estado indisponível.")
    year = states["date"].max()
    snap = states[states["date"] == year].copy()
    snap["por_100"] = [fam / _population(u) * 100 for u, fam in zip(snap["uf"], snap["value"], strict=True)]
    snap = snap.sort_values("por_100")
    nat = families[(families["uf"] == "BR") & (families["date"] == year)]["value"]
    ref = float(nat.iloc[0]) / _population("BR") * 100 if len(nat) else float(snap["por_100"].median())
    top, low = snap.iloc[-1], snap.iloc[0]
    colors = [PALETTE.alert if u == highlight else PALETTE.vulnerable if v >= ref else "rgba(74,85,104,0.55)" for u, v in zip(snap["uf"], snap["por_100"], strict=True)]
    title = (f"{top['uf']} tem a maior proporção de famílias no Bolsa Família ({br(top['por_100'], 1)} para cada 100 habitantes) "
             f"e {low['uf']} a menor ({br(low['por_100'], 1)})")
    fig = go.Figure(go.Bar(x=snap["por_100"], y=snap["uf"], orientation="h", marker_color=colors,
                           hovertemplate="%{y}: %{x:.1f} famílias por 100 habitantes<extra></extra>"))
    fig.add_vline(x=ref, line_dash="dash", line_color=PALETTE.institutional, annotation_text=f"Brasil {br(ref, 1)}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid)
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Benefícios do Bolsa Família em dez/{year.year} por 100 habitantes (população do Censo 2022)", height=720, wrap=52)


# --------------------------------------------------------------------------- #
# Bolsa Família mensal por estado (ETL do Portal da Transparência)
# --------------------------------------------------------------------------- #
def _pbf_monthly(pbf: pd.DataFrame) -> pd.DataFrame:
    out = pbf.copy()
    out["medio"] = out["valor"] / out["familias"]
    return out.sort_values(["uf", "date"])


def pbf_monthly_chart(pbf: pd.DataFrame, uf: str = "BR") -> go.Figure:
    if pbf is None or pbf.empty:
        return empty_figure("Bolsa Família mensal indisponível.")
    m = _pbf_monthly(pbf)
    own = m[m["uf"] == uf]
    if own.empty:
        return empty_figure(f"Sem dados do Bolsa Família para {uf}.")
    last = own.iloc[-1]
    ago = value_days_ago(own.rename(columns={"familias": "value"})[["date", "value"]], 365, tolerance_days=20)
    chg = f" ({br_pct((last['familias'] / ago - 1) * 100, 1, signed=True)} em 12 meses)" if ago else ""
    if uf == "BR":
        title = (f"Bolsa Família paga a {br(last['familias'] / 1e6, 1)} milhões de famílias em {last['date']:%m/%Y}{chg}: "
                 f"{br_money(last['medio'])} por família")
    else:
        nat = m[(m["uf"] == "BR") & (m["date"] == last["date"])]["medio"]
        vs = f", contra {br_money(nat.iloc[0])} no Brasil" if len(nat) else ""
        title = f"Em {uf}, {br(last['familias'], 0)} famílias recebem o Bolsa Família ({last['date']:%m/%Y}), com {br_money(last['medio'])} por família{vs}"
    fig = go.Figure()
    fig.add_trace(go.Bar(x=own["date"], y=own["familias"] / 1e6, name="Famílias (milhões)", marker_color="rgba(43,108,176,0.7)",
                         hovertemplate="%{y:.2f} milhões de famílias<extra></extra>"))
    fig.add_trace(go.Scatter(x=own["date"], y=own["medio"], name="Valor médio por família (R$)", mode="lines+markers", yaxis="y2",
                             line=dict(color=PALETTE.vulnerable, width=3), hovertemplate="R$ %{y:.0f} por família<extra></extra>"))
    fig.update_layout(yaxis=dict(title="Famílias (milhões)", rangemode="tozero"),
                      yaxis2=dict(title="Valor médio", overlaying="y", side="right", showgrid=False, tickprefix="R$ ", rangemode="tozero"))
    return base_layout(fig, title, "Famílias = favorecidos distintos com pagamento no mês; valor médio = total pago ÷ famílias (inclui parcelas retroativas) — Portal da Transparência/CGU")


def pbf_avg_uf_chart(pbf: pd.DataFrame, highlight: str | None = None) -> go.Figure:
    if pbf is None or pbf.empty:
        return empty_figure("Bolsa Família mensal indisponível.")
    m = _pbf_monthly(pbf)
    last = m["date"].max()
    snap = m[(m["date"] == last) & (m["uf"] != "BR")].sort_values("medio")
    if snap.empty:
        return empty_figure("Comparativo por estado indisponível.")
    nat = m[(m["date"] == last) & (m["uf"] == "BR")]["medio"]
    ref = float(nat.iloc[0]) if len(nat) else float(snap["medio"].median())
    top, low = snap.iloc[-1], snap.iloc[0]
    colors = [PALETTE.alert if u == highlight else PALETTE.vulnerable if v >= ref else "rgba(74,85,104,0.55)" for u, v in zip(snap["uf"], snap["medio"], strict=True)]
    title = (f"Benefício médio do Bolsa Família vai de {br_money(low['medio'])} ({low['uf']}) a {br_money(top['medio'])} ({top['uf']}) por família em {last:%m/%Y}")
    fig = go.Figure(go.Bar(x=snap["medio"], y=snap["uf"], orientation="h", marker_color=colors, hovertemplate="%{y}: R$ %{x:.0f} por família<extra></extra>"))
    fig.add_vline(x=ref, line_dash="dash", line_color=PALETTE.institutional, annotation_text=f"Brasil {br_money(ref)}", annotation_position="top")
    fig.update_layout(hovermode="closest", yaxis=dict(type="category"))
    fig.update_xaxes(showgrid=True, gridcolor=PALETTE.grid, tickprefix="R$ ")
    fig.update_yaxes(showgrid=False)
    return base_layout(fig, title, f"Valor médio pago por família em {last:%m/%Y}, por estado — Portal da Transparência/CGU", height=720, wrap=52)
