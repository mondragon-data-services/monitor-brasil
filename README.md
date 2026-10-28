# 🇧🇷 Monitor Brasil — Painel Cidadão de Indicadores Socioeconômicos

Painel público em **Python + Streamlit** com quatro eixos: inflação/juros/dívida (Banco Central), emprego/renda/PIB (IBGE),
segurança pública (Sinesp VDE / FBSP) e educação (INEP). Cada gráfico tem título ativo calculado dos dados e um bloco
"Para entender em 30 segundos" com fonte auditável.

## Executar (Windows)
```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\sync_data.py          # opcional: pré-carrega os Parquet
.venv\Scripts\python.exe -m streamlit run app.py
```
Testes: `.venv\Scripts\python.exe -m pytest -q`

## Como funciona
```
APIs (BCB SGS, IBGE SIDRA, Ipeadata) ──► ingestion/ ──► data/processed/*.parquet ──► metrics ──► charts ──► app.py
Planilha Sinesp (data/raw ou upload) ─┘                    (cache em disco)           (puras)    (Plotly)   (Streamlit)
```
- **Cache em 2 camadas**: `@st.cache_data(ttl=24h)` em memória + Parquet em disco. Se a API cair, o app usa o último
  Parquet e mostra um aviso com a data da sincronização. A barra lateral mostra o status de cada fonte (🟢 ao vivo /
  🟡 cache / 🔴 indisponível).
- **BCB SGS**: sempre com datas, janelas ≤ 10 anos, `User-Agent` próprio; resposta HTML com HTTP 200 é tratada como erro e repetida.
- **IBGE SIDRA**: o item `[0]` do JSON é cabeçalho; `"..."`, `"-"`, `"X"`, `".."` viram NaN e são removidos.

## Dicionário de dados
| Chave | Fonte | Código | Unidade |
|---|---|---|---|
| `ipca_mensal` | BCB SGS | 433 | % a.m. |
| `inpc_mensal` | BCB SGS | 188 | % a.m. |
| `ipca_alimentacao` | BCB SGS | 1635 | % a.m. |
| `selic_meta` | BCB SGS | 432 | % a.a. |
| `divida_bruta` | BCB SGS | 13762 | % do PIB |
| `divida_liquida` | BCB SGS | 4513 | % do PIB |
| `endividamento_familias` | BCB SGS | 29037 | % |
| `ibcbr` | BCB SGS | 24364 | índice (dessaz.) |
| `desocupacao` | IBGE SIDRA | tabela 6381 / var 4099 | % |
| `subutilizacao` | IBGE SIDRA | tabela **6441** / var 4118 | % |
| `rendimento_real` | IBGE SIDRA | tabela 6390 / var 5933 | R$ |
| `pib_4tri` | IBGE SIDRA | tabela 5932 / var 6562 | % (acum. 4 tri) |
| `pib_trimestral` | IBGE SIDRA | tabela 5932 / var 6564 | % (tri/tri dessaz.) |
| `seguranca_educacao` | Sinesp VDE / FBSP / INEP / IBGE | consolidado | ver abaixo |
| `anp_gasolina` | ANP — síntese semanal de preços (Brasil + 27 UFs) | `semanal-brasil/estados-desde-2013.xlsx` | R$/litro (gasolina comum), semanal |
| `ipca_itens` | IBGE SIDRA | tabela 7060 / var 63 | variação mensal % por item (desde jan/2020) |

Indicadores do consolidado (`seguranca_educacao_consolidado.parquet`): `mvi_taxa`, `mvi_total`, `feminicidio_total`,
`feminicidio_taxa`, `estupro_vulneravel_total`, `ideb_*` e `ideb_meta_*` (anos iniciais/finais/ensino médio),
`abandono_ensino_medio`, `nem_nem`.

Métricas (`src/transformation/metrics.py`): acumulado 12m por juros compostos, juro real (Fisher), taxa por 100 mil hab.,
média móvel, variação YoY em p.p. e detector de recessão técnica (2 trimestres negativos).

## 🛒 Preços do dia a dia (aba "Preços do Dia a Dia")
- **Gasolina por estado e Brasil** (R$/litro, semanal): planilhas oficiais da ANP. O arquivo de estados tem ~13 MB, então o
  resultado fica em `data/processed/anp_gasolina.parquet` e só é rebaixado após 3 dias (a ANP publica 1x por semana); o botão
  "Forçar atualização" ignora essa validade. O site da ANP exige cabeçalhos de navegador.
- **Alimentos (frango, carne, leite, café, pão, arroz, feijão, tomate)**: IPCA por item do IBGE (SIDRA 7060). **O IBGE publica a
  variação de preço, não o preço em reais**; por isso o painel mostra a inflação acumulada em 12 meses, a evolução no tempo e
  "quanto custa hoje o que custava R$ 100 em dez/2019". Só a gasolina tem preço em reais.
- **Carne moída** não existe no IPCA: usamos acém e patinho (cortes mais usados para moer). Feijão: carioca e preto. Frango: inteiro
  e em pedaços.
- O agente de IA responde sobre esses dados (`get_fuel_by_uf`, `get_price_ranking`) e explica que alimentos só têm variação.

## ⚠️ Limitações honestas
- **Segurança pública (Sinesp VDE oficial)**: o botão **⬇️ Baixar Sinesp VDE oficial** (ou
  `python scripts/sync_data.py --sinesp`) baixa direto do gov.br as planilhas anuais `bancovde-{ano}.xlsx`
  (~25 MB cada), reduz cada uma a `uf × evento × mês` e grava `data/processed/sinesp_vde_{ano}.parquet` — a planilha
  bruta é descartada. Anos fechados ficam em cache; o ano corrente é rebaixado após 7 dias. O servidor do Ministério
  responde 403 a HEAD e a clientes sem cabeçalhos de navegador; o downloader já trata isso. Só entram **anos completos**
  (12 meses); mortes violentas = homicídio doloso + latrocínio + lesão corporal seguida de morte + morte por
  intervenção de agente do Estado. Taxas por 100 mil usam a população do **Censo 2022** em todos os anos (pode diferir
  ~1–4% do FBSP). Também aceita upload manual (`.xlsx`/`.csv`/`.parquet` com `uf`, `evento`, `data_referencia`,
  `total_vitima`) ou arquivos em `data/raw/`.
- **Sem o Sinesp baixado**, o app usa um **consolidado de referência aproximado** (compilado de publicações do Anuário
  FBSP, INEP e IBGE; `qualidade="aproximado"`), com aviso na tela. A taxa de feminicídio por 100 mil mulheres, o Ideb, o
  abandono escolar e o "nem-nem" continuam vindo desse consolidado aproximado (não estão no Sinesp).
- **Ipeadata**: a API responde, mas os códigos candidatos de segurança/IDEB retornam séries vazias; por isso a fonte é opcional.
- O meta-prompt indicava SIDRA 6438/v4118 para subutilização, que hoje responde HTTP 400; usa-se a tabela 6441.

## 🤖 Agente Monitora Brasil (OpenAI)
Aba que responde perguntas **somente** sobre os dados e gráficos do painel ("Como está a segurança pública?",
"Como está o endividamento?", "Como está o PIB?"). Código em `src/agent/`.

**Por que não é RAG vetorial.** Os dados são séries numéricas estruturadas e pequenas. Embeddings recuperam trechos
"parecidos" e perdem datas, defasagens e variações — é assim que um LLM inventa números. O agente usa **recuperação
estruturada com function calling** (Responses API): o modelo só escolhe qual consulta fazer
(`get_overview`, `get_indicator_snapshot`, `get_time_series`, `get_security_by_uf`, `explain_topic`) e os números
(último valor, variação em 12 meses, mínimo/máximo, séries, manchetes dos gráficos) são **calculados em código** sobre um
catálogo fixo de 21 indicadores. Menos custo, nenhum embedding, respostas verificáveis.

**Camadas de segurança**
| Camada | O que faz |
|---|---|
| Chave | só em `st.secrets`/variável de ambiente; nunca no código, logs, UI ou resposta; `secrets.toml` está no `.gitignore` |
| Limite | **5 perguntas por sessão (fixo) e 5 por dia por IP** — a cota segue o IP, então F5 ou nova aba **não** zera —, mais disjuntor global de perguntas e de tokens por dia, intervalo mínimo entre perguntas (também por IP) e uma pergunta em andamento por sessão. O IP é guardado só como hash |
| Entrada | normalização Unicode, remoção de caracteres invisíveis, máx. 400 caracteres, detecção de injeção PT/EN **antes** do modelo (recusa local, sem gastar API) |
| Prompt | instruções claras, pergunta do usuário delimitada e tratada como dado não confiável, regras invioláveis, recusa padrão, exemplos |
| Ferramentas | somente leitura, argumentos validados contra listas fechadas (enum, `strict`), nenhum texto livre chega aos dados, no máx. 5 rodadas / 8 chamadas |
| Escopo | resposta sem consulta aos dados vira recusa padrão (o modelo não "conversa solto") |
| Saída | remove links, imagens, HTML e código; bloqueia vazamento do prompt (canário) e de chaves |
| Erros | mensagens genéricas; detalhes da API nunca chegam ao usuário |

**Onde colocar a chave (nunca no chat nem no Git)**
- Local: edite `.streamlit/secrets.toml` → `OPENAI_API_KEY = "sk-..."` (arquivo ignorado pelo Git; modelo em `secrets.toml.example`).
- Streamlit Cloud: *App → ⋮ → Settings → Secrets* → cole `OPENAI_API_KEY = "sk-..."` → Save.
- Recomendado: crie uma **chave de projeto dedicada** no painel da OpenAI, com **limite mensal de gasto** (a proteção
  definitiva de dinheiro) e, se possível, restrinja os modelos permitidos. Se a chave vazar, revogue-a imediatamente.
- Opcionais em secrets: `OPENAI_MODEL` (padrão `gpt-5-mini`), `AGENT_ENABLED=false` (desliga na hora),
  `AGENT_CLIENT_DAILY`, `AGENT_GLOBAL_DAILY`, `AGENT_TOKENS_DAILY`, `AGENT_COOLDOWN_S`.

**Limites honestos.** Os contadores ficam na memória do processo (zeram se o app reiniciar). O IP vem do cabeçalho
`X-Forwarded-For` (usa a entrada pública mais à direita, que o cliente não consegue forjar; IPs privados/proxies internos
são ignorados). Redes com IP compartilhado (CGNAT de operadoras, empresas) dividem a mesma cota de 5 — ajuste
`AGENT_CLIENT_DAILY` se isso incomodar. Se nenhum IP público for detectável, vale só o limite por sessão + os globais; para
conferir em produção, ative `AGENT_DEBUG = true` em Secrets (mostra "IP público detectado: sim/não" no agente). O teto real
de gasto são os limites globais + o limite de gasto da OpenAI. Cada pergunta usa ~6–10 mil
tokens (centavos). Teste ao vivo: `python scripts/agent_smoke.py`.

## Deploy no Streamlit Community Cloud
1. Acesse <https://share.streamlit.io> e entre com a conta GitHub `mondragon-data-services`.
2. **Create app → Deploy a public app from GitHub**:
   - Repository: `mondragon-data-services/monitor-brasil` · Branch: `main` · Main file path: `app.py`
   - **Advanced settings → Python version: 3.12** (o `pandas 3` exige Python ≥ 3.11).
3. Não há segredos nem variáveis de ambiente: todas as fontes (BCB, IBGE, Ipeadata) são APIs públicas.

Notas de operação:
- `requirements.txt` traz as dependências com versão fixa; o tema e as opções do servidor ficam em `.streamlit/config.toml`.
- Os Parquet de `data/processed/` vão versionados de propósito: são o *fallback* quando uma API estiver fora do ar e o
  que o app mostra no primeiro acesso. O disco do Streamlit Cloud é efêmero — o cache em disco é recriado a cada
  reinício, e o `@st.cache_data` (24 h) cuida do resto.
- Planilhas Sinesp enviadas pela barra lateral não são persistidas entre reinícios (e `data/raw/` não é versionado).
- O workflow `.github/workflows/tests.yml` roda o `pytest` a cada push/PR.

## Licença
MIT — veja [LICENSE](LICENSE). Especificação original do projeto em [docs/](docs/meta_prompt_monitor_brasil_mvp.md).
