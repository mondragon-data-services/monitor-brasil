---
name: monitor-brasil-storytelling
description: Regras de Data Storytelling do Monitor Brasil — títulos ativos, paleta semântica, anotações, formatação pt-BR e o bloco "Para entender em 30 segundos". Use ao criar ou alterar qualquer gráfico ou texto do app.
---

# Checklist de um gráfico novo (`src/visualization/charts.py`)

1. **Título ativo com número dos dados**: "Dívida bruta atinge 82,9% do PIB, com variação de +5,6 p.p. em 12 meses".
   Proibido "Gráfico de X (2019–2026)". Subtítulo = o "e daí" (meta, comparação, ressalva).
2. **Cor tem significado** (`config.PALETTE`): alerta/piora `#D9381E`; neutro/meta `#4A5568` e `#2B6CB0`;
   melhora `#1B9E77`; vulneráveis/alimentos `#D97706`. Não use vermelho/verde como único sinal.
3. **Contexto**: inflação sempre com faixa da meta (3,0% ± 1,5 p.p., `theme.add_inflation_band`); séries longas aceitam
   `theme.add_events` (toggle na sidebar).
4. **Limpeza**: `theme.base_layout` (legenda horizontal no topo, sem grade vertical, `separators=",."`).
   Hover em pt-BR: `%{y:.1f}%`, `R$ %{y:,.2f}`.
5. **Dados ausentes**: devolva `theme.empty_figure(msg)`; nunca levante exceção na UI.
6. Embaixo do gráfico: `explainer("<chave>")` (`config.EXPLAINERS`): *o que é* (1 frase sem economês), *por que importa no
   seu dia a dia*, *fonte auditável com link*.
7. Rodapé da aba: `data_section` com tabela + download CSV (`;` e vírgula decimal).

# Linguagem
Cidadão leigo primeiro: sem sigla sem explicação, sem jargão ("pontos percentuais" ok; "spread" não). Números formatados com
`theme.br`, `br_pct`, `br_pp`, `br_money`.

# KPIs
`delta_color="inverse"` quando subir é ruim (inflação, dívida, desemprego, homicídio); `"normal"` quando subir é bom (renda).
