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
