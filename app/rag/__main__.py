"""入库 CLI：python -m app.rag [--reports N] [--seed S]"""
import argparse
import asyncio
import sys

from app.core.winloop import ensure_selector_loop


async def _main() -> None:
    from app.rag import store
    from app.rag.ingest import ingest_corpus

    parser = argparse.ArgumentParser(description="FinRAG 语料入库")
    parser.add_argument("--companies", type=int, default=200)
    parser.add_argument("--years", default="2021,2022,2023,2024,2025")
    parser.add_argument("--seed", type=int, default=20260929)
    args = parser.parse_args()
    years = tuple(int(y) for y in args.years.split(",") if y.strip())

    await store.init_store()
    try:
        stats = await ingest_corpus(n_companies=args.companies, years=years, seed=args.seed)
        print(f"入库完成: {stats}")
    finally:
        await store.close_store()


if __name__ == "__main__":
    ensure_selector_loop()
    asyncio.run(_main())
    sys.exit(0)
