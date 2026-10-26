# META-PROMPT DE ARQUITETURA E IMPLEMENTAÇÃO: MONITOR BRASIL (MVP/PoC)

> **INSTRUÇÃO PARA O AGENTE DE IA (CLAUDE CODE / CLOUD CODE / CURSOR):**
> Você está atuando como um **Staff Data Engineer, Principal Data Scientist e Especialista em Data Storytelling**. Sua missão é construir, do zero até a execução funcional e testada, uma aplicação web analítica em **Python + Streamlit** chamada **"Monitor Brasil — Painel Cidadão de Indicadores Socioeconômicos"**.
> Siga rigorosamente todas as especificações arquiteturais, restrições de APIs governamentais brasileiras, padrões de cache em camadas (Medallion Architecture simplificada) e regras de visualização de dados descritas neste documento. Não utilize placeholders genéricos (`TODO` ou `pass`); implemente todo o código funcional e resiliente a falhas.

---

## 1. CONTEXTO E OBJETIVO DO PRODUTO

O objetivo deste sistema é oferecer a qualquer cidadão brasileiro uma plataforma pública, transparente, didática e auditável para acompanhar a evolução real do Brasil em quatro eixos críticos:
1. **Risco Fiscal, Orçamento e Inflação** (Banco Central do Brasil - SGS)
2. **Mercado de Trabalho, Renda e Atividade Econômica** (IBGE - API SIDRA)
3. **Segurança Pública e Crimes contra Vulneráveis** (Ministério da Justiça - Sinesp VDE / Ipeadata)
4. **Educação Básica** (INEP / IDEB e PNAD Educação)

**Público-Alvo:** Cidadãos leigos, jornalistas de dados, pesquisadores e analistas econômicos. O painel deve combinar **rigor estatístico** com **explicações claras em linguagem simples** (Data Storytelling).

---

## 2. STACK TECNOLÓGICA E ARQUITETURA DO PROJETO

### 2.1. Stack Obrigatória
* **Linguagem:** Python 3.11+ (com *Type Hints* estritos em todas as funções).
* **Frontend / App Framework:** `streamlit >= 1.38.0`
* **Processamento e Armazenamento Local (Colunar):** `pandas >= 2.2.0`, `pyarrow >= 16.0.0` (uso obrigatório de `.parquet` para cache persistente local).
* **Validação e Contratos de Dados:** `pydantic >= 2.7.0`
* **Ingestão HTTP & Resiliência:** `requests >= 2.32.0`, `urllib3` (com `Retry` e *Exponential Backoff*), `openpyxl >= 3.1.0`.
* **Visualização Interativa:** `plotly >= 5.22.0` (`plotly.express` e `plotly.graph_objects`).

### 2.2. Estrutura de Diretórios (Clean Architecture para Dados)
Crie exatamente a seguinte árvore de diretórios e arquivos no workspace:

```text
monitor-brasil/
├── .streamlit/
│   └── config.toml                 # Tema visual padronizado, fontes e cores acessíveis
├── data/
│   ├── raw/                        # Arquivos brutos baixados (CSV/XLSX Sinesp, INEP)
│   └── processed/                  # Cache local em formato Apache Parquet (.parquet)
├── src/
│   ├── __init__.py
│   ├── config.py                   # Dicionários de séries SGS, tabelas SIDRA, metadados e cores
│   ├── contracts.py                # Schemas Pydantic para validação de séries temporais
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── http_client.py          # Sessão HTTP com Retry, Timeout e User-Agent customizado
│   │   ├── bcb_client.py           # Extrator da API SGS do Banco Central
│   │   ├── ibge_client.py          # Extrator da API SIDRA do IBGE
│   │   ├── ipea_client.py          # Extrator da API OData do Ipeadata (Segurança/Social)
│   │   └── sinesp_loader.py        # Pipeline ETL para planilhas Sinesp VDE + Fallback resiliente
│   ├── transformation/
│   │   ├── __init__.py
│   │   └── metrics.py              # Cálculos de acumulados 12m, variação real, médias móveis e taxas/100k hab
│   └── visualization/
│       ├── __init__.py
│       ├── theme.py                # Paleta semântica, templates Plotly e anotações históricas
│       └── charts.py               # Componentes gráficos com Data Storytelling embutido
├── scripts/
│   └── sync_data.py                # Script CLI para pré-carregar e salvar dados em data/processed/*.parquet
├── app.py                          # Entrypoint do Streamlit (UI, KPIs, Abas, Filtros e Explicações)
├── requirements.txt                # Dependências com versões fixadas
└── README.md                       # Documentação de execução e dicionário de dados
```

---

## 3. REGRAS CRÍTICAS DE ENGENHARIA DE DADOS (CONTRATOS DE API)

Ao implementar a camada `src/ingestion/`, você **DEVE** respeitar as seguintes peculiaridades técnicas das APIs governamentais brasileiras:

### 3.1. API SGS do Banco Central (`src/ingestion/bcb_client.py`)
* **REGRA CRÍTICA (Mudança de Março/2025):** A API do SGS (`https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados`) **bloqueia requisições sem filtro de data** em séries históricas longas e **exige janela máxima de 10 anos por requisição**.
* **Formato da URL Obrigatório:**
  `https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json&dataInicial={dd/mm/aaaa}&dataFinal={dd/mm/aaaa}`
* **Headers Obrigatórios:** Sempre envie `{"Accept": "application/json", "User-Agent": "MonitorBrasilDataPipeline/1.0"}` para evitar bloqueio por WAF.
* **Séries SGS que devem estar mapeadas em `src/config.py`:**
  * `433`: IPCA — Variação mensal (%) — Inflação Oficial
  * `188`: INPC — Variação mensal (%) — Inflação da Baixa Renda (1 a 5 salários mínimos)
  * `1635`: IPCA — Alimentação e Bebidas (%) — Inflação do Supermercado
  * `432`: Meta para a Taxa Selic (% a.a.)
  * `13762`: Dívida Bruta do Governo Geral (% do PIB — Metodologia Bacen)
  * `4513`: Dívida Líquida do Setor Público (% do PIB)
  * `29037`: Endividamento das famílias com o Sistema Financeiro Nacional (%)
  * `24364`: IBC-Br com ajuste sazonal (Índice de Atividade Econômica do Banco Central — Proxy mensal do PIB)

### 3.2. API SIDRA do IBGE (`src/ingestion/ibge_client.py`)
* **REGRA CRÍTICA DO JSON DO SIDRA:** O retorno da API (`https://apisidra.ibge.gov.br/values/...`) é uma lista de objetos JSON onde **o elemento de índice `0` (`response.json()[0]`) NÃO É DADO, mas sim o cabeçalho descritivo das colunas**. Os dados reais começam obrigatoriamente em `response.json()[1:]`.
* **Tratamento de Valores Nulos/Sigilosos do IBGE:** O IBGE retorna strings como `"..."`, `"-"`, `"X"` ou `".."` quando o dado não está disponível. Converta sempre usando `pd.to_numeric(df["V"], errors="coerce")` e remova `NaN`.
* **Endpoints SIDRA obrigatórios em `src/config.py`:**
  1. **Taxa de Desocupação (Desemprego - PNAD Contínua Trimestral):**
     * Endpoint: `https://apisidra.ibge.gov.br/values/t/6381/n1/all/v/4099/p/last%2040`
  2. **Taxa de Subutilização da Força de Trabalho (Desemprego + Desalento + Subocupação):**
     * Endpoint: `https://apisidra.ibge.gov.br/values/t/6438/n1/all/v/4118/p/last%2040`
  3. **Rendimento Médio Real Habitual de Todos os Trabalhos (R$):**
     * Endpoint: `https://apisidra.ibge.gov.br/values/t/6390/n1/all/v/5933/p/last%2040`
  4. **PIB — Taxa Acumulada em 4 Trimestres (%):**
     * Endpoint: `https://apisidra.ibge.gov.br/values/t/5932/n1/all/v/6562/p/last%2032/c11255/90707`

### 3.3. Segurança Pública e Educação (`src/ingestion/ipea_client.py` & `sinesp_loader.py`)
* **Integração Primária via API OData do Ipeadata (`http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='{codigo}')`):**
  * Busque indicadores históricos anuais/mensais diretamente da API aberta do IPEA sem autenticação.
* **Pipeline Híbrido para Sinesp VDE (Ministério da Justiça) e IDEB (INEP):**
  * Implemente em `sinesp_loader.py` uma função capaz de:
    1. Ler arquivos `.parquet`, `.csv` ou `.xlsx` colocados na pasta `data/raw/` (padrão oficial Sinesp VDE com colunas `uf`, `municipio`, `evento`, `mes_ano`, `total_vitimas` / `total_peso`).
    2. Permitir upload direto via interface Streamlit (`st.sidebar.file_uploader`) que processa, padroniza colunas e salva automaticamente em `data/processed/sinesp_vde.parquet`.
    3. **Fallback Determinístico de Dados Reais Consolidados:** Caso a pasta `data/raw/` esteja vazia na primeira execução, o sistema deve gerar automaticamente um arquivo `data/processed/seguranca_educacao_consolidado.parquet` contendo a série histórica oficial consolidada (2016–2025/2026) do Anuário Brasileiro de Segurança Pública / Sinesp VDE e do INEP/IDEB (nacional e por UF) para os indicadores:
       * Taxa de Mortes Violentas Intencionais (MVI) por $100\text{ mil}$ habitantes;
       * Número absoluto e taxa de Feminicídios por $100\text{ mil}$ mulheres;
       * Registros de Estupro de Vulnerável (vítimas menores de 14 anos ou incapazes);
       * Evolução do IDEB (Anos Iniciais, Anos Finais e Ensino Médio) vs. Meta Nacional e Taxa de Evasão Escolar no Ensino Médio.

### 3.4. Estratégia de Cache Híbrido (Resiliência Offline-First)
Toda função de busca de dados deve implementar um fluxo de **Cache de Duas Camadas**:
1. **Camada 1 (Memória - Streamlit):** Decorador `@st.cache_data(ttl=86400, show_spinner=False)`.
2. **Camada 2 (Disco - Parquet):** Sempre que uma requisição à API do BCB ou IBGE tiver sucesso, salve o DataFrame limpo em `data/processed/{nome_serie}.parquet`. Se a API do governo cair, der timeout ou bloquear a requisição, capture a exceção, leia silenciosamente o arquivo `.parquet` mais recente do disco e exiba um `st.toast` ou aviso discreto informando a data da última sincronização local.

---

## 4. REGRAS DE CIÊNCIA DE DADOS E TRANSFORMAÇÕES ESTATÍSTICAS (`src/transformation/metrics.py`)

Nunca exiba apenas o número bruto sem contexto estatístico. Implemente funções puras e testáveis para calcular:

1. **Inflação Acumulada em 12 Meses (Juros Compostos, NÃO soma simples):**
   $$\text{Acumulado}_{12\text{m}} = \left[ \prod_{i=t-11}^{t} \left(1 + \frac{\text{taxa}_i}{100}\right) - 1 \right] \times 100$$
2. **Juro Real Ex-Post Simplificado (Equação de Fisher):**
   $$\text{Juro Real} = \left( \frac{1 + \frac{\text{Selic}}{100}}{1 + \frac{\text{IPCA}_{12\text{m}}}{100}} - 1 \right) \times 100$$
3. **Taxa Padronizada por $100\text{ mil}$ Habitantes (Segurança Pública):**
   $$\text{Taxa}_{100\text{k}} = \left( \frac{\text{Total de Ocorrências ou Vítimas}}{\text{População Residente}} \right) \times 100.000$$
4. **Média Móvel e Tendência Recente:**
   * Calcule a média móvel de 3 períodos (`rolling(3).mean()`) e a variação em pontos percentuais ($\text{p.p.}$) em relação ao mesmo período do ano anterior (*YoY - Year over Year*).
5. **Regra de Recessão Técnica:**
   * Crie um detector automático que verifica se os últimos 2 trimestres consecutivos de variação trimestral do PIB (ou 6 meses do IBC-Br) foram negativos, emitindo um badge visual de **"Alerta de Recessão Técnica"**, **"Desaceleração"** ou **"Expansão"**.

---

## 5. DIRETRIZES DE DATA STORYTELLING E UX (`src/visualization/`)

Os gráficos devem seguir os princípios de **Edward Tufte** e **Cole Nussbaumer Knaflic (*Storytelling with Data*)**:

### 5.1. Títulos Ativos e Subtítulos Explicativos
* **PROIBIDO** usar títulos passivos como *"Gráfico de IPCA (2019-2026)"*.
* **OBRIGATÓRIO** gerar títulos dinâmicos baseados nos dados mais recentes, por exemplo:
  * *"Inflação de Alimentos acumula X,XX% em 12 meses, pressionando X,X p.p. acima do IPCA Geral"*
  * *"Dívida Bruta atinge XX,X% do PIB, com variação de +X,X p.p. nos últimos 12 meses"*

### 5.2. Paleta Semântica de Cores (Daltonismo-Safe)
Configure em `src/visualization/theme.py`:
* **Alerta / Piora / Risco Fiscal ou Violência:** `#D9381E` (Vermelho Escarlate)
* **Estabilidade / Neutro / Meta:** `#4A5568` (Cinza Ardósia) e `#2B6CB0` (Azul Institucional)
* **Melhora / Queda de Crime / Crescimento Sustentável:** `#1B9E77` (Verde Esmeralda)
* **Destaque de Vulneráveis / Alimentos:** `#D97706` (Âmbar Forte)

### 5.3. Anotações Contextuais e Linhas de Referência (`add_hline` / `add_vrect`)
* Nos gráficos de **Inflação (IPCA)**, adicione sempre uma faixa horizontal (`add_hrect`) ou linhas tracejadas indicando o **Centro da Meta de Inflação (3,0%)** e o **Teto de Tolerância (4,5%)**.
* Nos gráficos de séries temporais longas (2019–2026), permita ativar/desativar marcadores de eventos históricos (ex.: *Choque da Pandemia 2020*, *Ciclo de Alta de Juros*, *Transição de Mandatos 2023 e Eleições 2026*).
* Remova poluição visual (*chartjunk*): desative grades verticais desnecessárias, posicione a legenda no topo horizontal (`orientation="h"`) e formate todos os tooltips (`hovertemplate`) no padrão brasileiro (`R$ 1.234,56` ou `12,3%`).

### 5.4. Componente "Para Entender em 30 Segundos" (Didática Cidadã)
Abaixo de cada gráfico principal, renderize um container estilizado contendo:
* **O que é este indicador?** (Explicação em 1 frase sem economês).
* **Por que isso importa no seu dia a dia?** (Impacto direto no emprego, no preço do mercado, nos impostos ou na segurança da família).
* **Fonte auditável:** Link direto para a tabela original do IBGE/Bacen/Sinesp.

---

## 6. LAYOUT E FUNCIONALIDADES DO `app.py`

Construa a interface principal em `app.py` com a seguinte estrutura:

1. **Sidebar (Painel de Controle e Transparência):**
   * Filtro de Período de Análise (ex.: *Últimos 2 anos*, *Desde 2023 (Mandato Atual)*, *Série Histórica Completa desde 2019*).
   * Botão **"🔄 Forçar Atualização nas APIs do Governo"** (limpa o cache `@st.cache_data` e reexecuta a ingestão).
   * Indicador de Status das Fontes (badges verdes/amarelos mostrando se BCB, IBGE e Sinesp vieram da API ao vivo ou do cache Parquet local).
   * Módulo de Upload de Planilha Sinesp VDE (`.xlsx`/`.csv`) para atualização manual de dados de segurança pública.

2. **Header Executivo — "Termômetro do Brasil Hoje" (Linha de 5 KPI Cards):**
   * Card 1: **IPCA Acumulado 12m (%)** vs. Inflação de Alimentos (com `delta_color="inverse"`, pois subida é ruim).
   * Card 2: **Dívida Bruta (% do PIB)** + Taxa Selic atual (`delta_color="inverse"`).
   * Card 3: **Taxa de Desemprego - PNAD (%)** + Subutilização (`delta_color="inverse"`).
   * Card 4: **Rendimento Médio Real (R$)** (`delta_color="normal"`, pois subida é boa).
   * Card 5: **Taxa de Homicídios (MVI / $100\text{k}$ hab.) e Feminicídios** (`delta_color="inverse"`).

3. **Navegação em 4 Abas Temáticas (`st.tabs`):**
   * **Aba 1: 💰 Inflação, Juros e Risco Fiscal (Orçamento)**
     * Gráfico 1 (Linhas Duplas): *IPCA Geral vs. Inflação de Alimentos no Domicílio (Acumulado 12m)* — mostra como a inflação do supermercado afeta os mais pobres.
     * Gráfico 2 (Área + Linha de Eixo Duplo): *Evolução da Dívida Bruta (% PIB) vs. Taxa Selic e Juro Real* — demonstra o custo de rolagem da dívida e a restrição orçamentária para promessas de governo.
   * **Aba 2: 👷 Emprego, Renda e Risco de Recessão**
     * Gráfico 1 (Barras Agrupadas/Linhas): *Taxa de Desocupação (Desemprego Aberto) vs. Taxa de Subutilização (Desalentados e Subocupados)*.
     * Gráfico 2 (Combinação): *Rendimento Médio Real do Trabalhador (R$) vs. Endividamento das Famílias (%)* — avalia se a renda cresce com folga ou se o consumo está travado por dívidas.
     * Gráfico 3 (Termômetro de Recessão): *Crescimento do PIB (IBGE) e IBC-Br (Banco Central)*.
   * **Aba 3: 🚨 Segurança Pública e Proteção a Vulneráveis**
     * Filtro interativo por Unidade da Federação (UF) ou Brasil Consolidado.
     * Gráfico 1: *Evolução da Taxa de Mortes Violentas Intencionais (MVI) por $100\text{ mil}$ habitantes*.
     * Gráfico 2: *Painel de Crimes contra Vulneráveis: Feminicídios e Estupro de Vulnerável (Série Histórica e Comparativo por UF em Barras Horizontais Ordenadas)*.
   * **Aba 4: 🎓 Educação e Futuro (IDEB e Evasão)**
     * Gráfico 1 (Bullet / Line Chart): *Evolução do IDEB (Ensino Fundamental e Ensino Médio) vs. Metas Oficiais do PNE*.
     * Gráfico 2: *Taxa de Abandono/Evasão Escolar no Ensino Médio e proporção de Jovens "Nem-Nem"*.

4. **Seção de Dados Abertos e Download:**
   * No rodapé de cada aba, inclua um `st.expander("📥 Inspecionar Tabela de Dados Brutos e Baixar CSV")` com botão `st.download_button` para que qualquer cidadão possa auditar os números.

---

## 7. PLANO DE EXECUÇÃO PASSO A PASSO PARA O AGENTE

Execute as tarefas abaixo em ordem sequencial, validando cada etapa no terminal antes de prosseguir:

1. **Passo 1 — Setup do Ambiente:** Crie a árvore de pastas, o arquivo `.streamlit/config.toml` e o `requirements.txt`. Instale os pacotes caso não estejam presentes no ambiente virtual.
2. **Passo 2 — Configuração e Contratos:** Implemente `src/config.py` e `src/contracts.py`.
3. **Passo 3 — Camada de Ingestão e Resiliência:** Implemente `http_client.py`, `bcb_client.py`, `ibge_client.py`, `ipea_client.py` e `sinesp_loader.py` garantindo a criação automática dos arquivos `.parquet` em `data/processed/`.
4. **Passo 4 — Camada de Transformação e Storytelling:** Implemente `src/transformation/metrics.py`, `src/visualization/theme.py` e `src/visualization/charts.py`.
5. **Passo 5 — Interface Principal e CLI de Sincronização:** Implemente `scripts/sync_data.py` e o aplicativo completo em `app.py`.
6. **Passo 6 — Teste Automatizado de Ingestão (Smoke Test):** Execute `python scripts/sync_data.py` no terminal para testar as chamadas reais às APIs do Banco Central e do IBGE, verificando se os arquivos `.parquet` foram gerados sem erro e se a sintaxe do `app.py` compila perfeitamente (`python -m py_compile app.py`).

Comece a implementação imediatamente pelo **Passo 1**.