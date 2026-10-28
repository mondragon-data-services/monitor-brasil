"""Configuração central: séries SGS, tabelas SIDRA, caminhos, cores e textos didáticos."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #
ROOT_DIR: Path = Path(__file__).resolve().parent.parent
DATA_RAW: Path = ROOT_DIR / "data" / "raw"
DATA_PROCESSED: Path = ROOT_DIR / "data" / "processed"

SINESP_PARQUET: Path = DATA_PROCESSED / "sinesp_vde.parquet"
CONSOLIDATED_PARQUET: Path = DATA_PROCESSED / "seguranca_educacao_consolidado.parquet"

# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
USER_AGENT: str = "MonitorBrasilDataPipeline/1.0"
HTTP_HEADERS: dict[str, str] = {"Accept": "application/json", "User-Agent": USER_AGENT}
HTTP_TIMEOUT: tuple[float, float] = (10.0, 45.0)  # (connect, read) em segundos
SGS_MAX_WINDOW_YEARS: int = 10  # regra do BCB desde mar/2025
SGS_HISTORY_START: str = "2016-01-01"  # início da janela histórica baixada

BCB_URL_TEMPLATE: str = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
    "?formato=json&dataInicial={start}&dataFinal={end}"
)
IPEA_URL_TEMPLATE: str = "http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='{code}')"


# --------------------------------------------------------------------------- #
# Metadados de séries
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SgsSeries:
    key: str
    code: int
    name: str
    unit: str
    description: str

    @property
    def source_url(self) -> str:
        return f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{self.code}/dados?formato=json"


SGS_SERIES: dict[str, SgsSeries] = {
    s.key: s
    for s in (
        SgsSeries("ipca_mensal", 433, "IPCA — variação mensal", "%", "Inflação oficial do Brasil"),
        SgsSeries("inpc_mensal", 188, "INPC — variação mensal", "%",
                  "Inflação da baixa renda (famílias de 1 a 5 salários mínimos)"),
        SgsSeries("ipca_alimentacao", 1635, "IPCA — Alimentação e bebidas", "%",
                  "Inflação do supermercado e da alimentação"),
        SgsSeries("selic_meta", 432, "Meta da taxa Selic", "% a.a.", "Juros básicos da economia"),
        SgsSeries("divida_bruta", 13762, "Dívida Bruta do Governo Geral", "% do PIB",
                  "Metodologia do Banco Central"),
        SgsSeries("divida_liquida", 4513, "Dívida Líquida do Setor Público", "% do PIB", "Setor público consolidado"),
        SgsSeries("endividamento_familias", 29037, "Endividamento das famílias com o SFN", "%",
                  "Dívida das famílias em relação à renda acumulada em 12 meses"),
        SgsSeries("ibcbr", 24364, "IBC-Br com ajuste sazonal", "índice",
                  "Índice de Atividade Econômica do Banco Central — proxy mensal do PIB"),
        SgsSeries("cambio_dolar", 1, "Dólar comercial (venda)", "R$", "Cotação diária do dólar americano"),
        SgsSeries("resultado_primario", 5793, "Resultado primário do setor público (déficit positivo)", "% do PIB",
                  "NFSP sem desvalorização cambial, fluxo acumulado em 12 meses. Positivo = déficit, negativo = superávit"),
        SgsSeries("resultado_nominal", 5727, "Resultado nominal do setor público (déficit positivo)", "% do PIB",
                  "NFSP sem desvalorização cambial, fluxo acumulado em 12 meses. Nominal = primário + juros"),
        SgsSeries("inadimplencia_pf", 21084, "Inadimplência da carteira de crédito — pessoas físicas", "%",
                  "Parcela do crédito das famílias com atraso acima de 90 dias"),
        SgsSeries("juros_cartao_rotativo", 22022, "Juros do cartão de crédito rotativo — pessoas físicas", "% a.a.",
                  "Taxa média de juros do rotativo do cartão (recursos livres)"),
        SgsSeries("juros_cheque_especial", 20741, "Juros do cheque especial — pessoas físicas", "% a.a.",
                  "Taxa média de juros do cheque especial (recursos livres)"),
        SgsSeries("salario_minimo", 1619, "Salário mínimo", "R$", "Valor nominal do salário mínimo nacional"),
    )
}


@dataclass(frozen=True)
class SidraTable:
    key: str
    table: int
    name: str
    unit: str
    url: str
    period_kind: str  # "movel" (trimestre móvel YYYYMM) | "trimestre" (YYYY0Q)

    @property
    def source_url(self) -> str:
        return f"https://sidra.ibge.gov.br/tabela/{self.table}"


_SIDRA = "https://apisidra.ibge.gov.br/values"
SIDRA_TABLES: dict[str, SidraTable] = {
    t.key: t
    for t in (
        SidraTable("desocupacao", 6381, "Taxa de desocupação (PNAD Contínua)", "%",
                   f"{_SIDRA}/t/6381/n1/all/v/4099/p/last%2040", "movel"),
        # O meta-prompt cita a tabela 6438/v4118, que hoje responde HTTP 400.
        # A taxa composta de subutilização (variável 4118) está na tabela 6441.
        SidraTable("subutilizacao", 6441, "Taxa composta de subutilização da força de trabalho", "%",
                   f"{_SIDRA}/t/6441/n1/all/v/4118/p/last%2040", "movel"),
        SidraTable("rendimento_real", 6390, "Rendimento médio real habitual (todos os trabalhos)", "R$",
                   f"{_SIDRA}/t/6390/n1/all/v/5933/p/last%2040", "movel"),
        SidraTable("pib_4tri", 5932, "PIB — taxa acumulada em 4 trimestres", "%",
                   f"{_SIDRA}/t/5932/n1/all/v/6562/p/last%2032/c11255/90707", "trimestre"),
        # Extra: variação trimestral dessazonalizada, necessária ao detector de recessão técnica.
        SidraTable("pib_trimestral", 5932, "PIB — variação trimestre contra trimestre anterior (dessaz.)", "%",
                   f"{_SIDRA}/t/5932/n1/all/v/6564/p/last%2032/c11255/90707", "trimestre"),
    )
}

# --------------------------------------------------------------------------- #
# Preços do dia a dia: IPCA por item (IBGE) e gasolina (ANP)
# --------------------------------------------------------------------------- #
IPCA_ITEMS_TABLE = 7060  # IPCA — variação mensal por grupo/item/subitem (série desde jan/2020)
IPCA_ITEMS_URL = f"{_SIDRA}/t/7060/n1/all/v/63/p/all/c315/" + "{codes}"


@dataclass(frozen=True)
class IpcaItem:
    key: str
    code: int          # código na classificação c315 da tabela 7060
    label: str
    group: str         # "referencia" | "comida" | "combustivel"
    note: str = ""


IPCA_ITEMS: dict[str, IpcaItem] = {
    i.key: i
    for i in (
        IpcaItem("geral", 7169, "IPCA geral", "referencia"),
        IpcaItem("alimentacao_domicilio", 7171, "Alimentação no domicílio", "referencia", "Supermercado como um todo"),
        IpcaItem("gasolina", 7657, "Gasolina", "combustivel"),
        IpcaItem("frango_inteiro", 107617, "Frango inteiro", "comida"),
        IpcaItem("frango_pedacos", 107618, "Frango em pedaços", "comida"),
        IpcaItem("carne_acem", 7300, "Carne bovina — acém", "comida", "O IPCA não tem 'carne moída'; acém e patinho são os cortes mais usados para moer"),
        IpcaItem("carne_patinho", 7295, "Carne bovina — patinho", "comida", "O IPCA não tem 'carne moída'; acém e patinho são os cortes mais usados para moer"),
        IpcaItem("leite", 12393, "Leite longa vida", "comida"),
        IpcaItem("cafe", 7392, "Café moído", "comida"),
        IpcaItem("pao", 7375, "Pão francês", "comida"),
        IpcaItem("arroz", 7173, "Arroz", "comida"),
        IpcaItem("feijao_carioca", 12222, "Feijão carioca", "comida"),
        IpcaItem("feijao_preto", 7176, "Feijão preto", "comida"),
        IpcaItem("tomate", 7212, "Tomate", "comida"),
        IpcaItem("refeicao_fora", 7434, "Refeição fora de casa", "comida"),
        IpcaItem("energia", 7485, "Energia elétrica residencial", "casa"),
        IpcaItem("gas_botijao", 7482, "Gás de botijão", "casa"),
        IpcaItem("aluguel", 7448, "Aluguel residencial", "casa"),
        IpcaItem("condominio", 7449, "Condomínio", "casa"),
        IpcaItem("agua_esgoto", 7451, "Água e esgoto", "casa"),
        IpcaItem("internet", 107688, "Acesso à internet", "casa"),
        IpcaItem("plano_saude", 7696, "Plano de saúde", "saude"),
        IpcaItem("remedios", 7662, "Remédios (produtos farmacêuticos)", "saude"),
        IpcaItem("onibus", 7628, "Ônibus urbano", "transporte"),
        IpcaItem("app_transporte", 47649, "Transporte por aplicativo", "transporte"),
        IpcaItem("passagem_aerea", 7634, "Passagem aérea", "transporte"),
    )
}
IPCA_ITEM_BY_CODE: dict[int, str] = {i.code: k for k, i in IPCA_ITEMS.items()}
BASE_DATE_IPCA_ITEMS = "2019-12"  # o índice acumulado do painel parte de dez/2019 (= 100)

ANP_BASE = ("https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/"
            "precos-revenda-e-de-distribuicao-combustiveis/shlp/semanal/")
ANP_URL_BRASIL = ANP_BASE + "semanal-brasil-desde-2013.xlsx"
ANP_URL_ESTADOS = ANP_BASE + "semanal-estados-desde-2013.xlsx"
ANP_REFERER = "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos"
ANP_PRODUCT = "GASOLINA COMUM"
ANP_MAX_AGE_DAYS = 3  # a ANP publica 1x por semana; o arquivo (≈13 MB) só é rebaixado se o cache passar disso

UF_BY_ANP_NAME: dict[str, str] = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA", "CEARA": "CE",
    "DISTRITO FEDERAL": "DF", "ESPIRITO SANTO": "ES", "GOIAS": "GO", "MARANHAO": "MA", "MATO GROSSO": "MT",
    "MATO GROSSO DO SUL": "MS", "MINAS GERAIS": "MG", "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR",
    "PERNAMBUCO": "PE", "PIAUI": "PI", "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO", "RORAIMA": "RR", "SANTA CATARINA": "SC", "SAO PAULO": "SP",
    "SERGIPE": "SE", "TOCANTINS": "TO",
}

# Boletim Focus (expectativas anuais do mercado) — BCB Olinda
FOCUS_URL = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais"
FOCUS_INDICATORS: dict[str, str] = {"IPCA": "IPCA (%)", "Selic": "Selic no fim do ano (% a.a.)",
                                    "PIB Total": "Crescimento do PIB (%)", "Câmbio": "Dólar no fim do ano (R$)"}
FOCUS_YEARS_AHEAD = 3  # ano corrente + 2

# --------------------------------------------------------------------------- #
# Pobreza, desigualdade, fome, qualidade do emprego e saúde
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SidraSocial:
    key: str
    url: str
    period_kind: str   # "ano" | "movel" | "trimestre"
    name: str
    unit: str


SIDRA_SOCIAL: dict[str, SidraSocial] = {
    t.key: t
    for t in (
        SidraSocial("gini", f"{_SIDRA}/t/7435/n1/all/v/10681/p/all", "ano",
                    "Índice de Gini da renda domiciliar per capita", "índice 0–1"),
        SidraSocial("inseguranca_alimentar",
                    f"{_SIDRA}/t/9552/n1/all/v/9784/c1/6795/c12404/109098,109099,109100,109101,109102/p/all", "ano",
                    "Domicílios por situação de segurança alimentar (PNAD)", "% dos domicílios"),
        SidraSocial("informalidade", f"{_SIDRA}/t/8513/n1/all/v/12466/p/all", "movel",
                    "Taxa de informalidade (PNAD Contínua)", "%"),
        SidraSocial("posicao_ocupacao",
                    f"{_SIDRA}/t/6320/n1/all/v/4090/c11913/96165,31722,31723,31725,31726,31727,96170,96171,31731/p/all", "movel",
                    "Ocupados por posição na ocupação (PNAD Contínua)", "mil pessoas"),
        SidraSocial("massa_rendimento", f"{_SIDRA}/t/6392/n1/all/v/6293/p/all", "movel",
                    "Massa de rendimento real habitual do trabalho", "R$ milhões"),
        SidraSocial("desalento", f"{_SIDRA}/t/6807/n1/all/v/9869/p/all", "movel",
                    "Percentual de desalentados na força de trabalho ampliada", "%"),
        SidraSocial("mortalidade_infancia", f"{_SIDRA}/t/6695/n1/all/n3/all/v/9731/p/all", "ano",
                    "Mortalidade de menores de 5 anos", "óbitos por mil nascidos vivos"),
    )
}
SIDRA_SOCIAL_SOURCE = {"gini": 7435, "inseguranca_alimentar": 9552, "informalidade": 8513, "posicao_ocupacao": 6320,
                       "massa_rendimento": 6392, "desalento": 6807, "mortalidade_infancia": 6695}
# Categorias (classificação c11913) da tabela 6320
POSICAO_OCUPACAO_LABEL: dict[str, str] = {
    "96165": "Total de ocupados", "31722": "Empregado no setor privado com carteira", "31723": "Empregado no setor privado sem carteira",
    "31725": "Trabalhador doméstico com carteira", "31726": "Trabalhador doméstico sem carteira", "31727": "Setor público (inclui militares)",
    "96170": "Empregador", "96171": "Conta própria", "31731": "Trabalhador familiar auxiliar",
}
# Categorias (classificação c12404) da tabela 9552
SEGURANCA_ALIMENTAR_LABEL: dict[str, str] = {
    "109098": "Com segurança alimentar", "109099": "Insegurança alimentar (total)", "109100": "Insegurança leve",
    "109101": "Insegurança moderada", "109102": "Insegurança grave (fome)",
}

# Ipeadata (OData): pobreza, emprego formal (Novo Caged) e Bolsa Família. ``level`` filtra NIVNOME quando a série tem níveis.
IPEA_SOCIAL: dict[str, dict[str, str | None]] = {
    "pobreza": {"code": "PNADS_PERCPOBRE830", "level": None},          # % da população abaixo de US$ 8,30/dia (PPC 2021)
    "pobreza_extrema": {"code": "PNADS_PERCPOBRE300", "level": None},  # % abaixo de US$ 3,00/dia
    "caged_saldo": {"code": "CAGED12_SALDONAJU12", "level": None},     # saldo mensal de vagas com carteira (ajustado)
    "bolsa_familia": {"code": "VAL_PBF12", "level": "Brasil"},          # valor total pago, R$ mil
}
IPEA_TIMEOUT = (10.0, 120.0)
# Ipeadata com vários níveis (Brasil + Estados): número de benefícios do Bolsa Família e valor total em dezembro (anuais, ~20 MB cada)
IPEA_LEVELS: dict[str, str] = {"pbf_familias": "FAM_PBF", "pbf_valor_dez": "VAL_PBF"}
IPEA_LEVELS_MAX_AGE_DAYS = 30

# Banco Mundial (fonte secundária — OMS/UNICEF/FAO): indicadores nacionais anuais
WORLD_BANK_URL = "https://api.worldbank.org/v2/country/BRA/indicator/{indicator}?format=json&per_page=80"
WORLD_BANK_INDICATORS: dict[str, str] = {
    "vacina_sarampo": "SH.IMM.MEAS", "vacina_dpt": "SH.IMM.IDPT", "expectativa_vida": "SP.DYN.LE00.IN",
    "gasto_saude_pib": "SH.XPD.CHEX.GD.ZS", "gasto_publico_saude_pib": "SH.XPD.GHED.GD.ZS",
}
VACCINE_TARGET = 95.0  # meta de cobertura do Programa Nacional de Imunizações

# Dengue — microdados do SINAN no OpenDataSUS (agregados por semana epidemiológica e UF)
DENGUE_URL = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SINAN/Dengue/csv/DENGBR{yy}.csv.zip"
DENGUE_FIRST_YEAR = 2019
DENGUE_MAX_AGE_DAYS = 3        # só o ano corrente é rebaixado (≈ 15 MB); anos fechados ficam em cache
DENGUE_INCOMPLETE_WEEKS = 3    # as últimas semanas chegam incompletas por atraso de digitação
UF_BY_IBGE_CODE: dict[str, str] = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA", "22": "PI", "23": "CE",
    "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP",
    "41": "PR", "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}

# Códigos candidatos do Ipeadata (OData). Se a API não devolver linhas, o pipeline
# usa o consolidado determinístico — veja sinesp_loader.build_consolidated_fallback.
IPEA_CANDIDATE_SERIES: dict[str, str] = {
    "mvi_taxa": "ATLAS_HOMICIDIO",
    "ideb": "ATLAS_IDEB",
}

# --------------------------------------------------------------------------- #
# Política de inflação (CMN) e recortes de período
# --------------------------------------------------------------------------- #
INFLATION_TARGET: float = 3.0
INFLATION_TOLERANCE_UPPER: float = 4.5
INFLATION_TOLERANCE_LOWER: float = 1.5

PERIOD_OPTIONS: dict[str, str | None] = {
    "Últimos 2 anos": "2y",
    "Desde 2023 (mandato atual)": "2023-01-01",
    "Série histórica completa desde 2019": "2019-01-01",
}

# --------------------------------------------------------------------------- #
# Cores semânticas (daltonismo-safe)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Palette:
    alert: str = "#D9381E"      # piora / risco fiscal / violência
    neutral: str = "#4A5568"    # estabilidade / meta
    institutional: str = "#2B6CB0"
    good: str = "#1B9E77"       # melhora / queda de crime / crescimento sustentável
    vulnerable: str = "#D97706" # vulneráveis / alimentos
    grid: str = "#E2E8F0"
    text: str = "#1A202C"
    muted: str = "#718096"
    band: str = "rgba(43,108,176,0.10)"


PALETTE = Palette()

# Eventos históricos opcionais nos gráficos de séries longas
HISTORICAL_EVENTS: list[dict[str, str]] = [
    {"start": "2020-03-01", "end": "2020-12-31", "label": "Choque da pandemia"},
    {"start": "2021-03-01", "end": "2022-08-31", "label": "Ciclo de alta de juros"},
    {"start": "2023-01-01", "end": "2023-01-31", "label": "Transição de mandato"},
    {"start": "2026-10-01", "end": "2026-10-31", "label": "Eleições 2026"},
]

# --------------------------------------------------------------------------- #
# Textos "Para entender em 30 segundos"
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Explainer:
    what: str
    why: str
    source_label: str
    source_url: str


EXPLAINERS: dict[str, Explainer] = {
    "inflacao": Explainer(
        what="É quanto os preços subiram nos últimos 12 meses. A linha da alimentação mostra só o que pesa no "
             "carrinho do supermercado.",
        why="Quando a comida sobe mais que o resto, o salário compra menos — e as famílias de menor renda "
            "sentem primeiro, porque gastam mais da renda com alimentação.",
        source_label="Banco Central — SGS séries 433 (IPCA) e 1635 (Alimentação)",
        source_url="https://www3.bcb.gov.br/sgspub/",
    ),
    "divida_juros": Explainer(
        what="A dívida bruta é tudo o que o governo deve, comparado com o tamanho da economia (PIB). A Selic é "
             "o juro básico; o juro real é a Selic descontada a inflação.",
        why="Juros altos encarecem a dívida pública e o crédito de quem compra a prazo; dívida alta deixa o "
            "governo com menos folga para obras, saúde e programas sociais sem aumentar impostos.",
        source_label="Banco Central — SGS séries 13762 (Dívida Bruta), 4513 (Dívida Líquida) e 432 (Selic)",
        source_url="https://www3.bcb.gov.br/sgspub/",
    ),
    "desemprego": Explainer(
        what="A desocupação conta quem procura trabalho e não acha. A subutilização soma ainda quem trabalha "
             "menos horas do que gostaria e quem desistiu de procurar.",
        why="É a medida mais honesta da folga no mercado de trabalho: mostra quanta gente quer trabalhar mais "
            "e não consegue.",
        source_label="IBGE — PNAD Contínua, SIDRA tabelas 6381 e 6441",
        source_url="https://sidra.ibge.gov.br/tabela/6381",
    ),
    "renda_endividamento": Explainer(
        what="Mostra o rendimento médio do trabalhador (já corrigido pela inflação) ao lado do percentual da "
             "renda das famílias comprometido com dívidas.",
        why="Se a renda sobe mas as dívidas pesam mais, sobra menos dinheiro para consumir — o orçamento "
            "da casa fica apertado mesmo com emprego.",
        source_label="IBGE — SIDRA tabela 6390 · Banco Central — SGS série 29037",
        source_url="https://sidra.ibge.gov.br/tabela/6390",
    ),
    "recessao": Explainer(
        what="O PIB é a soma de tudo o que o país produz. O IBC-Br é o 'PIB mensal' do Banco Central. "
             "Recessão técnica = dois trimestres seguidos de queda.",
        why="Quando a economia encolhe, as empresas contratam menos e a arrecadação de impostos cai — "
            "o que afeta o emprego e os serviços públicos.",
        source_label="IBGE — SIDRA tabela 5932 · Banco Central — SGS série 24364",
        source_url="https://sidra.ibge.gov.br/tabela/5932",
    ),
    "mvi": Explainer(
        what="Mortes Violentas Intencionais (MVI) somam homicídios, latrocínios, lesão corporal seguida de morte "
             "e mortes por intervenção policial, a cada 100 mil habitantes.",
        why="É o principal termômetro da segurança: permite comparar estados de tamanhos diferentes e ver se "
            "a violência está caindo de verdade.",
        source_label="Fórum Brasileiro de Segurança Pública / Sinesp VDE (Ministério da Justiça)",
        source_url="https://forumseguranca.org.br/anuario-brasileiro-seguranca-publica/",
    ),
    "vulneraveis": Explainer(
        what="Feminicídio é o assassinato de mulheres por serem mulheres. Estupro de vulnerável é violência "
             "sexual contra menores de 14 anos ou pessoas sem condição de consentir.",
        why="São crimes que atingem quem tem menos defesa, em geral dentro de casa. O número de registros "
            "também reflete a coragem de denunciar e a força da rede de proteção.",
        source_label="Fórum Brasileiro de Segurança Pública / Sinesp VDE (Ministério da Justiça)",
        source_url="https://forumseguranca.org.br/anuario-brasileiro-seguranca-publica/",
    ),
    "ideb": Explainer(
        what="O Ideb é a nota (de 0 a 10) da educação básica: junta o aprendizado nas provas e a taxa de "
             "aprovação dos alunos.",
        why="Mede se as crianças estão aprendendo o que precisam para ter bons empregos e renda no futuro — "
            "e se o país está caminhando para as metas do Plano Nacional de Educação.",
        source_label="INEP — Índice de Desenvolvimento da Educação Básica (Ideb)",
        source_url="https://www.gov.br/inep/pt-br/areas-de-atuacao/pesquisas-estatisticas-e-indicadores/ideb",
    ),
    "evasao": Explainer(
        what="O abandono é a proporção de alunos do ensino médio que saem da escola no meio do ano. 'Nem-nem' "
             "são jovens de 15 a 29 anos que não estudam nem trabalham.",
        why="Jovens fora da escola e do emprego têm mais dificuldade de conseguir renda no futuro e ficam mais "
            "expostos à violência.",
        source_label="INEP — Censo Escolar · IBGE — PNAD Contínua Educação",
        source_url="https://www.gov.br/inep/pt-br/areas-de-atuacao/pesquisas-estatisticas-e-indicadores/censo-escolar",
    ),
    "gasolina": Explainer(
        what="É o preço médio que o motorista paga por litro de gasolina comum no posto, apurado toda semana pela ANP "
             "em milhares de postos pelo país.",
        why="A gasolina pesa no orçamento de quem tem carro ou moto e sobe junto no frete: transporte mais caro "
            "encarece o preço de tudo que chega ao supermercado.",
        source_label="ANP — Levantamento de Preços de Combustíveis (síntese semanal)",
        source_url="https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/"
                   "levantamento-de-precos-de-combustiveis-ultimas-semanas-pesquisadas",
    ),
    "contas_casa": Explainer(
        what="Mostra quanto subiram, em 12 meses, as contas fixas da casa: energia, gás de cozinha, aluguel, condomínio, água, "
             "internet, plano de saúde, remédios e transporte. Vem do IPCA do IBGE.",
        why="São gastos difíceis de cortar: quando energia, aluguel ou plano de saúde sobem, sobra menos para a comida e o "
            "resto do orçamento — e muitas vezes o reajuste é mais forte que a inflação geral.",
        source_label="IBGE — IPCA por item, SIDRA tabela 7060",
        source_url="https://sidra.ibge.gov.br/tabela/7060",
    ),
    "cambio": Explainer(
        what="O dólar é o preço da moeda americana em reais. O Boletim Focus reúne o que ~140 instituições financeiras "
             "esperam, toda semana, para inflação, juros, PIB e dólar.",
        why="Dólar alto encarece combustível, trigo, eletrônicos e remédios importados; e a expectativa de inflação influencia "
            "os juros, o preço dos reajustes e o crédito que você consegue.",
        source_label="Banco Central — SGS série 1 (dólar) e Boletim Focus (Olinda)",
        source_url="https://www.bcb.gov.br/publicacoes/focus",
    ),
    "fiscal_resultado": Explainer(
        what="Resultado primário é a diferença entre o que o governo arrecada e gasta, sem contar juros. O nominal soma os "
             "juros da dívida. Aqui, positivo = déficit (gasta mais do que arrecada); negativo = superávit.",
        why="Quando o governo gasta mais do que arrecada e ainda paga juros altos, a dívida cresce — e é a conta que, no "
            "fim, aparece em impostos, juros e menos dinheiro para saúde, escola e segurança.",
        source_label="Banco Central — Necessidades de financiamento do setor público (SGS 5793 e 5727)",
        source_url="https://www.bcb.gov.br/estatisticas/necessidadefinanciamento",
    ),
    "credito": Explainer(
        what="Inadimplência é a parcela do crédito das famílias com mais de 90 dias de atraso. O rotativo do cartão é o juro "
             "cobrado quando a fatura não é paga por inteiro; o cheque especial, quando a conta fica no vermelho.",
        why="Juros de 100% ao ano ou mais fazem uma dívida pequena virar uma bola de neve. Inadimplência alta mostra famílias "
            "sem conseguir pagar as contas.",
        source_label="Banco Central — SGS séries 21084, 22022 e 20741",
        source_url="https://www.bcb.gov.br/estatisticas/reporttxjuros",
    ),
    "salario_minimo": Explainer(
        what="O salário mínimo nacional, e quanto ele realmente compra: o valor de cada ano corrigido pela inflação (IPCA) "
             "para os preços de hoje.",
        why="Ele baliza salários, aposentadorias e benefícios de milhões de brasileiros. Aumento nominal que a inflação "
            "devora não melhora a vida de ninguém — o que conta é o poder de compra.",
        source_label="Banco Central — SGS série 1619 · IBGE/IPCA",
        source_url="https://www3.bcb.gov.br/sgspub/",
    ),
    "pobreza": Explainer(
        what="Mostra que parte da população vive com menos de US$ 8,30 por dia (pobreza) e menos de US$ 3,00 por dia (extrema "
             "pobreza), em dólares de poder de compra — as linhas usadas pelo Banco Mundial. O Gini mede a desigualdade da renda: "
             "0 = todos ganham igual, 1 = uma pessoa fica com tudo.",
        why="É o retrato mais direto de quem não consegue pagar o básico. Quando a pobreza cai, mais famílias comem melhor, "
            "estudam mais e dependem menos de ajuda do governo.",
        source_label="IBGE (PNAD Contínua) via Ipeadata e SIDRA 7435",
        source_url="https://sidra.ibge.gov.br/tabela/7435",
    ),
    "fome": Explainer(
        what="Percentual de domicílios onde a família teve preocupação ou falta de comida em casa. Insegurança grave significa "
             "passar fome: adultos e, muitas vezes, crianças ficaram sem comer.",
        why="Fome é a privação mais básica. Também afeta o desenvolvimento das crianças e o desempenho na escola e no trabalho.",
        source_label="IBGE — PNAD Contínua, módulo de Segurança Alimentar (SIDRA 9552)",
        source_url="https://sidra.ibge.gov.br/tabela/9552",
    ),
    "informalidade": Explainer(
        what="Informal é quem trabalha sem carteira assinada, sem CNPJ ou sem registro. A taxa mostra a parcela dos ocupados "
             "nessa situação. O Caged conta as vagas com carteira criadas a cada mês.",
        why="Quem é informal não tem férias, 13º, FGTS nem seguro-desemprego, e perde a renda de um dia para o outro. "
            "Emprego com carteira dá segurança e acesso a crédito.",
        source_label="IBGE — PNAD Contínua (SIDRA 8513 e 6320) · Ministério do Trabalho — Novo Caged (via Ipeadata)",
        source_url="https://sidra.ibge.gov.br/tabela/8513",
    ),
    "bolsa_familia": Explainer(
        what="Valor total que o governo federal pagou no mês em benefícios do Bolsa Família (bilhões de reais) e, por ano, quantas "
             "famílias recebem o benefício em dezembro e qual o valor médio.",
        why="O programa sustenta a renda de milhões de famílias pobres e movimenta o comércio local. Mudanças no valor total "
            "refletem o número de famílias atendidas e o benefício médio.",
        source_label="Ministério do Desenvolvimento Social — via Ipeadata (VAL_PBF12)",
        source_url="http://www.ipeadata.gov.br/",
    ),
    "dengue": Explainer(
        what="Casos prováveis de dengue notificados ao SUS por semana epidemiológica (domingo a sábado). As semanas mais recentes "
             "ficam incompletas por atraso nos registros, por isso o painel as retira da comparação.",
        why="Epidemias de dengue lotam postos e hospitais, e a doença pode matar. Acompanhar a curva ajuda a saber se o "
            "pico já passou e quais estados estão mais afetados.",
        source_label="Ministério da Saúde — SINAN Dengue (OpenDataSUS)",
        source_url="https://opendatasus.saude.gov.br/",
    ),
    "vacinacao": Explainer(
        what="Percentual de crianças de 12 a 23 meses vacinadas (sarampo e DTP). A meta do Programa Nacional de Imunizações é 95%.",
        why="Abaixo de 95% doenças que estavam controladas, como o sarampo, podem voltar a circular. Vacina protege a criança "
            "e também as pessoas que não podem se vacinar.",
        source_label="OMS/UNICEF via Banco Mundial (fonte secundária)",
        source_url="https://data.worldbank.org/indicator/SH.IMM.MEAS?locations=BR",
    ),
    "leitos": Explainer(
        what="Quantos leitos hospitalares existem para cada mil habitantes: os que atendem pelo SUS e o total (com privados).",
        why="Leito é onde a pessoa é internada quando precisa. Onde há poucos leitos, a espera por internação e cirurgia tende a ser "
            "maior — embora o número de leitos, sozinho, não mostre as filas.",
        source_label="Ministério da Saúde — CNES (leitos), via TabNet/DATASUS",
        source_url="http://tabnet.datasus.gov.br/cgi/deftohtm.exe?cnes/cnv/leiintbr.def",
    ),
    "vacinacao_uf": Explainer(
        what="Percentual de crianças vacinadas em cada estado, para tríplice viral (1ª dose), poliomielite e pentavalente. A meta do "
             "Programa Nacional de Imunizações é 95%.",
        why="Quando a cobertura cai abaixo da meta, doenças como sarampo e poliomielite podem voltar. Ver o estado mostra onde o "
            "risco é maior.",
        source_label="Ministério da Saúde — SI-PNI, via TabNet/DATASUS (dados até 2022)",
        source_url="http://tabnet.datasus.gov.br/cgi/dhdat.exe?bd_pni/cpnibr.def",
    ),
    "mortalidade_infancia": Explainer(
        what="Quantas crianças morrem antes de completar 5 anos a cada mil nascidas vivas, segundo estimativas do IBGE.",
        why="É um dos melhores termômetros da qualidade da saúde, do saneamento e da renda de um lugar: onde há pré-natal, "
            "água tratada e atendimento rápido, menos crianças morrem.",
        source_label="IBGE — SIDRA tabela 6695 (ODS 3.2.1)",
        source_url="https://sidra.ibge.gov.br/tabela/6695",
    ),
    "saude_vida": Explainer(
        what="Expectativa de vida ao nascer (quantos anos, em média, o brasileiro vive) e quanto do PIB o país gasta com saúde, "
             "somando setor público e privado.",
        why="Mostra se o país está conseguindo transformar crescimento em vida mais longa e saudável, e quanto do esforço vem "
            "do Estado (SUS) e quanto sai do bolso das famílias e dos planos.",
        source_label="Banco Mundial (OMS e ONU) — fonte secundária",
        source_url="https://data.worldbank.org/country/brazil",
    ),
    "precos_itens": Explainer(
        what="Mostra quanto o preço de cada item subiu nos últimos 12 meses e quanto custa hoje o que custava R$ 100 "
             "em dezembro de 2019. Vem do IPCA, que o IBGE calcula pesquisando os preços em lojas e mercados.",
        why="A inflação geral esconde diferenças enormes: o tomate pode disparar enquanto o arroz cai. Aqui você vê a "
            "inflação do seu prato, não só a média do país.",
        source_label="IBGE — IPCA por item, SIDRA tabela 7060",
        source_url="https://sidra.ibge.gov.br/tabela/7060",
    ),
}

APP_TITLE: str = "Monitor Brasil — Painel Cidadão de Indicadores Socioeconômicos"
