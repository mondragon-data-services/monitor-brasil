"""Leitos do SUS (CNES), cobertura vacinal por estado (SI-PNI) e famílias do Bolsa Família (Ipeadata)."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime

import pandas as pd
import pytest

from src.agent.context import CATALOG, build_context
from src.agent.tools import run_tool
from src.ingestion import extra_jobs
from src.ingestion.cache import FetchResult, load_parquet, save_parquet, with_freshness
from src.ingestion.http_client import IngestionError
from src.ingestion.ipea_client import parse_ipea_levels
from src.ingestion.tabnet_client import (
    _uf_of,
    encode_form,
    parse_form,
    parse_leitos_pre,
    parse_pni_csv,
)
from src.visualization import charts


def text(fig) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fig.layout.title.text.replace("<br>", " "))).strip()


# ---------------------------------------------------------------- TabNet: formulário e parsers -------------------
FORM_HTML = """
<form ACTION="/cgi/tabcgi.exe?cnes/cnv/leiintbr.def" METHOD=POST>
<select name="Linha"><option value="Região" selected>Região<option value="Unidade_da_Federação">Unidade da Federação</select>
<select name=Coluna><option value="--Não-Ativa--" selected>Não ativa<option value="Ano/mês_compet.">Ano/mês compet.</select>
<select name="Arquivos" multiple><option value="ltbr2609.dbf" selected>Set/2026<option value="ltbr2512.dbf">Dez/2025</select>
<select name="SRegião"><option value="TODAS_AS_CATEGORIAS__" selected>Todas as categorias</select>
<input type="hidden" name="nomedef" value="bd_pni/cpnibr.def"><input type=button name="bt_x">
</form>"""


def test_parse_form_reads_selects_options_and_hidden_inputs():
    f = parse_form(FORM_HTML)
    assert [o["value"] for o in f.selects["Linha"]] == ["Região", "Unidade_da_Federação"]
    assert f.selects["Coluna"][1]["text"] == "Ano/mês compet." and [o["value"] for o in f.selects["Arquivos"]][0] == "ltbr2609.dbf"
    assert f.selects["SRegião"][0]["value"] == "TODAS_AS_CATEGORIAS__"
    assert {i.get("name"): i.get("value") for i in f.inputs if i.get("type") == "hidden"} == {"nomedef": "bd_pni/cpnibr.def"}


def test_encode_form_uses_latin1_not_utf8():
    body = encode_form([("Linha", "Unidade_da_Federação"), ("Arquivos", "a b")]).decode()
    assert "Unidade_da_Federa%E7%E3o" in body and "%C3" not in body and "Arquivos=a%20b" in body


def test_uf_label_mapping():
    assert _uf_of('"11 Rondônia"') == "RO" and _uf_of("35 S&atilde;o Paulo") == "SP" and _uf_of("Total") == "BR"
    assert _uf_of("Região Norte") is None and _uf_of("") is None


LEITOS_BODY = """<html><PRE>
"Unidade da Federa&ccedil;&atilde;o";"2024/Dez";"2025/Dez";"2026/Set"
"11 Rond&ocirc;nia";4812;5178;5429
"35 S&atilde;o Paulo";94000;95722;96468
"23 Cear&aacute;";-;12000;12100
"Total";450167;458751;460549
</PRE></html>"""


def test_parse_leitos_pre_handles_entities_totals_and_missing_values():
    df = parse_leitos_pre(LEITOS_BODY)
    assert set(df["uf"]) == {"RO", "SP", "CE", "BR"}
    assert df[(df["uf"] == "BR") & (df["date"] == pd.Timestamp("2026-09-01"))]["value"].iloc[0] == 460549
    assert df[(df["uf"] == "CE")]["value"].tolist() == [12000.0, 12100.0]              # '-' descartado
    assert df["date"].min() == pd.Timestamp("2024-12-01")
    with pytest.raises(IngestionError, match="PRE"):
        parse_leitos_pre("<html>erro</html>")


PNI_CSV = '''"Unidade da Federação";"1999";"2021";"2022";" Total"
"11 Rondônia";"0";"82,54";"89,16";"102,16"
"12 Acre";"0";"60,20";"70,47";"87,21"
"Total";"0";"80,0";"85,0";"90,0"
'''


def test_parse_pni_csv_decimal_comma_zeros_and_total_row():
    df = parse_pni_csv(PNI_CSV, "triplice_viral")
    assert set(df["uf"]) == {"RO", "AC"}                                               # linha Total (soma de %) descartada
    assert df["date"].dt.year.tolist().count(1999) == 0                                 # 0 = sem dado
    assert df[(df["uf"] == "RO") & (df["date"].dt.year == 2022)]["value"].iloc[0] == pytest.approx(89.16)
    assert df["vacina"].eq("triplice_viral").all()
    with pytest.raises(IngestionError):
        parse_pni_csv('"Unidade da Federação";"2022"\n', "x")


# ---------------------------------------------------------------- Ipeadata multinível + cache por validade -------
def test_parse_ipea_levels_maps_brazil_and_states():
    payload = {"value": [
        {"VALDATA": "2025-01-01T00:00:00-03:00", "VALVALOR": 18_618_236.0, "NIVNOME": "Brasil", "TERCODIGO": "0"},
        {"VALDATA": "2025-01-01T00:00:00-03:00", "VALVALOR": 2_301_351.0, "NIVNOME": "Estados", "TERCODIGO": "29"},
        {"VALDATA": "2025-01-01T00:00:00-03:00", "VALVALOR": 99.0, "NIVNOME": "Municípios", "TERCODIGO": "2927408"},
    ]}
    df = parse_ipea_levels(payload)
    assert dict(zip(df["uf"], df["value"], strict=True)) == {"BR": 18_618_236.0, "BA": 2_301_351.0}      # município descartado
    with pytest.raises(IngestionError):
        parse_ipea_levels({"value": []})
    with pytest.raises(IngestionError):
        parse_ipea_levels({"value": [{"VALDATA": "2025-01-01T00:00:00-03:00", "VALVALOR": 1.0}]})


def test_with_freshness_skips_the_source_while_cache_is_valid(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return pd.DataFrame({"date": [pd.Timestamp("2025-01-01")], "value": [1.0]})

    first = with_freshness("k", fetch, 30, tmp_path)
    assert first.mode == "live" and len(calls) == 1
    second = with_freshness("k", fetch, 30, tmp_path)
    assert second.mode == "cache" and second.detail.startswith("cache válido") and len(calls) == 1   # não baixou de novo
    assert with_freshness("k", fetch, 30, tmp_path, force=True).mode == "live" and len(calls) == 2
    stale = with_freshness("k", fetch, 0, tmp_path)                                                  # vencido: consulta a fonte
    assert stale.mode == "live" and len(calls) == 3

    def down():
        raise IngestionError("fora do ar")

    time.sleep(0.01)
    fallback = with_freshness("k", down, 0, tmp_path)                                                # vencido + fonte fora: cai no cache
    assert fallback.mode == "cache" and fallback.ok and fallback.detail.startswith("API indisponível")
    assert with_freshness("novo", down, 30, tmp_path).mode == "missing"
    assert load_parquet("k", tmp_path) is not None and save_parquet is not None


def test_new_sources_are_registered_as_jobs():
    assert {"ipea_pbf_familias", "ipea_pbf_valor_dez", "tabnet_leitos", "tabnet_pni"} <= set(extra_jobs())


# ---------------------------------------------------------------- gráficos --------------------------------------
def beds_df() -> pd.DataFrame:
    rows = []
    for uf, ex, sus in (("BR", 460_549, 318_479), ("SP", 96_468, 55_339), ("RO", 5_429, 4_664)):
        for date, f in ((pd.Timestamp("2011-12-01"), 0.95), (pd.Timestamp("2025-12-01"), 0.99), (pd.Timestamp("2026-09-01"), 1.0)):
            rows.append({"date": date, "uf": uf, "existentes": ex * f, "sus": sus * f})
    return pd.DataFrame(rows)


def pni_df() -> pd.DataFrame:
    rows = []
    for uf, v in (("AP", 61.0), ("SP", 78.0), ("RO", 89.0)):
        for year, delta in ((2014, 20.0), (2022, 0.0)):
            rows.append({"date": pd.Timestamp(f"{year}-01-01"), "uf": uf, "vacina": "triplice_viral", "value": v + delta})
    return pd.DataFrame(rows)


def pbf_df(values: dict[str, float], year: int) -> pd.DataFrame:
    return pd.DataFrame([{"date": pd.Timestamp(f"{year}-01-01"), "uf": u, "value": v} for u, v in values.items()])


def test_beds_charts():
    b = beds_df()
    t = text(charts.beds_chart(b))
    assert "O SUS tem 1,57 leitos hospitalares por mil habitantes (09/2026)" in t and "a mais que em 2011" in t and "no total (com privados) são 2,27" in t
    assert "Em SP, o SUS tem" in text(charts.beds_chart(b, "SP"))
    r = text(charts.beds_ranking_chart(b, "SP"))
    assert r.startswith("RO tem a maior oferta de leitos SUS") and "SP a menor" in r
    assert "Dados indisponíveis" in text(charts.beds_chart(pd.DataFrame(columns=["date", "uf", "existentes", "sus"])))


def test_vaccine_uf_charts_and_wording_when_no_state_hits_target():
    p = pni_df()
    t = text(charts.vaccine_uf_chart(p, "triplice_viral", "SP"))
    assert t.startswith("Nenhum dos 3 estados atingiu a meta de 95% de cobertura da vacina tríplice viral (1ª dose) em 2022") and "o pior é AP (61%)" in t
    p2 = p.copy()
    p2.loc[(p2["uf"] == "RO") & (p2["date"].dt.year == 2022), "value"] = 96.0
    assert text(charts.vaccine_uf_chart(p2)).startswith("Só 1 de 3 estados atingiu a meta")
    s = text(charts.vaccine_uf_series_chart(p, "triplice_viral", "SP"))
    assert "em SP está em 78% em 2022, abaixo da meta de 95%" in s and "o pico foi 98% em 2014" in s


def test_bolsa_familia_family_charts():
    fam = pd.concat([pbf_df({"BR": 20_444_419.0, "BA": 2_454_873.0, "SC": 100_000.0}, 2024), pbf_df({"BR": 18_618_236.0, "BA": 2_301_351.0, "SC": 90_000.0}, 2025)])
    val = pbf_df({"BR": 13_950_784.496}, 2024)
    t = text(charts.pbf_chart(fam, val))
    assert "Bolsa Família chega a 18,6 milhões de famílias em dez/2025, 8,9% a menos que em dez/2024" in t and "benefício médio de R$ 682,38 em dez/2024" in t
    u = text(charts.pbf_uf_chart(fam, "BA"))
    assert u.startswith("BA tem a maior proporção de famílias no Bolsa Família") and "SC a menor" in u


# ---------------------------------------------------------------- agente ----------------------------------------
@pytest.fixture(scope="module")
def gctx():
    now = datetime(2026, 10, 28, 9, 0)
    fr = lambda k, df: FetchResult(k, df, "live", now)  # noqa: E731
    fam = pd.concat([pbf_df({"BR": 20_444_419.0, "BA": 2_454_873.0, "SP": 2_000_000.0}, 2024), pbf_df({"BR": 18_618_236.0, "BA": 2_301_351.0, "SP": 2_163_208.0}, 2025)])
    return build_context({"tabnet_leitos": fr("l", beds_df()), "tabnet_pni": fr("p", pni_df()), "ipea_pbf_familias": fr("f", fam),
                          "ipea_pbf_valor_dez": fr("v", pbf_df({"BR": 13_950_784.496}, 2024))})


def test_catalog_series_for_beds_and_bolsa_familia(gctx):
    for key in ("leitos_sus_por_mil", "leitos_existentes_por_mil", "bolsa_familia_familias", "bolsa_familia_beneficio_medio"):
        assert key in CATALOG
    leitos = json.loads(run_tool(gctx, "get_indicator_snapshot", '{"indicator":"leitos_sus_por_mil"}'))
    assert leitos["ultimo"]["periodo"] == "2025" and leitos["unidade"] == "leitos por mil hab."          # só dezembros (série anual)
    fam = json.loads(run_tool(gctx, "get_indicator_snapshot", '{"indicator":"bolsa_familia_familias"}'))
    assert fam["ultimo"]["valor"] == pytest.approx(18.62) and fam["ha_12_meses"]["valor"] == pytest.approx(20.44)
    med = json.loads(run_tool(gctx, "get_indicator_snapshot", '{"indicator":"bolsa_familia_beneficio_medio"}'))
    assert med["ultimo"]["valor"] == pytest.approx(682.38) and med["ultimo"]["periodo"] == "2024"


def test_health_tool_beds_and_vaccines(gctx):
    sp = json.loads(run_tool(gctx, "get_health_by_uf", '{"metric":"leitos_sus_por_mil","uf":"sp"}'))
    assert sp["uf"] == "SP" and sp["referencia"] == "09/2026" and sp["brasil"] == pytest.approx(1.57) and sp["serie"][-1]["periodo"] == "2026-09"
    rank = json.loads(run_tool(gctx, "get_health_by_uf", '{"metric":"leitos_existentes_por_mil","uf":null}'))
    assert [r["uf"] for r in rank["ranking_decrescente"]][0] == "RO"
    assert "erro" in json.loads(run_tool(gctx, "get_health_by_uf", '{"metric":"leitos_sus_por_mil","uf":"BR"}'))
    v = json.loads(run_tool(gctx, "get_health_by_uf", '{"metric":"cobertura_triplice_viral","uf":null}'))
    assert v["ano_do_dado"] == 2022 and v["meta_pni"] == 95 and v["estados_que_atingiram_a_meta"] == 0 and v["ranking_crescente"][0]["uf"] == "AP"
    assert "2022" in v["observacao"]
    one = json.loads(run_tool(gctx, "get_health_by_uf", '{"metric":"cobertura_triplice_viral","uf":"SP"}'))
    assert one["valor"] == 78 and len(one["serie"]) == 2
    assert "erro" in json.loads(run_tool(gctx, "get_health_by_uf", '{"metric":"cobertura_polio","uf":null}'))      # sem dados dessa vacina...


def test_bolsa_familia_tool(gctx):
    ba = json.loads(run_tool(gctx, "get_bolsa_familia_by_uf", '{"uf":"ba"}'))
    assert ba["uf"] == "BA" and ba["familias"] == 2_301_351 and ba["referencia"] == "dezembro de 2025" and len(ba["serie"]) == 2
    assert ba["brasil"] == 18_618_236 and ba["brasil_ano_anterior"] == 20_444_419 and ba["beneficio_medio_nacional_reais"] == {"ano": 2024, "valor": 682.0}
    rank = json.loads(run_tool(gctx, "get_bolsa_familia_by_uf", '{"uf":null}'))
    assert rank["ranking_por_100_habitantes"][0]["uf"] == "BA"
    assert "erro" in json.loads(run_tool(gctx, "get_bolsa_familia_by_uf", '{"uf":"XX"}'))
    assert "erro" in json.loads(run_tool(build_context({}), "get_bolsa_familia_by_uf", '{"uf":null}'))


def test_prompt_and_quality_notes_reflect_new_coverage():
    from src.agent.prompts import build_instructions
    from src.agent.tools import NOT_AVAILABLE, QUALITY_NOTE
    p = build_instructions("MB-x")
    assert "get_bolsa_familia_by_uf" in p and "leitos hospitalares" in p and "filas de espera do SUS" in p
    assert "leitos" not in NOT_AVAILABLE.split("NÃO tem:")[1].split(",")[0]                      # leitos agora EXISTEM
    assert "filas de espera do SUS" in NOT_AVAILABLE and "depois de 2022" in NOT_AVAILABLE and "2022" in QUALITY_NOTE
