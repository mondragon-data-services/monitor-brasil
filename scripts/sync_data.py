"""CLI: pré-carrega todas as fontes e grava ``data/processed/*.parquet``.

Uso:  python scripts/sync_data.py            # baixa BCB, IBGE, Ipeadata e monta segurança/educação
      python scripts/sync_data.py --no-ipea  # pula o Ipeadata
Código de saída: 0 se todas as fontes essenciais (BCB + IBGE) têm dados; 1 caso contrário.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DATA_PROCESSED, SGS_SERIES, SIDRA_TABLES  # noqa: E402
from src.ingestion import sync_all  # noqa: E402

ICON = {"live": "OK  ao vivo", "cache": "!!  cache  ", "fallback": "--  consolid.", "missing": "XX  FALHOU  "}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-ipea", action="store_true", help="não consulta o Ipeadata")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR, format="%(levelname)s %(message)s")

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    results = sync_all(include_ipea=not args.no_ipea)

    print(f"{'fonte':<26}{'status':<14}{'linhas':>7}  último dado")
    for key, res in results.items():
        last = ""
        if res.ok and "date" in res.df.columns:
            last = str(res.df["date"].max().date())
        elif res.ok and "ano" in res.df.columns:
            last = str(int(res.df["ano"].max()))
        print(f"{key:<26}{ICON[res.mode]:<14}{len(res.df):>7}  {last}  {res.detail if res.mode != 'live' else ''}")

    essential = [*SGS_SERIES, *SIDRA_TABLES]
    missing = [k for k in essential if not results[k].ok]
    files = sorted(p.name for p in DATA_PROCESSED.glob("*.parquet"))
    print(f"\n{len(files)} arquivos Parquet em {DATA_PROCESSED}")
    if missing:
        print(f"ERRO: sem dados para {missing}")
        return 1
    print("Sincronização concluída.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
