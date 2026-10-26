"""CLI: pré-carrega todas as fontes e grava ``data/processed/*.parquet``.

Uso:  python scripts/sync_data.py            # baixa BCB, IBGE, Ipeadata e monta segurança/educação
      python scripts/sync_data.py --no-ipea  # pula o Ipeadata
      python scripts/sync_data.py --sinesp   # baixa as planilhas oficiais do Sinesp VDE (~25 MB/ano)
      python scripts/sync_data.py --sinesp --sinesp-years 2024 2025 --force-sinesp
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
    parser.add_argument("--sinesp", action="store_true", help="baixa e processa o Sinesp VDE oficial (gov.br)")
    parser.add_argument("--sinesp-years", type=int, nargs="+", metavar="ANO", help="anos do Sinesp (padrão: todos)")
    parser.add_argument("--force-sinesp", action="store_true", help="rebaixa mesmo anos já em cache")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR, format="%(levelname)s %(message)s")

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    if args.sinesp:
        from src.ingestion.sinesp_download import sync_sinesp_vde  # import tardio: puxa o leitor de planilhas

        report = sync_sinesp_vde(args.sinesp_years, force=args.force_sinesp,
                                 progress=lambda msg, frac: print(f"[{frac:4.0%}] {msg}", flush=True))
        print(f"\n{'ano':<6}{'status':<14}{'linhas':>7}  detalhe")
        for y in report.years:
            print(f"{y.year:<6}{y.status:<14}{y.rows:>7}  {y.detail}")
        print(f"Sinesp VDE combinado: {report.combined_rows} linhas\n")
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
