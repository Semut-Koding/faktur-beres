"""``npm run rag:indeks`` and ``npm run rag:cari -- "<kueri>"``."""

import argparse
import sys
from collections.abc import Sequence

from faktur_beres.config import get_settings
from faktur_beres.db.connection import app_url, make_engine
from faktur_beres.db.policy_store import PolicyStore
from faktur_beres.llm import usage
from faktur_beres.llm.embedding import gemini_embedder
from faktur_beres.llm.embedding_contract import EMBEDDING_DIMENSIONS
from faktur_beres.tools.policy_rag import index_policy, search_policy


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npm run rag:*", description=__doc__)
    commands = parser.add_subparsers(dest="perintah", required=True)
    commands.add_parser("indeks")
    commands.add_parser("cari").add_argument("kueri")
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = make_engine(app_url(settings))
    handler = usage.attach(f"rag-{args.perintah}")
    try:
        store = PolicyStore(engine)
        embedder = gemini_embedder(settings)
        if args.perintah == "indeks":
            report = index_policy(store, embedder)
            print(
                f"Potongan: {report.chunks} ({EMBEDDING_DIMENSIONS} dimensi). Di-embed: "
                f"{report.embedded_chunks} potongan, {report.embedded_queries} kueri alasan. "
                f"Dihapus: {report.removed}. Panggilan: {len(report.calls)}."
            )
        else:
            for hit in search_policy(store, embedder, args.kueri, 3).hits:
                print(f"{hit.score}  {hit.ref}  {hit.title}: {hit.quote}")
    finally:
        usage.detach(handler)
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
