"""Pobreza, trabalho, fome, Bolsa Família e saúde (dengue, vacinação, mortalidade na infância): dados, gráficos e agente."""
from __future__ import annotations

import json
import re
from datetime import date, datetime

import pandas as pd
import pytest

from src.agent.context import CATALOG, build_context
from src.agent.tools import _signal, run_tool
from src.config import IPEA_SOCIAL, SIDRA_SOCIAL, UF_BY_IBGE_CODE, WORLD_BANK_INDICATORS
from src.ingestion import dengue_client, extra_jobs
from src.ingestion.cache import FetchResult
from src.ingestion.dengue_client import aggregate_dengue, epiweek_start, get_dengue
from src.ingestion.http_client import IngestionError
from src.ingestion.ibge_client import parse_sidra_dims, sidra_period_to_date
from src.ingestion.ipea_client import parse_ipea_payload
from src.ingestion.worldbank_client import parse_world_bank
from src.transformation.metrics import (
    dengue_cumulative,
    dengue_incidence_by_uf,
    dengue_last_complete_week,
    dengue_national,
    rolling_sum,
)
from src.visualization import charts


def text(fig) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fig.layout.title.text.replace("<br>", " "))).strip()


# ---------------------------------------------------------------- config ----------------------------------------
def test_social_sources_are_registered():
    assert {"gini", "inseguranca_alimentar", "informalidade", "posicao_ocupacao", "massa_rendimento", "desalento",
            "mortalidade_infancia"} == set(SIDRA_SOCIAL)
    assert {"pobreza", "pobreza_extrema", "caged_saldo", "bolsa_familia"} == set(IPEA_SOCIAL)
    assert IPEA_SOCIAL["bolsa_familia"]["level"] == "Brasil"
    assert {"vacina_sarampo", "expectativa_vida", "gasto_saude_pib"} <= set(WORLD_BANK_INDICATORS)
    keys = set(extra_jobs())
    assert {"soc_gini", "ipea_pobreza", "ipea_bolsa_familia", "wb_vacina_sarampo", "dengue"} <= keys and len(keys) == 22
    assert {"ipea_pbf_familias", "ipea_pbf_valor_dez", "tabnet_leitos", "tabnet_pni"} <= keys
    assert len(UF_BY_IBGE_CODE) == 27


# ---------------------------------------------------------------- parsers ---------------------------------------
def sidra_header(**dims):
    base = {"D1C": "Brasil (Código)", "D1N": "Brasil", "D2C": "Variável (Código)", "D2N": "Variável", "V": "Valor"}
    base.update(dims)
    return base


def test_sidra_annual_period_and_dims_with_two_extra_dimensions():
    assert sidra_period_to_date("2024", "ano") == pd.Timestamp("2024-01-01")
    head = sidra_header(D3C="Situação do domicílio (Código)", D4C="Situação de segurança alimentar existente no domicílio (Código)",
                        D5C="Ano (Código)")
    rows = [{"V": "72.4", "D1C": "1", "D1N": "Brasil", "D3C": "6795", "D3N": "Total", "D4C": "109098", "D4N": "Com segurança alimentar",
             "D5C": "2023", "D5N": "2023"},
            {"V": "...", "D1C": "1", "D1N": "Brasil", "D3C": "6795", "D3N": "Total", "D4C": "109102", "D4N": "grave", "D5C": "2023", "D5N": "2023"}]
    df = parse_sidra_dims([head, *rows], "ano")
    assert len(df) == 1 and df["cat_code"].iloc[0] == "109098"       # categoria = ÚLTIMA dimensão extra (regressão), '...' descartado
    assert df["date"].iloc[0] == pd.Timestamp("2023-01-01") and df["geo_name"].iloc[0] == "Brasil"


def test_sidra_dims_geo_levels_and_moving_quarter():
    head = sidra_header(D3C="Trimestre Móvel (Código)")
    rows = [{"V": "37.5", "D1C": "1", "D1N": "Brasil", "D3C": "202608", "D3N": "jun-jul-ago 2026"}]
    df = parse_sidra_dims([head, *rows], "movel")
    assert df["date"].iloc[0] == pd.Timestamp("2026-08-01") and df["period_label"].iloc[0] == "jun-jul-ago 2026" and df["cat_code"].iloc[0] == ""
    uf_head = sidra_header(D3C="Ano (Código)")
    uf = parse_sidra_dims([uf_head, {"V": "15.1", "D1C": "11", "D1N": "Rondônia", "D3C": "2023", "D3N": "2023"}], "ano")
    assert uf["geo_code"].iloc[0] == "11" and uf["geo_name"].iloc[0] == "Rondônia"
    with pytest.raises(IngestionError):
        parse_sidra_dims([], "ano")


def test_ipea_level_filter_keeps_only_brazil():
    payload = {"value": [{"VALDATA": "2026-08-01T00:00:00-03:00", "VALVALOR": 100.0, "NIVNOME": "Brasil"},
                         {"VALDATA": "2026-08-01T00:00:00-03:00", "VALVALOR": 40.0, "NIVNOME": "Estados"}]}
    assert parse_ipea_payload(payload, "Brasil")["value"].tolist() == [100.0]
    assert len(parse_ipea_payload(payload)) == 1                       # sem filtro, 'drop_duplicates' mantém a última data
    with pytest.raises(IngestionError):
        parse_ipea_payload(payload, "Regiões")


def test_world_bank_parser_drops_nulls():
    payload = [{"page": 1}, [{"date": "2024", "value": 93.0}, {"date": "2025", "value": None}, {"date": "2023", "value": 90.0}]]
    df = parse_world_bank(payload)
    assert df["value"].tolist() == [90.0, 93.0] and df["date"].iloc[-1] == pd.Timestamp("2024-01-01")
    with pytest.raises(IngestionError):
        parse_world_bank([{"page": 1}, []])


# ---------------------------------------------------------------- dengue ----------------------------------------
def test_epiweek_start_is_a_sunday_and_matches_mmwr():
    assert epiweek_start(2026, 1).weekday() == 6                       # domingo
    assert epiweek_start(2019, 1) == pd.Timestamp("2018-12-30")        # semana 1 de 2019 começa em 30/12/2018
    assert (epiweek_start(2026, 2) - epiweek_start(2026, 1)).days == 7


def raw_dengue() -> pd.DataFrame:
    rows = []
    for sem in ("202601", "202602"):
        rows += [{"SEM_NOT": sem, "SG_UF_NOT": "35", "CLASSI_FIN": "10", "EVOLUCAO": "1"}] * 3
        rows += [{"SEM_NOT": sem, "SG_UF_NOT": "35", "CLASSI_FIN": "12", "EVOLUCAO": "2"}]
        rows += [{"SEM_NOT": sem, "SG_UF_NOT": "52", "CLASSI_FIN": "8", "EVOLUCAO": None}] * 2
    rows += [{"SEM_NOT": "202601", "SG_UF_NOT": "99", "CLASSI_FIN": "10", "EVOLUCAO": "1"},          # UF inválida: descartada
             {"SEM_NOT": None, "SG_UF_NOT": "35", "CLASSI_FIN": "10", "EVOLUCAO": "1"}]               # sem semana: descartada
    return pd.DataFrame(rows)


def test_aggregate_dengue_counts_cases_severe_and_deaths():
    out = aggregate_dengue(raw_dengue())
    sp = out[(out["uf"] == "SP") & (out["semana"] == 1)].iloc[0]
    assert (sp["casos"], sp["graves"], sp["obitos"]) == (4, 1, 1) and sp["ano"] == 2026
    assert set(out["uf"]) == {"SP", "GO"} and out["date"].iloc[0] == epiweek_start(2026, 1)
    with pytest.raises(IngestionError, match="layout"):
        aggregate_dengue(pd.DataFrame({"x": [1]}))


def test_get_dengue_caches_closed_years_and_refreshes_current(tmp_path, monkeypatch):
    calls = []

    def fake(year, session=None):
        calls.append(year)
        if year == 2027:
            raise IngestionError("404")
        df = aggregate_dengue(raw_dengue())
        return df.assign(ano=year, date=[epiweek_start(year, s) for s in df["semana"]])

    monkeypatch.setattr(dengue_client, "download_dengue_year", fake)
    first = get_dengue(base=tmp_path, first_year=2025, today=date(2026, 10, 27))
    assert calls == [2025, 2026] and first.mode == "live" and first.ok
    calls.clear()
    again = get_dengue(base=tmp_path, first_year=2025, today=date(2026, 10, 27))
    assert calls == [] and again.mode == "cache" and again.detail.startswith("cache válido")   # nada baixado de novo
    forced = get_dengue(base=tmp_path, first_year=2025, today=date(2026, 10, 27), force=True)
    assert sorted(calls) == [2025, 2026] and forced.mode == "live"

    def down(year, session=None):
        raise IngestionError("fora do ar")

    monkeypatch.setattr(dengue_client, "download_dengue_year", down)
    stale = get_dengue(base=tmp_path, first_year=2025, today=date(2026, 10, 27), force=True)
    assert stale.ok and "fora do ar" in stale.detail                                          # cai para o Parquet de cada ano
    assert get_dengue(base=tmp_path / "vazio", first_year=2025, today=date(2026, 10, 27)).mode == "missing"


def weekly_dengue() -> pd.DataFrame:
    rows = []
    for year, scale in ((2025, 2), (2026, 1)):
        for week in range(1, 41 if year == 2026 else 53):
            for uf, share in (("SP", 3), ("GO", 7)):
                rows.append({"date": epiweek_start(year, week), "ano": year, "semana": week, "uf": uf,
                             "casos": 10 * scale * share, "graves": scale, "obitos": 1 if share == 7 else 0})
    return pd.DataFrame(rows)


def test_dengue_metrics():
    d = weekly_dengue()
    assert dengue_last_complete_week(d, 3) == (2026, 37)               # semana 40 é a última; ignora as 3 mais recentes
    assert dengue_last_complete_week(pd.DataFrame(), 3) is None
    cur = dengue_cumulative(d, 2026, 37)
    assert cur["casos"] == 37 * 100 and cur["obitos"] == 37
    assert dengue_cumulative(d, 2025, 37)["casos"] == 2 * cur["casos"]
    inc = dengue_incidence_by_uf(d, 2026, 37, {"SP": 1_000_000, "GO": 500_000})
    assert inc["uf"].tolist() == ["GO", "SP"] and inc.iloc[0]["incidencia"] == pytest.approx(37 * 70 / 500_000 * 100_000)
    assert len(dengue_national(d, "SP")) == 40 + 52 and dengue_national(d)["casos"].iloc[0] == 100 * 2


def test_rolling_sum():
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=14, freq="MS"), "value": [1.0] * 14})
    out = rolling_sum(df, 12)
    assert len(out) == 3 and out["value"].eq(12.0).all()


# ---------------------------------------------------------------- gráficos --------------------------------------
def annual(values, start=2020, col="value") -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime([f"{start + i}-01-01" for i in range(len(values))]), col: values})


def monthly(values, start="2024-01-01") -> pd.DataFrame:
    return pd.DataFrame({"date": pd.date_range(start, periods=len(values), freq="MS"), "value": values})


def test_poverty_and_gini_titles():
    t = text(charts.poverty_chart(annual([30.0, 28.0, 26.0, 25.0], 2022), annual([6.0, 5.0, 4.8, 4.6], 2022)))
    assert "Pobreza atinge 25,0% da população em 2025 (-1,0 p.p. contra 2024)" in t and "o menor nível da série" in t and "extrema pobreza, 4,6%" in t
    g = text(charts.gini_chart(annual([0.540, 0.504, 0.511], 2023)))
    assert "Gini" in g and "0,511 em 2025, acima de 0,504 em 2024" in g
    assert "Dados indisponíveis" in text(charts.poverty_chart(pd.DataFrame(columns=["date", "value"]), annual([1.0])))


def test_food_insecurity_chart_uses_all_categories():
    rows = []
    for year, vals in ((2023, (72.4, 27.6, 18.2, 5.3, 4.1)), (2024, (75.8, 24.2, 16.4, 4.5, 3.2))):
        for code, v in zip(("109098", "109099", "109100", "109101", "109102"), vals, strict=True):
            rows.append({"date": pd.Timestamp(f"{year}-01-01"), "value": v, "cat_code": code})
    t = text(charts.food_insecurity_chart(pd.DataFrame(rows)))
    assert "24,2% dos domicílios convivem com insegurança alimentar em 2024" in t and "3,2% passam fome" in t and "27,6% e 4,1% em 2023" in t
    assert "Dados indisponíveis" in text(charts.food_insecurity_chart(pd.DataFrame(rows[:3])))


def test_labor_charts():
    inf = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=15, freq="MS"), "value": [38.0] * 3 + [37.5] * 12, "period_label": "x"})
    assert "Informalidade atinge 37,5%" in text(charts.informality_chart(inf)) and "-0,5 p.p. em 12 meses" in text(charts.informality_chart(inf))
    pos = pd.concat([monthly([39000.0] * 3).assign(cat_code="31722"), monthly([13000.0] * 3).assign(cat_code="31723"),
                     monthly([5000.0] * 3).assign(cat_code="31726"), monthly([26000.0] * 3).assign(cat_code="96171")])
    t = text(charts.job_quality_chart(pos))
    assert "39,0 milhões de empregados têm carteira" in t and "18,0 milhões trabalham sem carteira" in t and "26,0 milhões por conta própria" in t
    caged = monthly([100000.0] * 11 + [165827.0] + [-5000.0] * 2)
    c = text(charts.caged_chart(caged))
    assert "Brasil abre" in c and "vagas com carteira em 12 meses" in c and "perdeu empregos formais" in c
    mass = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=15, freq="MS"), "value": [380000.0] * 3 + [391000.0] * 12})
    assert "R$ 391,0 bilhões por mês" in text(charts.income_mass_chart(mass, monthly([2.1] * 15, "2025-01-01")))
    bf = text(charts.bolsa_familia_chart(monthly([12_600_000.0] * 12 + [12_763_250.0], "2025-08-01")))
    assert "Bolsa Família paga R$ 12,8 bilhões em 08/2026" in bf


def test_health_charts():
    d = weekly_dengue()
    t = text(charts.dengue_weekly_chart(d))
    assert "Dengue no Brasil: 3.700 casos prováveis em 2026 até a semana 37, 50% a menos que no mesmo período de 2025" in t
    assert "Dengue em SP:" in text(charts.dengue_weekly_chart(d, "SP"))
    u = text(charts.dengue_uf_chart(d, "SP"))
    assert u.startswith("GO tem a maior incidência de dengue em 2026") and "casos por 100 mil habitantes (Brasil:" in u
    assert "Dados indisponíveis" in text(charts.dengue_weekly_chart(pd.DataFrame()))
    vac = text(charts.vaccine_chart(annual([99.0, 91.0, 93.0], 2000 + 22), annual([90.0, 89.0, 91.0], 2022)))
    assert "Cobertura da vacina contra sarampo está em 93% em 2024, abaixo da meta de 95%" in vac
    life = text(charts.life_expectancy_chart(annual([70.0, 76.0], 2000).assign(date=pd.to_datetime(["2000-01-01", "2024-01-01"]))))
    assert "76,0 anos (2024), 6,0 anos a mais que em 2000" in life


def mortality_df() -> pd.DataFrame:
    rows = []
    for year in (2022, 2023):
        for code, name, v in (("1", "Brasil", 15.2 - (year - 2022) * 0.1), ("35", "São Paulo", 13.4), ("14", "Roraima", 33.2), ("42", "Santa Catarina", 10.7)):
            rows.append({"date": pd.Timestamp(f"{year}-01-01"), "value": v, "geo_code": code, "geo_name": name, "cat_code": "", "cat": "", "period_label": str(year)})
    return pd.DataFrame(rows)


def test_child_mortality_charts():
    m = mortality_df()
    assert "15,1 por mil nascidas vivas em 2023" in text(charts.child_mortality_chart(m))
    assert "Em São Paulo morrem 13,4 crianças menores de 5 anos" in text(charts.child_mortality_chart(m, "35"))
    r = text(charts.child_mortality_ranking_chart(m, "35"))
    assert "Roraima tem a maior mortalidade" in r and "Santa Catarina a menor (10,7)" in r


# ---------------------------------------------------------------- agente ----------------------------------------
@pytest.fixture(scope="module")
def sctx():
    now = datetime(2026, 10, 28, 9, 0)
    fr = lambda k, df: FetchResult(k, df, "live", now)  # noqa: E731
    food_rows = [{"date": pd.Timestamp(f"{y}-01-01"), "value": v, "cat_code": c}
                 for y, vals in ((2023, (72.4, 27.6, 18.2, 5.3, 4.1)), (2024, (75.8, 24.2, 16.4, 4.5, 3.2)))
                 for c, v in zip(("109098", "109099", "109100", "109101", "109102"), vals, strict=True)]
    pos = pd.concat([monthly([39000.0] * 15, "2025-01-01").assign(cat_code=c, period_label="x") for c in ("31722", "31723", "31726", "96171")])
    return build_context({
        "ipea_pobreza": fr("p", annual([30.0, 28.0, 26.6, 25.2], 2022)), "ipea_pobreza_extrema": fr("e", annual([6.0, 5.8, 4.8, 4.6], 2022)),
        "soc_gini": fr("g", annual([0.540, 0.504, 0.511], 2023)), "soc_inseguranca_alimentar": fr("f", pd.DataFrame(food_rows)),
        "soc_informalidade": fr("i", monthly([38.0] * 3 + [37.5] * 12, "2025-01-01").assign(period_label="x")),
        "soc_posicao_ocupacao": fr("po", pos), "ipea_caged_saldo": fr("c", monthly([100000.0] * 24, "2024-01-01")),
        "soc_massa_rendimento": fr("m", monthly([385566.0] * 15, "2025-01-01")), "soc_desalento": fr("d", monthly([2.1] * 15, "2025-01-01")),
        "ipea_bolsa_familia": fr("b", monthly([12_763_250.0] * 15, "2025-01-01")), "dengue": fr("dg", weekly_dengue()),
        "soc_mortalidade_infancia": fr("mi", mortality_df()), "wb_vacina_sarampo": fr("v", annual([87.0, 93.0], 2023)),
        "wb_vacina_dpt": fr("v2", annual([85.0, 91.0], 2023)), "wb_expectativa_vida": fr("l", annual([75.85, 76.02], 2023)),
        "wb_gasto_saude_pib": fr("s", annual([9.7], 2023)), "wb_gasto_publico_saude_pib": fr("sp", annual([4.3], 2023)),
    })


def test_annual_social_indicators_are_not_overwritten_by_security_loop(sctx):
    """Regressão: o laço dos indicadores anuais de segurança zerava TODOS os anuais do catálogo (pobreza, Gini, vacinas...)."""
    for key in ("pobreza", "gini", "fome_grave", "inseguranca_alimentar", "vacina_sarampo", "mortalidade_infancia", "expectativa_vida"):
        snap = json.loads(run_tool(sctx, "get_indicator_snapshot", json.dumps({"indicator": key})))
        assert "erro" not in snap and snap["ultimo"]["valor"] is not None, key
    assert json.loads(run_tool(sctx, "get_indicator_snapshot", '{"indicator":"pobreza"}'))["ultimo"]["valor"] == pytest.approx(25.2)


def test_new_catalog_entries_and_derived_series(sctx):
    for key in ("pobreza", "informalidade", "sem_carteira", "caged_saldo_12m", "bolsa_familia_valor", "dengue_casos_semana", "vacina_sarampo",
                "mortalidade_infancia", "gasto_publico_saude_pib"):
        assert key in CATALOG
    sc = json.loads(run_tool(sctx, "get_indicator_snapshot", '{"indicator":"sem_carteira"}'))
    assert sc["ultimo"]["valor"] == pytest.approx(78.0) and sc["unidade"] == "milhões"     # 31723 + 31726 (em milhões)
    caged = json.loads(run_tool(sctx, "get_indicator_snapshot", '{"indicator":"caged_saldo_12m"}'))
    assert caged["ultimo"]["valor"] == pytest.approx(1_200_000.0)
    bolsa = json.loads(run_tool(sctx, "get_indicator_snapshot", '{"indicator":"bolsa_familia_valor"}'))
    assert bolsa["ultimo"]["valor"] == pytest.approx(12.76) and bolsa["unidade"] == "R$ bilhões/mês"
    dengue = json.loads(run_tool(sctx, "get_indicator_snapshot", '{"indicator":"dengue_casos_semana"}'))
    assert dengue["frequencia"] == "semanal" and dengue["ultimo"]["periodo"] == str(epiweek_start(2026, 37).date())   # 3 últimas semanas removidas


def test_signal_threshold_works_for_small_scale_indexes():
    """Regressão: o Gini subiu 0,007 e saía 'estável' por causa de um limite absoluto de 0,05."""
    gini, pct = CATALOG["gini"], CATALOG["pobreza"]
    assert _signal(gini, 0.007, 0.511) == "piora" and _signal(gini, -0.007, 0.511) == "melhora"
    assert _signal(gini, 0.001, 0.511) == "estavel"
    assert _signal(pct, 0.04, 8.0) == "estavel" and _signal(pct, 0.5, 8.0) == "piora"


def test_get_health_by_uf_tool(sctx):
    d = json.loads(run_tool(sctx, "get_health_by_uf", '{"metric":"dengue_incidencia","uf":"go"}'))
    assert d["uf"] == "GO" and d["ate_a_semana_epidemiologica"] == 37 and d["posicao_no_ranking_de_incidencia"] == 1
    assert d["casos_no_ano"] == 37 * 70 and d["casos_mesmo_periodo_ano_anterior"] == 2 * 37 * 70 and d["obitos_confirmados_no_ano"] == 37
    rank = json.loads(run_tool(sctx, "get_health_by_uf", '{"metric":"dengue_casos","uf":null}'))
    assert rank["brasil"]["casos_no_ano"] == 3700 and [r["uf"] for r in rank["ranking_decrescente"]] == ["GO", "SP"]
    mort = json.loads(run_tool(sctx, "get_health_by_uf", '{"metric":"mortalidade_infancia","uf":null}'))
    assert mort["ano"] == 2023 and mort["ranking_decrescente"][0] == {"uf": "RR", "valor": 33.2}
    sp = json.loads(run_tool(sctx, "get_health_by_uf", '{"metric":"mortalidade_infancia","uf":"SP"}'))
    assert sp["valor"] == 13.4 and len(sp["serie"]) == 2
    assert "erro" in json.loads(run_tool(sctx, "get_health_by_uf", '{"metric":"dengue_casos","uf":"XX"}'))
    assert "valores_validos" in json.loads(run_tool(sctx, "get_health_by_uf", '{"metric":"lixo","uf":null}'))
    assert "erro" in json.loads(run_tool(build_context({}), "get_health_by_uf", '{"metric":"dengue_casos","uf":null}'))


def test_overviews_include_new_areas_and_stay_compact(sctx):
    for area, expected in (("pobreza", "Pobreza e extrema pobreza"), ("saude", "Dengue no Brasil")):
        out = json.loads(run_tool(sctx, "get_overview", json.dumps({"area": area})))
        assert out["indicadores"] and any(expected in h["grafico"] for h in out["manchetes_dos_graficos"])
    tudo = json.loads(run_tool(sctx, "get_overview", '{"area":"tudo"}'))
    per_area: dict[str, int] = {}
    for h in tudo["manchetes_dos_graficos"]:
        per_area[h["area"]] = per_area.get(h["area"], 0) + 1
    assert max(per_area.values()) <= 2                                                      # no máx. 2 manchetes por área
    assert len(run_tool(sctx, "get_overview", '{"area":"tudo"}')) < 16_000


def test_prompt_mentions_social_and_health_scope():
    from src.agent.prompts import build_instructions
    p = build_instructions("MB-x")
    assert "get_health_by_uf" in p and "Caged" in p and "dengue" in p and "Bolsa Família" in p and "mortalidade na infância" in p
