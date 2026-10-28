# Monitor Brasil — Painel Cidadão de Indicadores Socioeconômicos

App Streamlit público e didático sobre inflação/fiscal (BCB), emprego/renda/PIB (IBGE), segurança (Sinesp/FBSP) e
educação (INEP). Especificação original: `docs/meta_prompt_monitor_brasil_mvp.md`.

## Comandos (Windows, venv em `.venv`)
- Rodar: `.venv\Scripts\python.exe -m streamlit run app.py --server.port 8520`
- Sincronizar dados (smoke test com APIs reais): `.venv\Scripts\python.exe scripts\sync_data.py`
- Testes: `.venv\Scripts\python.exe -m pytest -q`
- Compilar: `.venv\Scripts\python.exe -m py_compile app.py`
- Sinesp VDE oficial (gov.br, ~25 MB/ano): `.venv\Scripts\python.exe scripts\sync_data.py --sinesp [--sinesp-years 2024 --force-sinesp]`
  — o servidor dá 403 a HEAD/sem cabeçalhos de navegador; layout real: `uf, municipio, evento, data_referencia, total_vitima`.
- Agente (OpenAI): chave SÓ em `.streamlit/secrets.toml`/Secrets do Cloud — nunca no chat, código ou commit. Teste ao vivo:
  `.venv\Scripts\python.exe scripts\agent_smoke.py`. Código em `src/agent/` (ver README, seção "Agente Monitora Brasil").
- Antes de dar uma tarefa por concluída: pytest verde + `sync_data.py` sem erro + `AppTest` sem exceções (ver skill `monitor-brasil-dados`).

## Arquitetura (Clean Architecture para dados)
`ingestion/` (HTTP + cache Parquet) → `transformation/metrics.py` (funções **puras**) → `visualization/` (Plotly) → `app.py` (UI).
- Ingestão e métricas **não importam Streamlit**; o cache em memória (`@st.cache_data`) vive só em `app.py`.
- Todo fetch passa por `cache.with_disk_fallback`: API ao vivo → grava `data/processed/{chave}.parquet`; se cair, lê o Parquet.
- Contrato de série: DataFrame `date` (datetime) + `value` (float), validado em `contracts.validate_series`.

## Regras que não se quebram
1. **BCB SGS**: sempre com `dataInicial`/`dataFinal`, janelas ≤ 10 anos, headers `Accept` + `User-Agent`. O BCB às vezes
   responde HTTP 200 com HTML de "Requisição inválida" — `http_client.get_json` trata como erro transitório e repete.
2. **SIDRA**: `payload[0]` é cabeçalho, dados em `payload[1:]`; `"..."`, `"-"`, `"X"`, `".."` → `pd.to_numeric(errors="coerce")`.
3. **Inflação 12m = juros compostos**, nunca soma. Juro real = Fisher.
4. **Títulos de gráfico são ativos e calculados dos dados** (nunca "Gráfico de X"). Números no padrão pt-BR (`theme.br*`).
5. Paleta semântica fixa em `config.PALETTE` (daltonismo-safe); legenda horizontal no topo; sem grade vertical.
6. Cada gráfico principal tem o bloco "Para entender em 30 segundos" (`config.EXPLAINERS`) com fonte auditável.
7. **Honestidade dos dados**: o consolidado de segurança/educação (`sinesp_loader.consolidated_rows`) é **aproximado**
   (`qualidade="aproximado"`) e a UI avisa. Nunca apresentar como dado oficial; nunca inventar série por UF — UF só com
   foto de um ano ou com planilha Sinesp enviada.
8. Sem `TODO`/`pass` em código entregue; type hints em todas as funções.

## Preços do dia a dia
- Gasolina: ANP (`ingestion/anp_client.py`), cache com validade de 3 dias; alimentos: IPCA por item (`ipca_itens_client.py`,
  SIDRA 7060). O IBGE só dá VARIAÇÃO — nunca apresentar como preço em reais. Códigos dos itens em `config.IPCA_ITEMS`.
- Itens novos do agente com `core=False` ficam fora da visão geral (mantém o contexto enxuto).

## Social e saúde
- Fontes novas ficam em `ingestion/__init__.py::extra_jobs()` (chaves `soc_*`, `ipea_*`, `wb_*`, `dengue`); app e `sync_data.py` usam a mesma lista.
- SIDRA com dimensões extras: `ibge_client.parse_sidra_dims` (a categoria é a ÚLTIMA dimensão extra). Ipeadata com níveis: `level="Brasil"`.
- TabNet (`ingestion/tabnet_client.py`): lê o formulário, monta o POST com os valores EXATOS do formulário (latin-1) e interpreta o `<PRE>` (CNES) ou o
  link do CSV (SI-PNI). SI-PNI só vai até 2022. Fontes pesadas usam `cache.with_freshness` (cache por validade).
- Dengue: nunca mostrar as últimas 3 semanas (incompletas). Banco Mundial é fonte secundária — dizer isso na tela.
- Catálogo do agente: indicadores anuais de **segurança/educação** vêm do consolidado (`_ANNUAL_FROM_SEC`); os demais anuais têm série própria
  em `build_context` (um laço antigo sobrescrevia todos — não reintroduzir).

## Bolsa Família mensal (ETL manual)
- `ingestion/bolsa_familia_download.py`: baixa o zip mensal do Portal da Transparência, agrega por UF e descarta. NÃO roda no app; use
  `python scripts/sync_data.py --bolsa-familia`. Valide sempre o total do mês contra o Ipeadata `VAL_PBF12`.
- Ao aplicar patches em scripts, cuidado com âncoras contidas no texto novo: reaplicar duplica blocos (já aconteceu em `tools.py`/`context.py`).

## Cuidado com sinais e códigos (já causaram erro)
- Resultado primário/nominal do BCB: **positivo = déficit** (NFSP). Confira nome da série no catálogo
  (`dadosabertos.bcb.gov.br/api/3/action/package_search?q=<codigo>`) antes de usar um código novo — 21082 é inadimplência TOTAL
  (a PF é 21084) e 20742 NÃO é o rotativo do cartão (é 22022).
- Focus: `baseCalculo eq 0`; indicadores `IPCA`, `Selic`, `PIB Total`, `Câmbio`.

## Divergências conhecidas do meta-prompt (APIs reais, verificadas em 26/10/2026)
- Subutilização: tabela **6441** v4118 (a 6438/v4118 devolve HTTP 400).
- PIB: além da 6562 (acum. 4 tri), usamos 6564 (trimestre/trimestre dessaz.) para o detector de recessão.
- Ipeadata OData responde, mas os códigos candidatos de segurança/IDEB vêm vazios → fallback consolidado.

## Agentes e skills do projeto (`.claude/`)
- `monitor-brasil-dados` (sonnet): APIs, ingestão, cache, Parquet, métricas.
- `monitor-brasil-viz` (sonnet): gráficos, storytelling, UX do `app.py`.
- `monitor-brasil-qa` (haiku): roda testes/smoke/AppTest e reporta — não edita código.
- Skills: `monitor-brasil-dados` (como adicionar série/fonte + checklist de verificação) e
  `monitor-brasil-storytelling` (regras de títulos, cores, explicadores).
