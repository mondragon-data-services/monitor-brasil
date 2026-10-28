"""Monitor Brasil — Painel Cidadão de Indicadores Socioeconômicos (entrypoint Streamlit)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
import streamlit as st

from src.config import (
    APP_TITLE,
    IPCA_ITEMS,
    DENGUE_INCOMPLETE_WEEKS,
    EXPLAINERS,
    FOCUS_INDICATORS,
    PALETTE,
    PERIOD_OPTIONS,
    SGS_SERIES,
    SIDRA_TABLES,
)
from src.agent.ui import open_agent, render_agent_tab, sidebar_agent_button
from src.ingestion import extra_jobs
from src.ingestion.anp_client import get_anp_gasolina
from src.ingestion.bcb_client import get_bcb_series
from src.ingestion.cache import FetchResult
from src.ingestion.http_client import build_session
from src.ingestion.focus_client import get_focus
from src.ingestion.ibge_client import get_ibge_series
from src.ingestion.ipca_itens_client import get_ipca_items
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
        futures["anp_gasolina"] = pool.submit(get_anp_gasolina, None, build_session())
        futures["ipca_itens"] = pool.submit(get_ipca_items, None, build_session())
        futures["focus"] = pool.submit(get_focus, None, build_session())
        for key, job in extra_jobs().items():  # pobreza, trabalho, saúde, dengue
            futures[key] = pool.submit(job, build_session())
        out: dict[str, FetchResult] = {k: f.result() for k, f in futures.items()}
    out["seguranca_educacao"] = load_security()
    return out


def refresh_all() -> None:
    try:
        get_anp_gasolina(force=True)  # o cache em disco da ANP tem validade; "forçar" precisa ignorá-la
    except Exception:
        pass
    st.cache_data.clear()
    st.rerun()


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
def _effective_mode(r: FetchResult) -> str:
    """Cache dentro da validade (fonte que atualiza 1x por semana) conta como ao vivo."""
    return "live" if r.mode == "cache" and r.detail.startswith("cache válido") else r.mode


def status_badge(label: str, results: list[FetchResult]) -> str:
    modes = {_effective_mode(r) for r in results}
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
    agent_clicked = sidebar_agent_button()
    period_label = st.selectbox("Período de análise", list(PERIOD_OPTIONS), index=2)
    show_events = st.toggle("Mostrar eventos históricos", value=True,
                            help="Pandemia, ciclo de juros, transição de mandato e eleições.")
    if st.button("🔄 Forçar Atualização nas APIs do Governo", width="stretch"):
        refresh_all()

    data = load_everything()
    st.markdown("#### Status das fontes")
    st.markdown(
        status_badge("Banco Central", [*(data[k] for k in SGS_SERIES), data["focus"]]) + "<br>"
        + status_badge("IBGE", [data[k] for k in SIDRA_TABLES]) + "<br>"
        + status_badge("Preços (ANP/IBGE)", [data["anp_gasolina"], data["ipca_itens"]]) + "<br>"
        + status_badge("Social e saúde", [data[k] for k in extra_jobs()]) + "<br>"
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

if agent_clicked:
    open_agent(data)

option = PERIOD_OPTIONS[period_label]
start = period_start(option)

# Aviso discreto quando algo veio do cache em disco
stale = [(k, r) for k, r in data.items()
         if r.mode == "cache" and r.synced_at and k != "seguranca_educacao" and _effective_mode(r) != "live"]
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
st.markdown(f"<p class='mb-sub'>{APP_TITLE.split('—', 1)[1].strip()} · preços, emprego, pobreza, segurança, educação e saúde em linguagem simples.</p>",
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

tab1, tab2, tab3, tab4, tab_prices, tab_social, tab_health, tab5 = st.tabs([
    "💰 Inflação, Juros e Risco Fiscal", "👷 Emprego, Renda e Recessão", "🚨 Segurança e Vulneráveis",
    "🎓 Educação e Futuro", "🛒 Preços do Dia a Dia", "🤝 Pobreza e Desigualdade", "🏥 Saúde", "🤖 Agente Monitora Brasil"])

# --------------------------- Aba 1 ------------------------------------------ #
with tab1:
    plot(charts.inflation_chart(ipca_m, food_m, start, show_events), "inflacao")
    explainer("inflacao")
    plot(charts.debt_chart(df("divida_bruta"), real_rate, start, show_events), "divida")
    explainer("divida_juros")
    st.markdown("### 💵 Dólar e o que o mercado espera")
    snap = charts.focus_snapshot(df("focus"))
    if not snap.empty:
        cols = st.columns(len(FOCUS_INDICATORS))
        years = sorted(snap["ano"].unique())[:2]
        for col, (ind, label) in zip(cols, FOCUS_INDICATORS.items(), strict=True):
            now = snap[(snap["indicador"] == ind) & (snap["ano"] == years[0])]["mediana"]
            nxt = snap[(snap["indicador"] == ind) & (snap["ano"] == years[1])]["mediana"] if len(years) > 1 else []
            if len(now):
                fmt = (lambda v: f"R$ {br(v, 2)}") if ind == "Câmbio" else (lambda v: f"{br(v, 2)}%")
                col.metric(f"{label.split(' (')[0]} esperado — {int(years[0])}", fmt(now.iloc[0]))
                if len(nxt):
                    col.caption(f"Para {int(years[1])}: **{fmt(nxt.iloc[0])}**")
        st.caption(f"Mediana das projeções do Boletim Focus em {snap['date'].max():%d/%m/%Y}.")
    plot(charts.focus_ipca_chart(df("focus"), start), "focus_ipca")
    plot(charts.dollar_chart(df("cambio_dolar"), df("focus"), start, show_events), "dolar")
    explainer("cambio")
    st.markdown("### 🧾 Resultado fiscal: quanto o governo gasta a mais e quanto vai para juros")
    plot(charts.fiscal_result_chart(df("resultado_primario"), df("resultado_nominal"), start, show_events), "fiscal_resultado")
    explainer("fiscal_resultado")
    data_section("inflacao_fiscal", {
        "Dólar (R$)": filter_period(df("cambio_dolar"), option),
        "Resultado primário 12m (% PIB, déficit +)": filter_period(df("resultado_primario"), option),
        "Resultado nominal 12m (% PIB, déficit +)": filter_period(df("resultado_nominal"), option),
        "Focus — expectativas (mediana)": filter_period(df("focus"), option),
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
    st.markdown("### 💳 Crédito das famílias: inadimplência e juros")
    plot(charts.credit_chart(df("inadimplencia_pf"), df("juros_cartao_rotativo"), df("juros_cheque_especial"), start, show_events), "credito")
    explainer("credito")
    st.markdown("### 💼 Quanto o salário mínimo realmente compra")
    plot(charts.min_wage_chart(df("salario_minimo"), df("ipca_mensal"), start), "salario_minimo")
    explainer("salario_minimo")
    data_section("emprego_renda", {
        "Inadimplência PF (%)": filter_period(df("inadimplencia_pf"), option),
        "Juros do cartão rotativo (% a.a.)": filter_period(df("juros_cartao_rotativo"), option),
        "Juros do cheque especial (% a.a.)": filter_period(df("juros_cheque_especial"), option),
        "Salário mínimo nominal (R$)": filter_period(df("salario_minimo"), option),
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

# --------------------------- Aba Preços do dia a dia ------------------------ #
with tab_prices:
    anp, items = df("anp_gasolina"), df("ipca_itens")
    st.markdown("### ⛽ Gasolina: preço por litro, por estado e no Brasil")
    gas_ufs = sorted(u for u in anp["uf"].unique() if u != "BR") if not anp.empty else []
    gas_uf = st.selectbox("Estado", ["BR"] + gas_ufs, format_func=lambda x: "Brasil" if x == "BR" else x, key="gas_uf")
    plot(charts.gasoline_chart(anp, gas_uf, start, show_events), "gasolina_serie")
    plot(charts.gasoline_ranking_chart(anp, None if gas_uf == "BR" else gas_uf), "gasolina_ranking")
    explainer("gasolina")

    st.markdown("### 🛒 Inflação do prato e do tanque: item por item")
    st.caption("O IBGE publica a **variação** do preço de cada item (não o preço em reais). Por isso mostramos quanto "
               "cada item subiu — e quanto custa hoje o que custava R$ 100 em dezembro de 2019.")
    plate = ("comida", "combustivel", "referencia")
    plot(charts.items_inflation_chart(items, plate), "itens_12m")
    plot(charts.real_cost_chart(items, plate), "itens_custo")
    options = [k for k, v in IPCA_ITEMS.items() if v.group in ("comida", "combustivel") or k == "alimentacao_domicilio"]
    chosen = st.multiselect("Itens para comparar no tempo", options, default=["tomate", "cafe", "arroz", "carne_acem"],
                            format_func=lambda k: IPCA_ITEMS[k].label, key="itens_sel")
    plot(charts.items_timeline_chart(items, chosen, start, show_events), "itens_tempo")
    st.caption("ℹ️ O IPCA não tem 'carne moída': usamos acém e patinho, os cortes mais comuns para moer. "
               "Feijão: carioca (o mais consumido) e preto. Frango: inteiro e em pedaços.")
    explainer("precos_itens")

    st.markdown("### 🏠 Contas fixas da casa: energia, gás, aluguel, saúde e transporte")
    bills = ("casa", "saude", "transporte")
    plot(charts.items_inflation_chart(items, bills), "contas_12m")
    plot(charts.real_cost_chart(items, bills), "contas_custo")
    bill_opts = [k for k, v in IPCA_ITEMS.items() if v.group in bills]
    bill_sel = st.multiselect("Contas para comparar no tempo", bill_opts, default=["energia", "aluguel", "plano_saude", "gas_botijao"],
                              format_func=lambda k: IPCA_ITEMS[k].label, key="contas_sel")
    plot(charts.items_timeline_chart(items, bill_sel, start, show_events), "contas_tempo")
    explainer("contas_casa")
    from src.transformation.metrics import split_items as _split_items
    acc_long = pd.concat([accumulated_12m(d).dropna().assign(item=k) for k, d in _split_items(items).items()],
                         ignore_index=True) if not items.empty else items
    data_section("precos", {
        "Gasolina comum ANP (R$/litro, semanal)": filter_period(anp, option),
        "IPCA por item — variação mensal (%)": filter_period(items, option),
        "IPCA por item — acumulado 12m (%)": filter_period(acc_long, option) if not items.empty else acc_long,
    })

# --------------------------- Aba Pobreza e Desigualdade --------------------- #
with tab_social:
    pov, ext, gini = df("ipea_pobreza"), df("ipea_pobreza_extrema"), df("soc_gini")
    food, inf, pos = df("soc_inseguranca_alimentar"), df("soc_informalidade"), df("soc_posicao_ocupacao")
    caged, mass, disc, bolsa = df("ipea_caged_saldo"), df("soc_massa_rendimento"), df("soc_desalento"), df("ipea_bolsa_familia")
    k1, k2, k3, k4, k5 = st.columns(5)
    if not pov.empty:
        d = latest_with_delta(pov, 1)
        k1.metric("Pobreza (< US$ 8,30/dia)", br_pct(d.latest, 1), br_pp(d.change) if d.change == d.change else None, delta_color="inverse")
    if not ext.empty:
        d = latest_with_delta(ext, 1)
        k2.metric("Extrema pobreza (< US$ 3/dia)", br_pct(d.latest, 1), br_pp(d.change) if d.change == d.change else None, delta_color="inverse")
    if not gini.empty:
        d = latest_with_delta(gini, 1)
        k3.metric("Desigualdade (Gini)", br(d.latest, 3), br(d.change, 3, signed=True) if d.change == d.change else None, delta_color="inverse")
    if not inf.empty:
        d = latest_with_delta(inf, 12)
        k4.metric("Informalidade", br_pct(d.latest, 1), br_pp(d.change) if d.change == d.change else None, delta_color="inverse")
    if not food.empty:
        grave = food[food["cat_code"] == "109102"].sort_values("date")
        if not grave.empty:
            d = latest_with_delta(grave, 1)
            k5.metric("Fome (insegurança grave)", br_pct(d.latest, 1), br_pp(d.change) if d.change == d.change else None, delta_color="inverse")
    st.markdown("### 🧺 Pobreza, desigualdade e fome")
    plot(charts.poverty_chart(pov, ext), "pobreza")
    plot(charts.gini_chart(gini), "gini")
    explainer("pobreza")
    plot(charts.food_insecurity_chart(food), "fome")
    explainer("fome")
    st.markdown("### 💼 Qualidade do emprego: carteira, informalidade e vagas")
    plot(charts.informality_chart(inf, start), "informalidade")
    plot(charts.job_quality_chart(pos, start), "carteira")
    plot(charts.caged_chart(caged, start), "caged")
    explainer("informalidade")
    plot(charts.income_mass_chart(mass, disc, start), "massa_renda")
    st.markdown("### 🤲 Bolsa Família")
    plot(charts.bolsa_familia_chart(bolsa, start), "bolsa_familia")
    pbf_fam, pbf_val = df("ipea_pbf_familias"), df("ipea_pbf_valor_dez")
    plot(charts.pbf_chart(pbf_fam, pbf_val), "pbf_familias")
    plot(charts.pbf_uf_chart(pbf_fam), "pbf_uf")
    pbf_m = df("pbf_mensal")
    if pbf_m.empty:
        st.info("A série mensal de famílias e do valor médio por estado ainda não foi processada. "
                "Rode `python scripts/sync_data.py --bolsa-familia` (baixa ~330 MB por mês do Portal da Transparência).", icon="ℹ️")
    else:
        pbf_ufs = sorted(u for u in pbf_m["uf"].unique() if u != "BR")
        pbf_uf = st.selectbox("Estado (Bolsa Família mensal)", ["BR"] + pbf_ufs, format_func=lambda x: "Brasil" if x == "BR" else x, key="pbfm_uf")
        plot(charts.pbf_monthly_chart(pbf_m, pbf_uf), "pbf_mensal")
        plot(charts.pbf_avg_uf_chart(pbf_m, None if pbf_uf == "BR" else pbf_uf), "pbf_valor_medio_uf")
    explainer("bolsa_familia")
    st.caption("Séries anuais: famílias e benefício médio de dezembro (Ipeadata). Série mensal por estado: ETL do Portal da Transparência "
               "(últimos 24 meses; famílias = favorecidos distintos). Os dois cálculos de valor médio podem diferir um pouco.")
    data_section("pobreza_trabalho", {
        "Pobreza US$ 8,30/dia (%)": pov, "Extrema pobreza US$ 3,00/dia (%)": ext, "Gini": gini,
        "Insegurança alimentar (% domicílios)": food, "Informalidade (%)": filter_period(inf, option),
        "Ocupados por posição (mil pessoas)": filter_period(pos, option), "Saldo do Caged (vagas)": filter_period(caged, option),
        "Massa de rendimento (R$ milhões)": filter_period(mass, option), "Desalento (%)": filter_period(disc, option),
        "Bolsa Família — valor total (R$ mil)": filter_period(bolsa, option),
        "Bolsa Família — benefícios em dezembro (Brasil e UF)": pbf_fam, "Bolsa Família — valor em dezembro (R$ mil)": pbf_val,
        "Bolsa Família mensal por UF (famílias, pagamentos, valor R$)": pbf_m,
    })

# --------------------------- Aba Saúde -------------------------------------- #
with tab_health:
    dengue, vac, vac_dpt = df("dengue"), df("wb_vacina_sarampo"), df("wb_vacina_dpt")
    mort, life = df("soc_mortalidade_infancia"), df("wb_expectativa_vida")
    spend, spend_pub = df("wb_gasto_saude_pib"), df("wb_gasto_publico_saude_pib")
    from src.transformation.metrics import dengue_cumulative, dengue_last_complete_week
    h1, h2, h3, h4, h5 = st.columns(5)
    done = dengue_last_complete_week(dengue, DENGUE_INCOMPLETE_WEEKS)
    if done:
        cur, prev = dengue_cumulative(dengue, done[0], done[1]), dengue_cumulative(dengue, done[0] - 1, done[1])
        pct = (cur["casos"] / prev["casos"] - 1) * 100 if prev["casos"] else None
        h1.metric(f"Dengue em {done[0]} (até sem. {done[1]})", br(cur["casos"], 0), br_pct(pct, 0, signed=True) if pct is not None else None,
                  delta_color="inverse", help="Casos prováveis; variação contra o mesmo período do ano anterior.")
    if not vac.empty:
        d = latest_with_delta(vac, 1)
        h2.metric("Vacina contra sarampo", br_pct(d.latest, 0), br_pp(d.change) if d.change == d.change else None, delta_color="normal",
                  help="Meta do PNI: 95%.")
    if not mort.empty:
        nat = mort[mort["geo_code"] == "1"]
        if not nat.empty:
            d = latest_with_delta(nat, 1)
            h3.metric("Mortes de menores de 5 anos", f"{br(d.latest, 1)} por mil", br(d.change, 1, signed=True) if d.change == d.change else None,
                      delta_color="inverse")
    if not life.empty:
        d = latest_with_delta(life, 1)
        h4.metric("Expectativa de vida", f"{br(d.latest, 1)} anos", f"{br(d.change, 1, signed=True)} ano(s)" if d.change == d.change else None,
                  delta_color="normal")
    if not spend.empty:
        h5.metric("Gasto total em saúde", br_pct(spend.iloc[-1]["value"], 1) + " do PIB")
        if not spend_pub.empty:
            h5.caption(f"Setor público: **{br_pct(spend_pub.iloc[-1]['value'], 1)}** do PIB ({spend_pub.iloc[-1]['date'].year})")
    st.markdown("### 🦟 Dengue")
    dengue_ufs = sorted(dengue["uf"].unique()) if not dengue.empty else []
    d_uf = st.selectbox("Estado", ["BR"] + dengue_ufs, format_func=lambda x: "Brasil" if x == "BR" else x, key="dengue_uf")
    plot(charts.dengue_weekly_chart(dengue, d_uf, DENGUE_INCOMPLETE_WEEKS), "dengue_semanas")
    plot(charts.dengue_uf_chart(dengue, None if d_uf == "BR" else d_uf, DENGUE_INCOMPLETE_WEEKS), "dengue_ufs")
    explainer("dengue")
    st.markdown("### 💉 Vacinação")
    plot(charts.vaccine_chart(vac, vac_dpt), "vacinas")
    explainer("vacinacao")
    st.caption("Indicador nacional da OMS/UNICEF (Banco Mundial). Mais abaixo há a cobertura por estado do SI-PNI, até 2022.")
    st.markdown("### 👶 Mortalidade na infância")
    names = {r.geo_code: r.geo_name for r in mort[["geo_code", "geo_name"]].drop_duplicates().itertuples()} if not mort.empty else {}
    m_code = st.selectbox("Estado ", ["1"] + sorted((c for c in names if c != "1"), key=lambda c: names[c]),
                          format_func=lambda c: "Brasil" if c == "1" else names.get(c, c), key="mort_uf")
    plot(charts.child_mortality_chart(mort, m_code), "mortalidade_serie")
    plot(charts.child_mortality_ranking_chart(mort, None if m_code == "1" else m_code), "mortalidade_ranking")
    explainer("mortalidade_infancia")
    st.markdown("### 🏨 Leitos hospitalares")
    beds, pni = df("tabnet_leitos"), df("tabnet_pni")
    bed_ufs = sorted(u for u in beds["uf"].unique() if u != "BR") if not beds.empty else []
    b_uf = st.selectbox("Estado  ", ["BR"] + bed_ufs, format_func=lambda x: "Brasil" if x == "BR" else x, key="beds_uf")
    plot(charts.beds_chart(beds, b_uf), "leitos_serie")
    plot(charts.beds_ranking_chart(beds, None if b_uf == "BR" else b_uf), "leitos_ranking")
    explainer("leitos")
    st.caption("O número de leitos não mostra as filas: não existe base nacional aberta de filas do SUS (cirurgias, consultas, exames).")
    st.markdown("### 💉 Cobertura vacinal por estado")
    vac_label = {"triplice_viral": "Tríplice viral (1ª dose)", "polio": "Poliomielite", "penta": "Pentavalente"}
    v_key = st.selectbox("Vacina", list(vac_label), format_func=vac_label.get, key="vac_uf_key")
    pni_ufs = sorted(pni["uf"].unique()) if not pni.empty else []
    v_uf = st.selectbox("Estado   ", pni_ufs or ["SP"], key="vac_uf_sel")
    plot(charts.vaccine_uf_chart(pni, v_key, v_uf), "vacina_ranking")
    plot(charts.vaccine_uf_series_chart(pni, v_key, v_uf), "vacina_serie_uf")
    explainer("vacinacao_uf")
    st.caption("O Ministério da Saúde só publica a cobertura por estado no TabNet até **2022**; a partir de 2023 os dados ficam em um painel "
               "sem acesso automático. Por isso este bloco termina em 2022 (o indicador nacional acima vai até 2024).")
    st.markdown("### 🧓 Quanto vivemos e quanto gastamos com saúde")
    plot(charts.life_expectancy_chart(life), "expectativa_vida")
    explainer("saude_vida")
    data_section("saude", {
        "Dengue por semana e UF (SINAN)": filter_period(dengue, option), "Vacina sarampo (%)": vac, "Vacina DTP (%)": vac_dpt,
        "Mortalidade menores de 5 anos (por mil)": mort, "Expectativa de vida (anos)": life,
        "Gasto total em saúde (% PIB)": spend, "Gasto público em saúde (% PIB)": spend_pub,
        "Leitos por UF (existentes e SUS)": beds, "Cobertura vacinal por UF (%)": pni,
    })

# --------------------------- Aba Agente ------------------------------------- #
with tab5:
    render_agent_tab(data)

st.divider()
st.caption("Monitor Brasil é um projeto de dados abertos. Os números vêm de APIs públicas do governo e podem sofrer "
           "revisões. · Código e dicionário de dados no README.")
