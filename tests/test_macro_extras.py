"""Dólar, Focus, resultado fiscal, crédito das famílias, salário mínimo real e contas da casa."""
from __future__ import annotations

import json
import re
from datetime import date, datetime

import pandas as pd
import pytest

from src.agent.context import CATALOG, build_context
from src.agent.tools import run_tool
from src.config import FOCUS_INDICATORS, IPCA_ITEMS, SGS_SERIES
from src.ingestion import focus_client
from src.ingestion.cache import FetchResult
from src.ingestion.focus_client import build_focus_url, fetch_focus, parse_focus
from src.ingestion.http_client import IngestionError
from src.transformation.metrics import real_minimum_wage
from src.visualization import charts


def text(fig) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fig.layout.title.text.replace("<br>", " "))).strip()


def monthly(values, start="2024-01-01", col="value") -> pd.DataFrame:
    return pd.DataFrame({"date": pd.date_range(start, periods=len(values), freq="MS"), col: values})


# ---------------------------------------------------------------- configuração ----------------------------------
def test_new_series_use_verified_codes():
    expected = {"cambio_dolar": 1, "resultado_primario": 5793, "resultado_nominal": 5727, "inadimplencia_pf": 21084,
                "juros_cartao_rotativo": 22022, "juros_cheque_especial": 20741, "salario_minimo": 1619}
    for key, code in expected.items():
        assert SGS_SERIES[key].code == code
    assert len({s.code for s in SGS_SERIES.values()}) == len(SGS_SERIES)


def test_household_bills_items_are_configured():
    groups = {k: v.group for k, v in IPCA_ITEMS.items()}
    assert {"energia", "gas_botijao", "aluguel", "agua_esgoto", "plano_saude", "remedios", "onibus"} <= set(groups)
    assert {groups[k] for k in ("energia", "aluguel", "internet")} == {"casa"} and groups["plano_saude"] == "saude"
    assert len({i.code for i in IPCA_ITEMS.values()}) == len(IPCA_ITEMS)


# ---------------------------------------------------------------- Focus -----------------------------------------
def focus_payload(indicator: str, year: int) -> dict:
    return {"value": [
        {"Indicador": indicator, "Data": "2026-10-16", "DataReferencia": str(year), "Media": 5.0, "Mediana": 5.1, "Minimo": 4.8, "Maximo": 5.7, "numeroRespondentes": 140},
        {"Indicador": indicator, "Data": "2026-10-23", "DataReferencia": str(year), "Media": 5.1, "Mediana": 5.19, "Minimo": 4.8, "Maximo": 5.7, "numeroRespondentes": 142},
    ]}


def test_focus_url_and_parse():
    url = build_focus_url("IPCA", 2026, date(2024, 1, 1))
    assert "baseCalculo%20eq%200" in url and "DataReferencia%20eq%20%272026%27" in url and "Indicador%20eq%20%27IPCA%27" in url
    df = parse_focus(focus_payload("IPCA", 2026), "IPCA")
    assert list(df.columns) == ["date", "indicador", "ano", "mediana", "media", "minimo", "maximo", "n"]
    assert df["mediana"].iloc[-1] == 5.19 and df["ano"].iloc[0] == 2026
    assert parse_focus({"value": []}, "IPCA").empty
    with pytest.raises(IngestionError):
        parse_focus([], "IPCA")


def test_fetch_focus_queries_every_indicator_and_year(monkeypatch):
    calls = []

    def fake(url, session=None, **kw):
        calls.append(url)
        ind = next(i for i in FOCUS_INDICATORS if f"%27{i.replace(' ', '%20').replace('â', '%C3%A2')}%27" in url or f"%27{i}%27" in url)
        year = int(re.search(r"DataReferencia%20eq%20%27(\d{4})", url).group(1))
        return focus_payload(ind, year)

    monkeypatch.setattr(focus_client, "get_json", fake)
    df = fetch_focus(today=date(2026, 10, 27))
    assert len(calls) == len(FOCUS_INDICATORS) * 3 and set(df["indicador"]) == set(FOCUS_INDICATORS)
    assert set(df["ano"]) == {2026, 2027, 2028}


# ---------------------------------------------------------------- métricas e gráficos ---------------------------
def synth_focus() -> pd.DataFrame:
    rows = []
    for d in pd.date_range("2025-10-01", "2026-10-23", freq="7D"):
        for ind, base in (("IPCA", 4.2), ("Selic", 12.0), ("PIB Total", 2.0), ("Câmbio", 5.4)):
            for year, add in ((2026, 0.0), (2027, -0.8), (2028, -1.2)):
                rows.append({"date": d, "indicador": ind, "ano": year, "mediana": base + add + (d.dayofyear % 3) * 0.1,
                             "media": base, "minimo": base - 0.3, "maximo": base + 0.5, "n": 140})
    df = pd.DataFrame(rows)
    df.loc[(df["indicador"] == "IPCA") & (df["ano"] == 2026) & (df["date"] == df["date"].max()), "mediana"] = 5.19
    return df


def test_real_minimum_wage_corrects_by_inflation():
    ipca = monthly([1.0] * 24, "2024-01-01")                      # 1% ao mês
    sm = monthly([1000.0] * 12 + [1100.0] * 12, "2024-01-01")
    out = real_minimum_wage(sm, ipca)
    assert out["real"].iloc[-1] == pytest.approx(1100.0)            # último mês = valor nominal
    assert out["real"].iloc[0] == pytest.approx(1000.0 * 1.01 ** 23, rel=1e-6)   # jan/24 corrigido por 23 meses
    assert real_minimum_wage(sm.iloc[0:0], ipca).empty


def test_dollar_and_focus_charts_have_active_titles():
    dolar = pd.DataFrame({"date": pd.date_range("2025-10-01", "2026-10-27", freq="D"), "value": 5.4})
    assert "Dólar fecha a R$ 5,40" in text(charts.dollar_chart(dolar, synth_focus())) and "o mercado espera" in text(charts.dollar_chart(dolar, synth_focus()))
    t = text(charts.focus_ipca_chart(synth_focus()))
    assert "Mercado espera IPCA de 5,19% em 2026, acima do teto da meta (4,5%)" in t and "para 2027" in t
    assert "Dados indisponíveis" in text(charts.dollar_chart(pd.DataFrame(columns=["date", "value"])))
    snap = charts.focus_snapshot(synth_focus())
    assert set(snap["indicador"]) == set(FOCUS_INDICATORS) and snap["date"].nunique() == 1


def test_fiscal_chart_reads_signs_correctly():
    deficit = text(charts.fiscal_result_chart(monthly([0.6] * 6, col="value"), monthly([9.5] * 6)))
    assert "déficit nominal de 9,5% do PIB" in deficit and "8,9% do PIB são só juros" in deficit and "0,6% é déficit primário" in deficit
    surplus = text(charts.fiscal_result_chart(monthly([-1.0] * 6), monthly([6.0] * 6)))
    assert "superávit primário de 1,0% do PIB" in surplus and "juros (7,0%)" in surplus


def test_credit_and_min_wage_charts():
    inad, rot = monthly([5.0] * 11 + [6.0] * 14, "2024-01-01"), monthly([440.0] * 25, "2024-01-01")
    t = text(charts.credit_chart(inad, rot, monthly([130.0] * 25, "2024-01-01")))
    assert "Inadimplência das famílias está em 6,0%" in t and "rotativo do cartão cobra 440% ao ano" in t
    sm = monthly([1000.0] * 12 + [1100.0] * 13, "2024-01-01")
    mw = text(charts.min_wage_chart(sm, monthly([0.5] * 25, "2024-01-01"), pd.Timestamp("2024-01-01")))
    assert "Salário mínimo de R$ 1.100,00 tem" in mw and "poder de compra" in mw and "01/2024" in mw


def test_items_charts_filter_by_group():
    rows = [{"date": d, "item": k, "value": 0.5 + (2.0 if k == "energia" else 0.0)} for k in IPCA_ITEMS for d in pd.date_range("2020-01-01", periods=80, freq="MS")]
    items = pd.DataFrame(rows)
    bills = text(charts.items_inflation_chart(items, ("casa", "saude", "transporte")))
    assert "Energia elétrica residencial lidera a alta em 12 meses" in bills
    n_bills = sum(1 for v in IPCA_ITEMS.values() if v.group in ("casa", "saude", "transporte"))
    assert f"de {n_bills} itens" in bills
    assert "Dados indisponíveis" in text(charts.items_inflation_chart(items, ("grupo_que_nao_existe",)))


# ---------------------------------------------------------------- agente ----------------------------------------
@pytest.fixture(scope="module")
def mctx():
    now = datetime(2026, 10, 27, 9, 0)
    fr = lambda k, df: FetchResult(k, df, "live", now)  # noqa: E731
    return build_context({
        "ipca_mensal": fr("i", monthly([0.4] * 40)), "cambio_dolar": fr("c", pd.DataFrame({"date": pd.date_range("2025-10-01", "2026-10-27", freq="D"), "value": 5.3})),
        "resultado_primario": fr("p", monthly([0.6] * 40)), "resultado_nominal": fr("n", monthly([9.5] * 40)),
        "inadimplencia_pf": fr("a", monthly([5.0] * 28 + [6.0] * 12)), "juros_cartao_rotativo": fr("r", monthly([440.0] * 40)),
        "juros_cheque_especial": fr("e", monthly([130.0] * 40)), "salario_minimo": fr("s", monthly([1500.0] * 20 + [1621.0] * 20)),
        "focus": fr("f", synth_focus()),
    })


def test_catalog_has_new_indicators_and_derived_series(mctx):
    for key in ("cambio_dolar", "resultado_nominal", "juros_da_divida", "inadimplencia_pf", "juros_cartao_rotativo",
                "salario_minimo_real", "focus_ipca_ano_corrente", "ipca_item_energia_12m", "ipca_item_plano_saude_12m"):
        assert key in CATALOG
    juros = json.loads(run_tool(mctx, "get_indicator_snapshot", '{"indicator":"juros_da_divida"}'))
    assert juros["ultimo"]["valor"] == pytest.approx(8.9) and juros["unidade"] == "% do PIB"
    inad = json.loads(run_tool(mctx, "get_indicator_snapshot", '{"indicator":"inadimplencia_pf"}'))
    assert inad["leitura_12m"] == "piora" and inad["ha_12_meses"]["variacao_absoluta"] == pytest.approx(1.0)
    real = json.loads(run_tool(mctx, "get_indicator_snapshot", '{"indicator":"salario_minimo_real"}'))
    assert real["ultimo"]["valor"] == pytest.approx(1621.0)


def test_market_expectations_tool(mctx):
    out = json.loads(run_tool(mctx, "get_market_expectations", "{}"))
    ipca26 = next(e for e in out["expectativas"] if e["indicador"] == "IPCA" and e["ano"] == 2026)
    assert ipca26["mediana"] == 5.19 and ipca26["mediana_ha_12_meses"] is not None and "teto 4,5%" in out["meta_de_inflacao"]
    assert {e["indicador"] for e in out["expectativas"]} == set(FOCUS_INDICATORS)
    empty = json.loads(run_tool(build_context({}), "get_market_expectations", "{}"))
    assert "erro" in empty


def test_overview_fiscal_includes_new_indicators_and_headlines(mctx):
    out = json.loads(run_tool(mctx, "get_overview", '{"area":"fiscal"}'))
    keys = {i["indicador"] for i in out["indicadores"]}
    assert {"cambio_dolar", "resultado_primario", "juros_da_divida", "focus_ipca_ano_corrente"} <= keys
    heads = " ".join(h["grafico"] for h in out["manchetes_dos_graficos"])
    assert "Dólar" in heads and "Resultado fiscal" in heads and "Focus" in heads
    tudo = run_tool(mctx, "get_overview", '{"area":"tudo"}')
    assert len(tudo) < 16_000 and "juros_cheque_especial" not in tudo


def test_prompt_mentions_new_scope():
    from src.agent.prompts import build_instructions
    p = build_instructions("MB-x")
    assert "get_market_expectations" in p and "Boletim Focus" in p and "cheque especial" in p and "salário mínimo" in p
