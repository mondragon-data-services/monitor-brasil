"""Monitor Brasil — Painel Cidadão de Indicadores Socioeconômicos (entrypoint Streamlit)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
import streamlit as st

from src.config import (
    APP_TITLE,
    EXPLAINERS,
    PALETTE,
    PERIOD_OPTIONS,
    SGS_SERIES,
    SIDRA_TABLES,
)
from src.agent.ui import render_agent_tab
from src.ingestion.bcb_client import get_bcb_series
from src.ingestion.cache import FetchResult
from src.ingestion.http_client import build_session
from src.ingestion.ibge_client import get_ibge_series
from src.ingestion.sinesp_download import sync_sinesp_vde
from src.ingestion.sinesp_loader import get_security_education, process_upload
from src.transformation.metrics import (
    accumulated_12m,
    build_real_interest,
    filter_period,
    latest_with_delta,
    period_start,
)
from src.visualization import charts
from src.visualization.theme import br, br_money, br_pct, br_pp

st.set_page_config(page_title="Monitor Brasil", page_icon="🇧🇷", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    f"""
    <style>
      .block-container {{ padding-top: 1.6rem; max-width: 1280px; }}
      div[data-testid="stMetric"] {{ background:#F7FAFC; border:1px solid {PALETTE.grid}; border-radius:12px; padding:12px 14px; }}
      div[data-testid="stMetricValue"] {{ font-size: 1.7rem; }}
      .mb-badge {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:.78rem; font-weight:600; margin-right:6px; }}
      .mb-ok {{ background:#E6F6F0; color:#0F6B4F; }} .mb-warn {{ background:#FEF3C7; color:#92400E; }}
      .mb-bad {{ background:#FDE8E4; color:#9B2C1A; }}
      .mb-status {{ display:inline-block; padding:6px 14px; border-radius:8px; font-weight:700; color:#fff; }}
      .mb-sub {{ color:{PALETTE.muted}; font-size:.92rem; margin-top:-.4rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Camada 1 do cache (memória, 24h). A camada 2 (Parquet) vive em src/ingestion/cache.py
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=86400, show_spinner=False)
def load_security() -> FetchResult:
    return get_security_education()


@st.cache_data(ttl=86400, show_spinner="Consultando Banco Central e IBGE…")
def load_everything() -> dict[str, FetchResult]:
    """Baixa todas as séries em paralelo: uma API lenta do governo não trava as demais."""
    jobs = {**{k: (get_bcb_series, k) for k in SGS_SERIES}, **{k: (get_ibge_series, k) for k in SIDRA_TABLES}}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {k: pool.submit(fn, key, None, build_session()) for k, (fn, key) in jobs.items()}
        out: dict[str, FetchResult] = {k: f.result() for k, f in futures.items()}
    out["seguranca_educacao"] = load_security()
    return out


def refresh_all() -> None:
    st.cache_data.clear()
    st.rerun()


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def status_badge(label: str, results: list[FetchResult]) -> str:
    modes = {r.mode for r in results}
    synced = [r.synced_at for r in results if r.synced_at]
    when = max(synced).strftime("%d/%m %H:%M") if synced else ""
    if modes == {"live"}:
        return f"<span class='mb-badge mb-ok'>🟢 {label} · ao vivo {when}</span>"
    if "missing" in modes:
        return f"<span class='mb-badge mb-bad'>🔴 {label} · indisponível</span>"
    if modes == {"fallback"}:
        return f"<span class='mb-badge mb-warn'>🟡 {label} · consolidado de referência</span>"
    return f"<span class='mb-badge mb-warn'>🟡 {label} · cache local {when}</span>"


def sinesp_badge(res: FetchResult) -> str:
    """Sinesp oficial baixado = verde; só o consolidado de referência = amarelo."""
    if res.mode == "cache" and res.synced_at:
        return f"<span class='mb-badge mb-ok'>🟢 Sinesp VDE oficial · baixado {res.synced_at:%d/%m/%Y}</span>"
    return status_badge("Sinesp/INEP", [res])


with st.sidebar:
    st.markdown("### 🇧🇷 Monitor Brasil")
    st.caption("Painel público, didático e auditável.")
    period_label = st.selectbox("Período de análise", list(PERIOD_OPTIONS), index=2)
    show_events = st.toggle("Mostrar eventos históricos", value=True,
                            help="Pandemia, ciclo de juros, transição de mandato e eleições.")
    if st.button("🔄 Forçar Atualização nas APIs do Governo", width="stretch"):
        refresh_all()

    data = load_everything()
    st.markdown("#### Status das fontes")
    st.markdown(
        status_badge("Banco Central", [data[k] for k in SGS_SERIES]) + "<br>"
        + status_badge("IBGE", [data[k] for k in SIDRA_TABLES]) + "<br>"
        + sinesp_badge(data["seguranca_educacao"]),
        unsafe_allow_html=True,
    )
    st.markdown("#### Atualizar segurança pública")
    if st.button("⬇️ Baixar Sinesp VDE oficial (gov.br)", width="stretch",
                 help="Baixa as planilhas anuais do Ministério da Justiça (~25 MB por ano; só o que falta). "
                      "Na primeira vez leva alguns minutos."):
        with st.status("Baixando Sinesp VDE…", expanded=True) as box:
            bar = st.progress(0.0)

            def _progress(msg: str, frac: float) -> None:
                bar.progress(min(frac, 1.0), text=msg)

            report = sync_sinesp_vde(progress=_progress)
            failed = [y for y in report.years if y.status in ("erro",)]
            box.update(label=f"Sinesp VDE: {report.combined_rows:,} linhas".replace(",", ".")
                       + (f" · {len(failed)} ano(s) com erro" if failed else " · ok"),
                       state="error" if failed and not report.ok else "complete")
        st.cache_data.clear()
        st.rerun()
    st.caption("Ou envie a planilha (.xlsx, .csv, .parquet) com `uf`, `evento`, `data_referencia` e `total_vitima`.")
    upload = st.file_uploader("Planilha Sinesp VDE", type=["xlsx", "csv", "parquet"], label_visibility="collapsed")
    if upload is not None and st.session_state.get("last_upload") != upload.file_id:
        try:
            frame = process_upload(upload, upload.name)
            st.session_state["last_upload"] = upload.file_id
            st.cache_data.clear()
            st.toast(f"Sinesp atualizado: {len(frame):,} linhas padronizadas.".replace(",", "."), icon="✅")
            st.rerun()
        except Exception as exc:
            st.error(f"Não foi possível ler a planilha: {exc}")
    st.divider()
    st.caption("Fontes: Banco Central (SGS), IBGE (SIDRA), Ministério da Justiça (Sinesp VDE), INEP.")

option = PERIOD_OPTIONS[period_label]
start = period_start(option)

# Aviso discreto quando algo veio do cache em disco
stale = [(k, r) for k, r in data.items() if r.mode == "cache" and r.synced_at and k != "seguranca_educacao"]
if stale and "toast_shown" not in st.session_state:
    last = max(r.synced_at for _, r in stale)
    st.toast(f"API do governo indisponível para {len(stale)} fonte(s). Usando cache local de {last:%d/%m/%Y %H:%M}.", icon="⚠️")
    st.session_state["toast_shown"] = True


def df(key: str) -> pd.DataFrame:
    return data[key].df


# --------------------------------------------------------------------------- #
# Dados derivados
# --------------------------------------------------------------------------- #
ipca_m, food_m = df("ipca_mensal"), df("ipca_alimentacao")
ipca_12 = accumulated_12m(ipca_m).dropna() if not ipca_m.empty else pd.DataFrame()
food_12 = accumulated_12m(food_m).dropna() if not food_m.empty else pd.DataFrame()
real_rate = build_real_interest(df("selic_meta"), ipca_m) if not df("selic_meta").empty and not ipca_m.empty else pd.DataFrame()
sec = df("seguranca_educacao")


# --------------------------------------------------------------------------- #
# Componentes de UI
# --------------------------------------------------------------------------- #
def explainer(key: str) -> None:
    ex = EXPLAINERS[key]
    with st.container(border=True):
        st.markdown("**💡 Para entender em 30 segundos**")
        st.markdown(f"**O que é este indicador?** {ex.what}")
        st.markdown(f"**Por que isso importa no seu dia a dia?** {ex.why}")
        st.markdown(f"**Fonte auditável:** [{ex.source_label}]({ex.source_url})")


def data_section(name: str, frames: dict[str, pd.DataFrame]) -> None:
    with st.expander("📥 Inspecionar Tabela de Dados Brutos e Baixar CSV"):
        parts = []
        for label, frame in frames.items():
            if frame is None or frame.empty:
                continue
            tmp = frame.copy()
            tmp.insert(0, "serie", label)
            parts.append(tmp)
        if not parts:
            st.info("Sem dados para exibir.")
            return
        table = pd.concat(parts, ignore_index=True)
        st.dataframe(table, width="stretch", hide_index=True, height=300)
        csv = table.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
        st.download_button("⬇️ Baixar CSV", csv, file_name=f"monitor_brasil_{name}_{datetime.now():%Y%m%d}.csv",
                           mime="text/csv", key=f"dl_{name}")
        st.caption("CSV com separador `;` e vírgula decimal (abre direto no Excel em português).")


def plot(fig, key: str) -> None:
    st.plotly_chart(fig, width="stretch", key=key, config={"displaylogo": False, "locale": "pt-BR"})


def quality_notice(frame: pd.DataFrame, pattern: str) -> None:
    """Aviso de qualidade só sobre os indicadores mostrados na aba (``pattern`` = regex de indicadores)."""
    shown = frame[frame["indicador"].str.contains(pattern)] if not frame.empty else frame
    if shown.empty:
        return
    if (shown["qualidade"] == "aproximado").any():
        st.warning("**Atenção:** parte destes números é um **consolidado de referência aproximado** (Anuário Brasileiro de "
                   "Segurança Pública, INEP e IBGE), usado quando não há planilha oficial. Para dados auditáveis, use "
                   "**⬇️ Baixar Sinesp VDE oficial** na barra lateral.", icon="⚠️")
    else:
        years = shown["ano"]
        st.success(f"Dados **oficiais do Sinesp VDE** (Ministério da Justiça), {int(years.min())}–{int(years.max())}. "
                   "Apenas anos completos; taxas por 100 mil usam a população do Censo 2022 em todos os anos "
                   "(pode diferir ~1–4% do FBSP). Em 2015–2017 a cobertura dos estados pode estar incompleta, "
                   "o que afeta sobretudo os registros de estupro.", icon="✅")


# --------------------------------------------------------------------------- #
# Header + Termômetro do Brasil Hoje
# --------------------------------------------------------------------------- #
st.title("🇧🇷 Monitor Brasil")
st.markdown(f"<p class='mb-sub'>{APP_TITLE.split('—', 1)[1].strip()} · inflação, emprego, segurança e educação em linguagem simples.</p>",
            unsafe_allow_html=True)
st.subheader("🌡️ Termômetro do Brasil Hoje")
k1, k2, k3, k4, k5 = st.columns(5)

with k1:
    if not ipca_12.empty:
        d = latest_with_delta(ipca_12, 12)
        food_last = food_12.iloc[-1]["value"] if not food_12.empty else float("nan")
        st.metric("IPCA acumulado 12m", br_pct(d.latest, 2), br_pp(d.change) if d.change == d.change else None,
                  delta_color="inverse", help="Variação em p.p. contra 12 meses atrás.")
        st.caption(f"🛒 Alimentos: **{br_pct(food_last, 2)}**")
    else:
        st.metric("IPCA acumulado 12m", "—")
with k2:
    debt = df("divida_bruta")
    if not debt.empty:
        d = latest_with_delta(debt, 12)
        st.metric("Dívida bruta (% do PIB)", br_pct(d.latest), br_pp(d.change) if d.change == d.change else None,
                  delta_color="inverse")
        selic = df("selic_meta")
        st.caption(f"🏦 Selic: **{br_pct(selic.iloc[-1]['value'], 2)}** a.a." if not selic.empty else "")
    else:
        st.metric("Dívida bruta (% do PIB)", "—")
with k3:
    un = df("desocupacao")
    if not un.empty:
        d = latest_with_delta(un, 12)
        st.metric("Desemprego (PNAD)", br_pct(d.latest), br_pp(d.change) if d.change == d.change else None,
                  delta_color="inverse")
        sub = df("subutilizacao")
        st.caption(f"📉 Subutilização: **{br_pct(sub.iloc[-1]['value'])}**" if not sub.empty else "")
    else:
        st.metric("Desemprego (PNAD)", "—")
with k4:
    inc = df("rendimento_real")
    if not inc.empty:
        d = latest_with_delta(inc, 12)
        st.metric("Rendimento médio real", br_money(d.latest),
                  f"{br(d.change, 2, signed=True)} R$" if d.change == d.change else None, delta_color="normal")
        st.caption("Habitual, todos os trabalhos")
    else:
        st.metric("Rendimento médio real", "—")
with k5:
    mvi = sec[(sec["indicador"] == "mvi_taxa") & (sec["uf"] == "BR")].sort_values("ano") if not sec.empty else pd.DataFrame()
    if not mvi.empty:
        last_y = mvi.iloc[-1]
        prev = mvi.iloc[-2]["valor"] if len(mvi) > 1 else float("nan")
        st.metric(f"Homicídios (MVI/100 mil) · {int(last_y['ano'])}", br(last_y["valor"]),
                  br(last_y["valor"] - prev, 1, signed=True) if prev == prev else None, delta_color="inverse")
        fem = sec[(sec["indicador"] == "feminicidio_total") & (sec["uf"] == "BR")].sort_values("ano")
        st.caption(f"♀ Feminicídios: **{br(fem.iloc[-1]['valor'], 0)}**" if not fem.empty else "")
    else:
        st.metric("Homicídios (MVI/100 mil)", "—")

st.divider()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["💰 Inflação, Juros e Risco Fiscal", "👷 Emprego, Renda e Recessão",
                                        "🚨 Segurança e Vulneráveis", "🎓 Educação e Futuro", "🤖 Agente Monitora Brasil"])

# --------------------------- Aba 1 ------------------------------------------ #
with tab1:
    plot(charts.inflation_chart(ipca_m, food_m, start, show_events), "inflacao")
    explainer("inflacao")
    plot(charts.debt_chart(df("divida_bruta"), real_rate, start, show_events), "divida")
    explainer("divida_juros")
    data_section("inflacao_fiscal", {
        "IPCA 12m (%)": filter_period(ipca_12, option), "Alimentação 12m (%)": filter_period(food_12, option),
        "INPC mensal (%)": filter_period(df("inpc_mensal"), option),
        "Dívida bruta (% PIB)": filter_period(df("divida_bruta"), option),
        "Dívida líquida (% PIB)": filter_period(df("divida_liquida"), option),
        "Selic/juro real (% a.a.)": filter_period(real_rate, option),
    })

# --------------------------- Aba 2 ------------------------------------------ #
with tab2:
    plot(charts.labor_chart(filter_period(df("desocupacao"), option), filter_period(df("subutilizacao"), option), show_events), "trabalho")
    explainer("desemprego")
    plot(charts.income_debt_chart(filter_period(df("rendimento_real"), option), df("endividamento_familias"), show_events), "renda")
    explainer("renda_endividamento")
    fig_rec, rec_status = charts.recession_chart(filter_period(df("pib_4tri"), None), df("pib_trimestral"), df("ibcbr"))
    color = {"Alerta de Recessão Técnica": PALETTE.alert, "Desaceleração": PALETTE.vulnerable,
             "Expansão": PALETTE.good}.get(rec_status, PALETTE.neutral)
    st.markdown(f"**Termômetro de recessão:** <span class='mb-status' style='background:{color}'>{rec_status}</span>",
                unsafe_allow_html=True)
    plot(fig_rec, "recessao")
    explainer("recessao")
    data_section("emprego_renda", {
        "Desocupação (%)": filter_period(df("desocupacao"), option), "Subutilização (%)": filter_period(df("subutilizacao"), option),
        "Rendimento real (R$)": filter_period(df("rendimento_real"), option),
        "Endividamento famílias (%)": filter_period(df("endividamento_familias"), option),
        "PIB 4 trimestres (%)": df("pib_4tri"), "PIB trimestral dessaz. (%)": df("pib_trimestral"),
        "IBC-Br (índice)": filter_period(df("ibcbr"), option),
    })

# --------------------------- Aba 3 ------------------------------------------ #
with tab3:
    quality_notice(sec, '^(mvi_taxa|feminicidio_total|estupro_vulneravel_total)$')
    ufs = sorted(sec.loc[(sec["indicador"] == "mvi_taxa") & (sec["uf"] != "BR"), "uf"].unique()) if not sec.empty else []
    uf = st.selectbox("Unidade da Federação", ["BR"] + ufs or ["BR"], format_func=lambda x: "Brasil consolidado" if x == "BR" else x)
    plot(charts.mvi_chart(sec, uf), "mvi")
    explainer("mvi")
    c1, c2 = st.columns([1, 1])
    with c1:
        plot(charts.vulnerable_chart(sec), "vulneraveis")
    with c2:
        plot(charts.uf_ranking_chart(sec, "mvi_taxa", None if uf == "BR" else uf), "ranking_uf")
    explainer("vulneraveis")
    data_section("seguranca", {"Segurança pública e educação (consolidado)": sec[sec["indicador"].str.contains("mvi|femin|estupro")]
                               if not sec.empty else sec})

# --------------------------- Aba 4 ------------------------------------------ #
with tab4:
    quality_notice(sec, 'ideb|abandono|nem_nem')
    plot(charts.ideb_chart(sec), "ideb")
    explainer("ideb")
    plot(charts.dropout_chart(sec), "evasao")
    explainer("evasao")
    data_section("educacao", {"Educação (consolidado)": sec[sec["indicador"].str.contains("ideb|abandono|nem_nem")]
                              if not sec.empty else sec})

# --------------------------- Aba 5 ------------------------------------------ #
with tab5:
    render_agent_tab(data)

st.divider()
st.caption("Monitor Brasil é um projeto de dados abertos. Os números vêm de APIs públicas do governo e podem sofrer "
           "revisões. · Código e dicionário de dados no README.")
