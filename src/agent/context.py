"""Catálogo de indicadores e contexto de dados do Agente Monitora Brasil.

O agente NUNCA lê arquivos, rede ou texto livre: ele só enxerga os indicadores registrados aqui, já limpos e
calculados (acumulado 12m, juro real, taxas), exatamente como alimentam os gráficos do painel.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from src.config import IPCA_ITEMS
from src.config import IPCA_ITEMS
from src.ingestion.cache import FetchResult
from src.transformation.metrics import accumulated_12m, build_real_interest, detect_recession, monthly_last
from src.visualization import charts

AREAS: dict[str, str] = {
    "fiscal": "Inflação, juros e risco fiscal",
    "trabalho": "Emprego, renda, crédito das famílias e atividade econômica",
    "seguranca": "Segurança pública e vulneráveis",
    "educacao": "Educação",
    "precos": "Preços do dia a dia (gasolina, alimentos e contas da casa)",
    "pobreza": "Pobreza, desigualdade, fome, qualidade do emprego e Bolsa Família",
    "saude": "Saúde (dengue, vacinação, mortalidade na infância, expectativa de vida e gasto)",
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
    core: bool = True    # False = só aparece na visão da própria área (mantém a visão geral compacta)


def _ind(*a, **kw) -> Indicator:
    return Indicator(*a, **kw)


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

# Dólar, resultado fiscal, crédito das famílias, salário mínimo e expectativas do mercado (Focus)
for _args in (
    ("cambio_dolar", "Dólar comercial (venda), último valor de cada mês", "R$", "fiscal", "mensal", 12, True,
     "Banco Central (SGS 1)", "Cotação do dólar em reais. Dólar alto pressiona combustíveis e alimentos importados."),
    ("resultado_primario", "Resultado primário do setor público em 12 meses (déficit positivo)", "% do PIB", "fiscal", "mensal", 12, True,
     "Banco Central (SGS 5793)", "Receitas menos despesas sem juros. Positivo = déficit; negativo = superávit."),
    ("resultado_nominal", "Resultado nominal do setor público em 12 meses (déficit positivo)", "% do PIB", "fiscal", "mensal", 12, True,
     "Banco Central (SGS 5727)", "Primário + juros. Positivo = déficit; negativo = superávit."),
    ("juros_da_divida", "Juros pagos pelo setor público em 12 meses", "% do PIB", "fiscal", "mensal", 12, True,
     "Cálculo do painel: resultado nominal − resultado primário (BCB 5727 e 5793)", "Quanto do PIB vai só para pagar juros da dívida."),
    ("inadimplencia_pf", "Inadimplência do crédito das famílias (pessoas físicas)", "%", "trabalho", "mensal", 12, True,
     "Banco Central (SGS 21084)", "Parcela do crédito com atraso acima de 90 dias."),
    ("juros_cartao_rotativo", "Juros do cartão de crédito rotativo (pessoas físicas)", "% a.a.", "trabalho", "mensal", 12, True,
     "Banco Central (SGS 22022)", "Taxa média anual cobrada no rotativo do cartão."),
    ("juros_cheque_especial", "Juros do cheque especial (pessoas físicas)", "% a.a.", "trabalho", "mensal", 12, True,
     "Banco Central (SGS 20741)", "Taxa média anual do cheque especial."),
    ("salario_minimo", "Salário mínimo nominal", "R$", "trabalho", "mensal", 12, False,
     "Banco Central (SGS 1619)", "Valor nominal do salário mínimo nacional."),
    ("salario_minimo_real", "Salário mínimo em reais de hoje (corrigido pelo IPCA)", "R$ de hoje", "trabalho", "mensal", 12, False,
     "Cálculo do painel sobre BCB 1619 e IPCA", "Poder de compra do salário mínimo: valor de cada mês corrigido pela inflação até hoje."),
    ("focus_ipca_ano_corrente", "Expectativa do mercado (Focus) para o IPCA do ano corrente", "%", "fiscal", "mensal", 12, True,
     "Banco Central — Boletim Focus", "Mediana das projeções de inflação do ano corrente; meta 3,0% ± 1,5 p.p."),
    ("focus_ipca_proximo_ano", "Expectativa do mercado (Focus) para o IPCA do próximo ano", "%", "fiscal", "mensal", 12, True,
     "Banco Central — Boletim Focus", "Mediana das projeções de inflação do próximo ano."),
):
    CATALOG[_args[0]] = _ind(*_args, core=_args[0] in ("cambio_dolar", "resultado_nominal", "inadimplencia_pf", "focus_ipca_ano_corrente"))

# Pobreza, desigualdade, fome, qualidade do emprego, Bolsa Família e saúde
for _args, _core in (
    (("pobreza", "Pobreza: população abaixo de US$ 8,30 por dia", "%", "pobreza", "anual", 1, True, "IBGE/PNAD via Ipeadata",
      "Percentual da população com renda abaixo de US$ 8,30/dia (poder de compra)."), True),
    (("pobreza_extrema", "Extrema pobreza: população abaixo de US$ 3,00 por dia", "%", "pobreza", "anual", 1, True, "IBGE/PNAD via Ipeadata",
      "Percentual da população com renda abaixo de US$ 3,00/dia."), False),
    (("gini", "Índice de Gini da renda domiciliar per capita", "índice 0–1", "pobreza", "anual", 1, True, "IBGE (SIDRA 7435)",
      "Desigualdade de renda: 0 = igualdade total, 1 = máxima."), False),
    (("inseguranca_alimentar", "Domicílios com insegurança alimentar (qualquer grau)", "% dos domicílios", "pobreza", "anual", 1, True,
      "IBGE — PNAD (SIDRA 9552)", "Domicílios onde faltou ou houve preocupação com comida."), False),
    (("fome_grave", "Domicílios com insegurança alimentar grave (fome)", "% dos domicílios", "pobreza", "anual", 1, True,
      "IBGE — PNAD (SIDRA 9552)", "Domicílios onde alguém passou fome."), False),
    (("informalidade", "Taxa de informalidade", "%", "pobreza", "trimestre_movel", 12, True, "IBGE — PNAD Contínua (SIDRA 8513)",
      "Parcela dos ocupados sem carteira, sem CNPJ ou sem registro."), True),
    (("carteira_privado", "Empregados do setor privado com carteira assinada", "milhões", "pobreza", "trimestre_movel", 12, False,
      "IBGE — PNAD Contínua (SIDRA 6320)", "Ocupados com carteira no setor privado (exclui doméstico)."), False),
    (("sem_carteira", "Trabalhadores sem carteira (setor privado + doméstico)", "milhões", "pobreza", "trimestre_movel", 12, True,
      "IBGE — PNAD Contínua (SIDRA 6320)", "Soma de empregados do setor privado e domésticos sem carteira."), False),
    (("conta_propria", "Trabalhadores por conta própria", "milhões", "pobreza", "trimestre_movel", 12, None,
      "IBGE — PNAD Contínua (SIDRA 6320)", "Ocupados por conta própria (com e sem CNPJ)."), False),
    (("caged_saldo_mensal", "Saldo mensal de vagas com carteira (Novo Caged)", "vagas", "pobreza", "mensal", 12, False,
      "Ministério do Trabalho via Ipeadata", "Admissões menos desligamentos formais, com ajuste."), False),
    (("caged_saldo_12m", "Saldo de vagas com carteira em 12 meses (Novo Caged)", "vagas", "pobreza", "mensal", 12, False,
      "Cálculo do painel sobre o Novo Caged", "Soma dos últimos 12 saldos mensais."), True),
    (("massa_rendimento", "Massa de rendimento real do trabalho", "R$ bilhões/mês", "pobreza", "trimestre_movel", 12, False,
      "IBGE — PNAD Contínua (SIDRA 6392)", "Soma dos rendimentos reais habituais de todos os ocupados."), False),
    (("desalento", "Desalentados na força de trabalho", "%", "pobreza", "trimestre_movel", 12, True, "IBGE — PNAD Contínua (SIDRA 6807)",
      "Pessoas que desistiram de procurar emprego."), False),
    (("bolsa_familia_valor", "Bolsa Família: valor total pago por mês", "R$ bilhões/mês", "pobreza", "mensal", 12, None,
      "Ministério do Desenvolvimento Social via Ipeadata", "Valor total; o painel não tem número de famílias."), False),
    (("dengue_casos_semana", "Dengue: casos prováveis por semana epidemiológica (Brasil)", "casos", "saude", "semanal", 52, True,
      "Ministério da Saúde — SINAN (OpenDataSUS)", "As últimas 3 semanas são removidas por atraso de registro."), False),
    (("vacina_sarampo", "Cobertura da vacina contra sarampo (12 a 23 meses)", "%", "saude", "anual", 1, False,
      "OMS/UNICEF via Banco Mundial (fonte secundária)", "Meta do PNI: 95%."), True),
    (("leitos_sus_por_mil", "Leitos hospitalares do SUS por mil habitantes (Brasil, dezembro de cada ano)", "leitos por mil hab.", "saude",
      "anual", 1, False, "Ministério da Saúde — CNES via TabNet", "Por estado e o mês mais recente: get_health_by_uf."), False),
    (("leitos_existentes_por_mil", "Leitos hospitalares existentes (SUS + privados) por mil habitantes (Brasil, dezembro)", "leitos por mil hab.",
      "saude", "anual", 1, False, "Ministério da Saúde — CNES via TabNet", "Total de leitos existentes."), False),
    (("bolsa_familia_familias", "Famílias no Bolsa Família em dezembro", "milhões", "pobreza", "anual", 1, None,
      "Ministério do Desenvolvimento Social via Ipeadata", "Número de benefícios pagos em dezembro; por estado use get_bolsa_familia_by_uf."), False),
    (("bolsa_familia_beneficio_medio", "Benefício médio do Bolsa Família em dezembro", "R$", "pobreza", "anual", 1, None,
      "Cálculo do painel (valor total ÷ benefícios, em dezembro; Ipeadata)", "Só existe para os anos em que há valor e número de benefícios."), False),
    (("bolsa_familia_familias_mensal", "Famílias que recebem o Bolsa Família no mês", "milhões", "pobreza", "mensal", 12, None,
      "Portal da Transparência/CGU (ETL do painel)", "Favorecidos distintos com pagamento no mês; por estado use get_bolsa_familia_by_uf."), False),
    (("bolsa_familia_valor_medio_familia", "Valor médio pago por família do Bolsa Família no mês", "R$", "pobreza", "mensal", 12, None,
      "Portal da Transparência/CGU (ETL do painel)", "Total pago ÷ famílias no mês (inclui parcelas retroativas)."), False),
    (("vacina_dpt", "Cobertura da vacina DTP (12 a 23 meses)", "%", "saude", "anual", 1, False,
      "OMS/UNICEF via Banco Mundial (fonte secundária)", "Meta do PNI: 95%."), False),
    (("mortalidade_infancia", "Mortalidade de menores de 5 anos (Brasil)", "por mil nascidos vivos", "saude", "anual", 1, True,
      "IBGE (SIDRA 6695)", "Óbitos antes dos 5 anos por mil nascidos vivos; por estado use get_health_by_uf."), True),
    (("expectativa_vida", "Expectativa de vida ao nascer", "anos", "saude", "anual", 1, False,
      "ONU/OMS via Banco Mundial (fonte secundária)", "Anos de vida em média."), False),
    (("gasto_saude_pib", "Gasto total em saúde (público + privado)", "% do PIB", "saude", "anual", 1, None,
      "OMS via Banco Mundial (fonte secundária)", "Gasto corrente em saúde em proporção do PIB."), False),
    (("gasto_publico_saude_pib", "Gasto público em saúde", "% do PIB", "saude", "anual", 1, None,
      "OMS via Banco Mundial (fonte secundária)", "Gasto do governo geral em saúde em proporção do PIB."), False),
):
    CATALOG[_args[0]] = _ind(*_args, core=_core)

# Preços do dia a dia: gasolina (ANP) e inflação acumulada em 12 meses por item do IPCA (IBGE)
CATALOG["gasolina_brasil"] = _ind(
    "gasolina_brasil", "Preço médio da gasolina comum no Brasil", "R$/litro", "precos", "semanal", 52, True,
    "ANP — Levantamento de Preços de Combustíveis (síntese semanal)",
    "Preço médio de revenda da gasolina comum em milhares de postos; por estado use get_fuel_by_uf.")
for _key, _item in IPCA_ITEMS.items():
    if _key != "geral":
        CATALOG[f"ipca_item_{_key}_12m"] = _ind(
            f"ipca_item_{_key}_12m", f"IPCA — {_item.label}: acumulado em 12 meses", "%", "precos", "mensal", 12, True,
            "IBGE — IPCA por item (SIDRA 7060)",
            (_item.note or "Inflação acumulada em 12 meses do item.") + " O IBGE publica variação, não preço em reais.",
            core=False)

# Preços do dia a dia: gasolina (ANP) e inflação acumulada em 12 meses por item do IPCA (IBGE)
CATALOG["gasolina_brasil"] = _ind(
    "gasolina_brasil", "Preço médio da gasolina comum no Brasil", "R$/litro", "precos", "semanal", 52, True,
    "ANP — Levantamento de Preços de Combustíveis (síntese semanal)",
    "Preço médio de revenda da gasolina comum em milhares de postos; por estado use get_fuel_by_uf.")
for _key, _item in IPCA_ITEMS.items():
    if _key != "geral":
        CATALOG[f"ipca_item_{_key}_12m"] = _ind(
            f"ipca_item_{_key}_12m", f"IPCA — {_item.label}: acumulado em 12 meses", "%", "precos", "mensal", 12, True,
            "IBGE — IPCA por item (SIDRA 7060)",
            (_item.note or "Inflação acumulada em 12 meses do item.") + " O IBGE publica variação, não preço em reais.",
            core=False)

# Indicadores de segurança/educação que vêm do consolidado anual (coluna "indicador")
_ANNUAL_FROM_SEC = [k for k, v in CATALOG.items() if v.freq == "anual" and v.area in ("seguranca", "educacao")]


@dataclass
class DataContext:
    """Séries prontas (colunas ``date``/``value``), o consolidado por UF e metadados de frescor."""

    series: dict[str, pd.DataFrame] = field(default_factory=dict)
    quality: dict[str, str] = field(default_factory=dict)       # indicador -> oficial | aproximado
    sec: pd.DataFrame = field(default_factory=pd.DataFrame)       # consolidado de segurança/educação (inclui UFs)
    freshness: dict[str, str] = field(default_factory=dict)       # fonte -> "ao vivo em ..." | "cache de ..."
    raw: dict[str, pd.DataFrame] = field(default_factory=dict)    # séries brutas usadas pelos gráficos
    dengue: pd.DataFrame = field(default_factory=pd.DataFrame)    # SINAN: date, ano, semana, uf, casos, graves, obitos
    mortality: pd.DataFrame = field(default_factory=pd.DataFrame) # mortalidade <5 anos: date, geo_code, geo_name, value
    beds: pd.DataFrame = field(default_factory=pd.DataFrame)      # leitos CNES: date, uf (BR + UFs), existentes, sus
    pni: pd.DataFrame = field(default_factory=pd.DataFrame)       # cobertura vacinal por UF: date, uf, vacina, value
    pbf_fam: pd.DataFrame = field(default_factory=pd.DataFrame)   # benefícios do Bolsa Família em dezembro: date, uf, value
    pbf_val: pd.DataFrame = field(default_factory=pd.DataFrame)   # valor do Bolsa Família em dezembro (R$ mil): date, uf, value
    pbf_m: pd.DataFrame = field(default_factory=pd.DataFrame)     # Bolsa Família mensal: date, uf, familias, pagamentos, valor (R$)
    beds: pd.DataFrame = field(default_factory=pd.DataFrame)      # leitos CNES: date, uf (BR + UFs), existentes, sus
    pni: pd.DataFrame = field(default_factory=pd.DataFrame)       # cobertura vacinal por UF: date, uf, vacina, value
    pbf_fam: pd.DataFrame = field(default_factory=pd.DataFrame)   # benefícios do Bolsa Família em dezembro: date, uf, value
    pbf_val: pd.DataFrame = field(default_factory=pd.DataFrame)   # valor do Bolsa Família em dezembro (R$ mil): date, uf, value
    social: dict = field(default_factory=dict)                    # quadros de pobreza/trabalho/saúde usados pelos gráficos
    focus: pd.DataFrame = field(default_factory=pd.DataFrame)     # Boletim Focus: date, indicador, ano, mediana...
    anp: pd.DataFrame = field(default_factory=pd.DataFrame)       # gasolina: date, uf (BR + estados), value
    items: pd.DataFrame = field(default_factory=pd.DataFrame)     # IPCA por item (longo): date, item, value
    anp: pd.DataFrame = field(default_factory=pd.DataFrame)       # gasolina: date, uf (BR + estados), value
    items: pd.DataFrame = field(default_factory=pd.DataFrame)     # IPCA por item (longo): date, item, value

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
        try:
            if not r["cambio_dolar"].empty:
                out.append(("fiscal", "Dólar e expectativas", charts.dollar_chart(r["cambio_dolar"], self.focus)))
            if not self.focus.empty:
                out.append(("fiscal", "Expectativa de inflação (Focus)", charts.focus_ipca_chart(self.focus)))
            out.append(("fiscal", "Resultado fiscal (primário, juros e nominal)",
                        charts.fiscal_result_chart(r["resultado_primario"], r["resultado_nominal"])))
            out.append(("trabalho", "Crédito das famílias", charts.credit_chart(
                r["inadimplencia_pf"], r["juros_cartao_rotativo"], r["juros_cheque_especial"])))
            out.append(("trabalho", "Salário mínimo real", charts.min_wage_chart(r["salario_minimo"], r["ipca_mensal"])))
        except KeyError:
            pass
        try:
            sf = _social_frames(self)
            out.append(("pobreza", "Pobreza e extrema pobreza", charts.poverty_chart(sf["pobreza"], sf["pobreza_extrema"])))
            out.append(("pobreza", "Desigualdade (Gini)", charts.gini_chart(sf["gini"])))
            out.append(("pobreza", "Insegurança alimentar", charts.food_insecurity_chart(sf["food_raw"])))
            out.append(("pobreza", "Informalidade", charts.informality_chart(sf["informalidade_raw"])))
            out.append(("pobreza", "Emprego formal (Caged)", charts.caged_chart(sf["caged"])))
            out.append(("pobreza", "Bolsa Família", charts.bolsa_familia_chart(sf["bolsa"])))
            out.append(("saude", "Leitos hospitalares", charts.beds_chart(self.beds)))
            out.append(("saude", "Cobertura vacinal por estado (tríplice viral)", charts.vaccine_uf_chart(self.pni, "triplice_viral")))
            out.append(("pobreza", "Bolsa Família: famílias e benefício médio", charts.pbf_chart(self.pbf_fam, self.pbf_val)))
            if not self.pbf_m.empty:
                out.append(("pobreza", "Bolsa Família mensal", charts.pbf_monthly_chart(self.pbf_m)))
            out.append(("saude", "Dengue no Brasil", charts.dengue_weekly_chart(self.dengue)))
            out.append(("saude", "Dengue por estado", charts.dengue_uf_chart(self.dengue)))
            out.append(("saude", "Vacinação (sarampo e DTP)", charts.vaccine_chart(sf["vacina_sarampo"], sf["vacina_dpt"])))
            out.append(("saude", "Mortalidade na infância", charts.child_mortality_chart(self.mortality)))
            out.append(("saude", "Expectativa de vida", charts.life_expectancy_chart(sf["expectativa_vida"])))
        except (KeyError, ValueError):
            pass
        if not self.anp.empty:
            out.append(("precos", "Gasolina (Brasil)", charts.gasoline_chart(self.anp, "BR")))
            out.append(("precos", "Ranking da gasolina por estado", charts.gasoline_ranking_chart(self.anp, None)))
        if not self.items.empty:
            out.append(("precos", "Inflação em 12 meses por item (comida e gasolina)",
                        charts.items_inflation_chart(self.items, ("comida", "combustivel", "referencia"))))
            out.append(("precos", "O que custava R$ 100 em dez/2019 (comida e gasolina)",
                        charts.real_cost_chart(self.items, ("comida", "combustivel", "referencia"))))
            out.append(("precos", "Contas fixas da casa em 12 meses",
                        charts.items_inflation_chart(self.items, ("casa", "saude", "transporte"))))
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


def _social_frames(ctx: "DataContext") -> dict[str, pd.DataFrame]:
    """Quadros brutos de pobreza/trabalho/saúde usados pelos gráficos (guardados em ``ctx.raw``)."""
    empty = pd.DataFrame(columns=["date", "value"])
    return {k: ctx.social.get(k, empty) for k in ("pobreza", "pobreza_extrema", "gini", "food_raw", "informalidade_raw", "caged", "bolsa",
                                                 "vacina_sarampo", "vacina_dpt", "expectativa_vida")}


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

    ctx.series = {
        "ipca_12m": ipca12, "ipca_alimentacao_12m": food12,
        "selic_meta": monthly_last(selic) if not selic.empty else selic,
        "juro_real": real[["date", "juro_real"]].rename(columns={"juro_real": "value"}) if not real.empty else pd.DataFrame(),
        "divida_bruta": frame("divida_bruta"), "divida_liquida": frame("divida_liquida"),
        "desocupacao": frame("desocupacao"), "subutilizacao": frame("subutilizacao"),
        "rendimento_real": frame("rendimento_real"), "endividamento_familias": frame("endividamento_familias"),
        "pib_4tri": frame("pib_4tri"), "pib_trimestral": frame("pib_trimestral"), "ibcbr_12m": ibc_yoy,
    }
    from src.transformation.metrics import real_minimum_wage
    prim, nom = frame("resultado_primario"), frame("resultado_nominal")
    if not prim.empty and not nom.empty:
        juros = prim.rename(columns={"value": "p"}).merge(nom.rename(columns={"value": "n"}), on="date")
        ctx.series["juros_da_divida"] = juros.assign(value=juros["n"] - juros["p"])[["date", "value"]]
    for key in ("cambio_dolar",):
        raw_df = frame(key)
        ctx.series[key] = monthly_last(raw_df) if not raw_df.empty else raw_df
    for key in ("resultado_primario", "resultado_nominal", "inadimplencia_pf", "juros_cartao_rotativo", "juros_cheque_especial",
                "salario_minimo"):
        ctx.series[key] = frame(key)
    sm = frame("salario_minimo")
    if not sm.empty and not ipca.empty:
        real_sm = real_minimum_wage(sm, ipca)
        ctx.series["salario_minimo_real"] = real_sm[["date", "real"]].rename(columns={"real": "value"})
    focus_res = results.get("focus")
    if focus_res is not None and focus_res.ok:
        ctx.focus = focus_res.df
        year0 = int(focus_res.df["date"].max().year)
        for key, yr in (("focus_ipca_ano_corrente", year0), ("focus_ipca_proximo_ano", year0 + 1)):
            sub = focus_res.df[(focus_res.df["indicador"] == "IPCA") & (focus_res.df["ano"] == yr)]
            s = pd.DataFrame({"date": sub["date"], "value": sub["mediana"]})
            ctx.series[key] = monthly_last(s) if not s.empty else s
    def res_df(key: str) -> pd.DataFrame:
        r = results.get(key)
        return r.df if r is not None and r.ok else pd.DataFrame()

    def vdf(df_in: pd.DataFrame, scale: float = 1.0) -> pd.DataFrame:
        if df_in.empty:
            return pd.DataFrame(columns=["date", "value"])
        cols = [c for c in ("date", "value", "period_label") if c in df_in.columns]
        out = df_in[cols].copy()
        out["value"] = out["value"] * scale
        return out.reset_index(drop=True)

    from src.transformation.metrics import dengue_national, rolling_sum
    poverty, extreme, gini_df = res_df("ipea_pobreza"), res_df("ipea_pobreza_extrema"), res_df("soc_gini")
    ctx.series["pobreza"], ctx.series["pobreza_extrema"], ctx.series["gini"] = vdf(poverty), vdf(extreme), vdf(gini_df)
    food_df = res_df("soc_inseguranca_alimentar")
    if not food_df.empty:
        ctx.series["inseguranca_alimentar"] = vdf(food_df[food_df["cat_code"] == "109099"])
        ctx.series["fome_grave"] = vdf(food_df[food_df["cat_code"] == "109102"])
    inf_df = res_df("soc_informalidade")
    ctx.series["informalidade"] = vdf(inf_df)
    pos_df = res_df("soc_posicao_ocupacao")
    if not pos_df.empty:
        wide = pos_df.pivot_table(index="date", columns="cat_code", values="value", aggfunc="last").sort_index() / 1000.0
        labels = pos_df.drop_duplicates("date").set_index("date")["period_label"]

        def pos_series(col: str) -> pd.DataFrame:
            s = wide[col].dropna().reset_index().rename(columns={col: "value"})
            s["period_label"] = s["date"].map(labels)
            return s
        if {"31722", "31723", "31726", "96171"} <= set(wide.columns):
            wide["sem_carteira"] = wide["31723"] + wide["31726"]
            ctx.series["carteira_privado"], ctx.series["sem_carteira"] = pos_series("31722"), pos_series("sem_carteira")
            ctx.series["conta_propria"] = pos_series("96171")
    caged = vdf(res_df("ipea_caged_saldo"))
    ctx.series["caged_saldo_mensal"] = caged
    ctx.series["caged_saldo_12m"] = rolling_sum(caged, 12) if not caged.empty else caged
    ctx.series["massa_rendimento"] = vdf(res_df("soc_massa_rendimento"), 0.001)
    ctx.series["desalento"] = vdf(res_df("soc_desalento"))
    bolsa = vdf(res_df("ipea_bolsa_familia"), 1e-6)
    ctx.series["bolsa_familia_valor"] = bolsa
    ctx.beds, ctx.pni = res_df("tabnet_leitos"), res_df("tabnet_pni")
    ctx.pbf_fam, ctx.pbf_val = res_df("ipea_pbf_familias"), res_df("ipea_pbf_valor_dez")
    if not ctx.beds.empty:
        per_mil = charts.beds_per_thousand(ctx.beds)
        dec = per_mil[(per_mil["uf"] == "BR") & (per_mil["date"].dt.month == 12)]
        ctx.series["leitos_sus_por_mil"] = dec[["date", "sus_mil"]].rename(columns={"sus_mil": "value"}).reset_index(drop=True)
        ctx.series["leitos_existentes_por_mil"] = dec[["date", "existentes_mil"]].rename(columns={"existentes_mil": "value"}).reset_index(drop=True)
    if not ctx.pbf_fam.empty:
        fam_br = ctx.pbf_fam[ctx.pbf_fam["uf"] == "BR"]
        ctx.series["bolsa_familia_familias"] = fam_br.assign(value=fam_br["value"] / 1e6)[["date", "value"]].reset_index(drop=True)
        if not ctx.pbf_val.empty:
            both = fam_br.merge(ctx.pbf_val[ctx.pbf_val["uf"] == "BR"], on=["date", "uf"], suffixes=("_f", "_v"))
            ctx.series["bolsa_familia_beneficio_medio"] = both.assign(value=both["value_v"] * 1000 / both["value_f"])[["date", "value"]].reset_index(drop=True)
    ctx.pbf_m = res_df("pbf_mensal")
    if not ctx.pbf_m.empty:
        br_m = ctx.pbf_m[ctx.pbf_m["uf"] == "BR"].sort_values("date")
        ctx.series["bolsa_familia_familias_mensal"] = br_m.assign(value=br_m["familias"] / 1e6)[["date", "value"]].reset_index(drop=True)
        ctx.series["bolsa_familia_valor_medio_familia"] = br_m.assign(value=br_m["valor"] / br_m["familias"])[["date", "value"]].reset_index(drop=True)
    ctx.dengue = res_df("dengue")
    if not ctx.dengue.empty:
        from src.config import DENGUE_INCOMPLETE_WEEKS
        nat = dengue_national(ctx.dengue)
        weekly = nat[["date", "casos"]].rename(columns={"casos": "value"})
        ctx.series["dengue_casos_semana"] = weekly.iloc[:-DENGUE_INCOMPLETE_WEEKS].reset_index(drop=True) if len(weekly) > DENGUE_INCOMPLETE_WEEKS else weekly
    mort = res_df("soc_mortalidade_infancia")
    ctx.mortality = mort
    if not mort.empty:
        ctx.series["mortalidade_infancia"] = vdf(mort[mort["geo_code"] == "1"])
    for key, wb in (("vacina_sarampo", "wb_vacina_sarampo"), ("vacina_dpt", "wb_vacina_dpt"), ("expectativa_vida", "wb_expectativa_vida"),
                    ("gasto_saude_pib", "wb_gasto_saude_pib"), ("gasto_publico_saude_pib", "wb_gasto_publico_saude_pib")):
        ctx.series[key] = vdf(res_df(wb))
    ctx.social = ({"pobreza": vdf(poverty), "pobreza_extrema": vdf(extreme), "gini": vdf(gini_df), "food_raw": food_df,
                    "informalidade_raw": vdf(inf_df), "caged": caged, "bolsa": vdf(res_df("ipea_bolsa_familia")),
                    "vacina_sarampo": vdf(res_df("wb_vacina_sarampo")), "vacina_dpt": vdf(res_df("wb_vacina_dpt")),
                    "expectativa_vida": vdf(res_df("wb_expectativa_vida"))})
    sec_res = results.get("seguranca_educacao")
    ctx.sec = sec_res.df if sec_res is not None and sec_res.ok else pd.DataFrame()
    if not ctx.sec.empty:
        for key in _ANNUAL_FROM_SEC:
            ctx.series[key] = _annual_series(ctx.sec, key)
            q = ctx.sec[(ctx.sec["indicador"] == key) & (ctx.sec["uf"] == "BR")]["qualidade"]
            ctx.quality[key] = "oficial" if len(q) and (q == "oficial").all() else "aproximado"
    anp_res, items_res = results.get("anp_gasolina"), results.get("ipca_itens")
    if anp_res is not None and anp_res.ok:
        ctx.anp = anp_res.df
        ctx.series["gasolina_brasil"] = anp_res.df[anp_res.df["uf"] == "BR"][["date", "value"]].reset_index(drop=True)
    if items_res is not None and items_res.ok:
        from src.transformation.metrics import split_items
        ctx.items = items_res.df
        for item_key, item_df in split_items(items_res.df).items():
            if item_key != "geral":
                ctx.series[f"ipca_item_{item_key}_12m"] = accumulated_12m(item_df).dropna()
    anp_res, items_res = results.get("anp_gasolina"), results.get("ipca_itens")
    if anp_res is not None and anp_res.ok:
        ctx.anp = anp_res.df
        ctx.series["gasolina_brasil"] = anp_res.df[anp_res.df["uf"] == "BR"][["date", "value"]].reset_index(drop=True)
    if items_res is not None and items_res.ok:
        from src.transformation.metrics import split_items
        ctx.items = items_res.df
        for item_key, item_df in split_items(items_res.df).items():
            if item_key != "geral":
                ctx.series[f"ipca_item_{item_key}_12m"] = accumulated_12m(item_df).dropna()
    for key in CATALOG:
        ctx.quality.setdefault(key, "oficial")
    ctx.raw = {
        "cambio_dolar": frame("cambio_dolar"), "resultado_primario": prim, "resultado_nominal": nom,
        "inadimplencia_pf": frame("inadimplencia_pf"), "juros_cartao_rotativo": frame("juros_cartao_rotativo"),
        "juros_cheque_especial": frame("juros_cheque_especial"), "salario_minimo": frame("salario_minimo"),
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
