---
name: monitor-brasil-dados
description: Como adicionar ou corrigir uma série/fonte de dados no Monitor Brasil (BCB SGS, IBGE SIDRA, Ipeadata, Sinesp) e o checklist de verificação em loop. Use ao mexer em src/ingestion, src/config.py ou metrics.py.
---

# Adicionar uma série

1. **Sondar a API real** (PowerShell `Invoke-WebRequest` ou `requests`) — confirme código, janela e formato:
   - SGS: `https://api.bcb.gov.br/dados/serie/bcdata.sgs.{cod}/dados/ultimos/2?formato=json` (teste rápido) e depois com
     `dataInicial`/`dataFinal`. HTTP 200 com HTML = "Requisição inválida" transitória → repetir.
   - SIDRA: `https://apisidra.ibge.gov.br/values/t/{tabela}/n1/all/v/all/p/last%201` lista as variáveis da tabela.
   - Ipeadata: `http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='{cod}')` — lista vazia = código errado.
2. Registre em `src/config.py` (`SGS_SERIES`, `SIDRA_TABLES` ou `IPEA_CANDIDATE_SERIES`) com nome, unidade e fonte.
3. Carregue via `get_bcb_series` / `get_ibge_series` (já fazem cache Parquet + fallback). Não crie novo caminho de fetch.
4. Transformação nova = função **pura** em `metrics.py` + teste em `tests/test_metrics.py`.
5. Parser novo = teste com payload fixo em `tests/test_ingestion.py` (sem rede).

# Loop de verificação (repetir até verde)

```
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\sync_data.py        # BCB + IBGE precisam sair "ao vivo" ou "cache"
.venv\Scripts\python.exe -c "from streamlit.testing.v1 import AppTest; at=AppTest.from_file('app.py',default_timeout=120).run(); print(at.exception, at.error)"
```

# Armadilhas conhecidas
- SIDRA `payload[0]` é cabeçalho. Período `movel` = `AAAAMM`; `trimestre` = `AAAA0Q` (mês = Q×3).
- Séries SGS diárias (Selic 432) → `metrics.monthly_last` antes de juntar com séries mensais.
- Datas futuras do SGS são descartadas em `bcb_client.fetch_sgs`.
- Dados de segurança/educação aproximados: `qualidade="aproximado"` + aviso na UI; Sinesp enviado → `"oficial"`.
