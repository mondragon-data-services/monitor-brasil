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

Indicadores do consolidado (`seguranca_educacao_consolidado.parquet`): `mvi_taxa`, `mvi_total`, `feminicidio_total`,
`feminicidio_taxa`, `estupro_vulneravel_total`, `ideb_*` e `ideb_meta_*` (anos iniciais/finais/ensino médio),
`abandono_ensino_medio`, `nem_nem`.

Métricas (`src/transformation/metrics.py`): acumulado 12m por juros compostos, juro real (Fisher), taxa por 100 mil hab.,
média móvel, variação YoY em p.p. e detector de recessão técnica (2 trimestres negativos).

## ⚠️ Limitações honestas
- **Segurança e educação**: sem planilha oficial, o app usa um **consolidado de referência aproximado** (compilado de
  publicações do Anuário FBSP, INEP e IBGE; `qualidade="aproximado"`), com aviso na tela. Séries nacionais 2016–2024; o
  recorte por UF é uma foto de 2023 da taxa de MVI. Para dados oficiais e série por UF, envie a planilha **Sinesp VDE**
  (`.xlsx`/`.csv`/`.parquet` com `uf`, `evento`, `mes_ano`, `total_vitimas`) pela barra lateral ou coloque-a em `data/raw/`.
- **Ipeadata**: a API responde, mas os códigos candidatos de segurança/IDEB retornam séries vazias; por isso a fonte é opcional.
- O meta-prompt indicava SIDRA 6438/v4118 para subutilização, que hoje responde HTTP 400; usa-se a tabela 6441.

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
