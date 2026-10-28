"""Preços do dia a dia: gasolina (ANP) e inflação por item (IBGE) — parsers, cache, métricas, gráficos e agente."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.agent.context import CATALOG, build_context
from src.agent.tools import run_tool
from src.config import IPCA_ITEMS
from src.ingestion import anp_client
from src.ingestion.anp_client import get_anp_gasolina, parse_anp_sheet
from src.ingestion.cache import FetchResult, save_parquet
from src.ingestion.http_client import IngestionError
from src.ingestion.ipca_itens_client import parse_ipca_items
from src.transformation.metrics import cumulative_index, split_items, value_days_ago
from src.visualization import charts


# ---------------------------------------------------------------- ANP -------------------------------------------
def anp_raw(with_state: bool) -> pd.DataFrame:
    """Planilha bruta da ANP: linhas institucionais, linha de títulos ('DATA INICIAL') e a tabela."""
    cols = ["DATA INICIAL", "DATA FINAL"] + (["REGIÃO", "ESTADO"] if with_state else []) + [
        "PRODUTO", "NÚMERO DE POSTOS PESQUISADOS", "UNIDADE DE MEDIDA", "PREÇO MÉDIO REVENDA", "PREÇO MÍNIMO REVENDA",
        "PREÇO MÁXIMO REVENDA"]
    body = []
    for week in range(3):
        start = pd.Timestamp("2026-10-04") + pd.Timedelta(days=7 * week)
        end = start + pd.Timedelta(days=6)
        for estado in (["SÃO PAULO", "ESPIRITO SANTO", "RORAIMA"] if with_state else [None]):
            geo = ["SUDESTE", estado] if with_state else []
            body.append([start, end, *geo, "GASOLINA COMUM", 100, "R$/l", 6.0 + 0.1 * week + (0.5 if estado == "RORAIMA" else 0), 5.0, 7.0])
            body.append([start, end, *geo, "ETANOL HIDRATADO", 100, "R$/l", 4.0, 3.0, 5.0])
    body.append([pd.Timestamp("2026-10-18"), pd.Timestamp("2026-10-24")] + (["N", "ACRE"] if with_state else [])
                + ["GASOLINA COMUM", 5, "R$/l", "-", "-", "-"])           # valor sigiloso -> descartado
    pad = [["AGÊNCIA NACIONAL DO PETRÓLEO"] + [None] * (len(cols) - 1)] * 17
    return pd.DataFrame(pad + [cols] + body)


def test_parse_anp_states_maps_names_and_filters_product():
    df = parse_anp_sheet(anp_raw(True))
    assert set(df["uf"]) == {"SP", "ES", "RR"}                 # nomes sem acento/caixa alta -> siglas
    assert len(df) == 9 and df["value"].between(6.0, 6.8).all()   # só gasolina comum; '-' descartado
    assert df["date"].max() == pd.Timestamp("2026-10-24")        # data = fim da semana pesquisada


def test_parse_anp_brasil_and_layout_change():
    df = parse_anp_sheet(anp_raw(False))
    assert set(df["uf"]) == {"BR"} and len(df) == 3
    with pytest.raises(IngestionError, match="DATA INICIAL"):
        parse_anp_sheet(pd.DataFrame([["lixo", "x"]] * 30))


def _anp_df(price: float = 6.5) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(["2026-10-24"]), "uf": ["BR"], "value": [price], "postos": [10], "min": [5.0], "max": [8.0]})


def test_anp_cache_validity_and_fallback(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(anp_client, "fetch_anp_gasolina", lambda s=None: calls.append(1) or _anp_df(7.0))
    first = get_anp_gasolina(base=tmp_path)                         # sem cache: baixa
    assert first.mode == "live" and len(calls) == 1
    again = get_anp_gasolina(base=tmp_path)                         # cache fresco: NÃO baixa 13 MB de novo
    assert again.mode == "cache" and again.detail.startswith("cache válido") and len(calls) == 1
    forced = get_anp_gasolina(base=tmp_path, force=True)
    assert forced.mode == "live" and len(calls) == 2

    def boom(s=None):
        raise IngestionError("ANP fora do ar")

    monkeypatch.setattr(anp_client, "fetch_anp_gasolina", boom)
    stale = get_anp_gasolina(base=tmp_path, max_age_days=-1)        # cache vencido + ANP fora -> usa o cache
    assert stale.mode == "cache" and stale.ok and "indisponível" in stale.detail
    assert get_anp_gasolina(base=tmp_path / "vazio").mode == "missing"


# ---------------------------------------------------------------- IPCA por item ---------------------------------
def ipca_payload() -> list[dict]:
    head = {"D3C": "Mês (Código)", "D4C": "Geral, grupo, subgrupo, item e subitem (Código)", "V": "Valor"}
    rows = [head]
    for code, vals in ((7169, ["0.5", "0.4"]), (7212, ["37.76", "..."]), (999999, ["1", "1"])):
        for month, v in zip(("202608", "202609"), vals, strict=True):
            rows.append({"D3C": month, "D4C": str(code), "V": v})
    return rows


def test_parse_ipca_items_maps_codes_and_drops_unknowns():
    df = parse_ipca_items(ipca_payload())
    assert set(df["item"]) == {"geral", "tomate"}                   # código desconhecido ignorado
    assert len(df) == 3                                              # '...' do IBGE removido
    assert df[df["item"] == "tomate"]["value"].iloc[0] == 37.76
    with pytest.raises(IngestionError):
        parse_ipca_items([])


def test_item_config_has_every_requested_product():
    wanted = {"gasolina", "frango_inteiro", "carne_acem", "carne_patinho", "leite", "cafe", "pao", "arroz", "feijao_carioca", "tomate"}
    assert wanted <= set(IPCA_ITEMS) and "geral" in IPCA_ITEMS
    assert len({i.code for i in IPCA_ITEMS.values()}) == len(IPCA_ITEMS)   # códigos únicos


# ---------------------------------------------------------------- métricas --------------------------------------
def test_cumulative_index_and_value_days_ago():
    m = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=3, freq="MS"), "value": [10.0, 10.0, -10.0]})
    assert cumulative_index(m)["value"].round(2).tolist() == [110.0, 121.0, 108.9]
    w = pd.DataFrame({"date": pd.date_range("2025-10-25", periods=54, freq="7D"), "value": range(54)})
    assert value_days_ago(w, 365) == 0.0 or value_days_ago(w, 365) == 1.0   # ~52 semanas antes
    assert value_days_ago(w.iloc[-3:], 365) is None                         # sem dado perto de 12 meses atrás


# ---------------------------------------------------------------- gráficos --------------------------------------
def synth_anp() -> pd.DataFrame:
    rows = []
    for i, d in enumerate(pd.date_range("2025-10-25", periods=54, freq="7D")):
        for uf, bump in (("BR", 0.0), ("SP", -0.14), ("RR", 1.07)):
            rows.append({"date": d, "uf": uf, "value": 6.2 + 0.005 * i + bump, "postos": 50, "min": 5.0, "max": 8.0})
    return pd.DataFrame(rows)


def synth_items() -> pd.DataFrame:
    rows = []
    for key in IPCA_ITEMS:
        for d in pd.date_range("2020-01-01", periods=80, freq="MS"):
            v = 3.0 if key == "tomate" else 0.5
            rows.append({"date": d, "item": key, "value": v})
    return pd.DataFrame(rows)


def title(fig) -> str:
    """Título como texto corrido (o Plotly quebra linhas com <br> e usa tags de formatação)."""
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fig.layout.title.text.replace("<br>", " "))).strip()


def test_gasoline_charts_have_active_titles():
    anp = synth_anp()
    assert "Gasolina comum custa R$" in title(charts.gasoline_chart(anp, "BR")) and "em 12 meses" in title(charts.gasoline_chart(anp, "BR"))
    sp = title(charts.gasoline_chart(anp, "SP"))
    assert "Em SP a gasolina custa" in sp and "abaixo da média do Brasil" in sp
    rank = title(charts.gasoline_ranking_chart(anp, "SP"))
    assert "mais cara no RR" in rank and "mais barata no SP" in rank
    assert "Dados indisponíveis" in title(charts.gasoline_chart(pd.DataFrame(columns=anp.columns)))


def test_items_charts_have_active_titles():
    items = synth_items()
    infl = title(charts.items_inflation_chart(items))
    assert "Tomate lidera a alta em 12 meses" in infl and "itens sobem mais que o IPCA geral" in infl
    cost = title(charts.real_cost_chart(items))
    assert "O que custava R$ 100 em dez/2019" in cost and "tomate foi o que mais subiu" in cost
    assert "Tomate tem a maior alta" in title(charts.items_timeline_chart(items, ["tomate", "arroz"]))
    assert "Dados indisponíveis" in title(charts.items_inflation_chart(pd.DataFrame(columns=["date", "item", "value"])))


# ---------------------------------------------------------------- agente ----------------------------------------
@pytest.fixture(scope="module")
def pctx():
    now = datetime(2026, 10, 27, 9, 0)
    return build_context({"anp_gasolina": FetchResult("anp", synth_anp(), "live", now),
                          "ipca_itens": FetchResult("it", synth_items(), "live", now)})


def test_catalog_and_series_for_prices(pctx):
    assert "gasolina_brasil" in CATALOG and "ipca_item_tomate_12m" in CATALOG and "ipca_item_geral_12m" not in CATALOG
    assert CATALOG["gasolina_brasil"].area == "precos" and not CATALOG["ipca_item_tomate_12m"].core
    snap = json.loads(run_tool(pctx, "get_indicator_snapshot", '{"indicator":"ipca_item_tomate_12m"}'))
    assert snap["ultimo"]["valor"] > 40 and snap["unidade"] == "%"
    gas = json.loads(run_tool(pctx, "get_indicator_snapshot", '{"indicator":"gasolina_brasil"}'))
    assert gas["frequencia"] == "semanal" and gas["ultimo"]["periodo"].count("-") == 2 and gas["ha_12_meses"]["valor"] is not None


def test_fuel_by_uf_tool(pctx):
    rank = json.loads(run_tool(pctx, "get_fuel_by_uf", '{"uf":null}'))
    assert [r["uf"] for r in rank["ranking_decrescente"]] == ["RR", "SP"] and rank["brasil"] is not None
    sp = json.loads(run_tool(pctx, "get_fuel_by_uf", '{"uf":"sp"}'))
    assert sp["uf"] == "SP" and sp["preco_atual"] < sp["preco_brasil"] and len(sp["ultimas_semanas"]) == 8
    assert "erro" in json.loads(run_tool(pctx, "get_fuel_by_uf", '{"uf":"XX"}'))
    assert "erro" in json.loads(run_tool(pctx, "get_fuel_by_uf", '{"uf":"BR"}'))                # BR não é estado


def test_price_ranking_tool(pctx):
    out = json.loads(run_tool(pctx, "get_price_ranking", "{}"))
    first = out["itens_do_maior_para_o_menor_aumento"][0]
    assert first["item"] == "Tomate" and first["acumulado_12m_pct"] > out["ipca_geral_12m_pct"]
    assert first["r100_de_dez2019_viraram"] > out["ipca_geral_r100_viraram"] and "carne moída" in out["observacao"].lower()
    assert out["gasolina_preco_litro_brasil"]["valor"] is not None
    assert all("chave" not in i for i in out["itens_do_maior_para_o_menor_aumento"])


def test_overview_stays_compact_and_area_precos_lists_items(pctx):
    tudo = run_tool(pctx, "get_overview", '{"area":"tudo"}')
    assert len(tudo) < 12_000 and "ipca_item_arroz_12m" not in tudo and "gasolina_brasil" in tudo
    precos = json.loads(run_tool(pctx, "get_overview", '{"area":"precos"}'))
    assert {"ipca_item_arroz_12m", "ipca_item_tomate_12m", "gasolina_brasil"} <= {i["indicador"] for i in precos["indicadores"]}
    assert any("Gasolina" in h["grafico"] or "item" in h["grafico"] for h in precos["manchetes_dos_graficos"])


def test_explain_topic_for_price_items(pctx):
    out = json.loads(run_tool(pctx, "explain_topic", '{"topic":"ipca_item_arroz_12m"}'))
    assert out["o_que_e"] and "IPCA" in out["o_que_e"]
    assert json.loads(run_tool(pctx, "explain_topic", '{"topic":"gasolina_brasil"}'))["o_que_e"]


def test_prompt_teaches_price_scope_and_no_reais_for_food():
    from src.agent.prompts import build_instructions
    p = build_instructions("MB-x")
    assert "get_fuel_by_uf" in p and "get_price_ranking" in p and "nunca o preço em reais" in p and "carne moída" in p.lower()


def test_split_items_roundtrip():
    parts = split_items(synth_items())
    assert set(parts) == set(IPCA_ITEMS) and list(parts["arroz"].columns) == ["date", "value"]
    assert save_parquet is not None and timedelta(days=1).days == 1  # imports usados
