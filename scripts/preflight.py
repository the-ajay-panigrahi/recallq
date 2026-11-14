import asyncio
from backend import core


async def main():
    try:
        vector = await core.embed("RecallQ setup check")
        print("Local MiniLM embedding OK:", len(vector), "dimensions")
    except core.ServiceError as exc:
        print("Embedding check:", exc.code, exc.message)
    try:
        result = await core.qdrant("", method="GET")
        print(
            "Qdrant read access OK; vectors:",
            result["result"]["config"]["params"]["vectors"],
        )
    except core.ServiceError as exc:
        print("Qdrant check:", exc.code, exc.message)


if __name__ == "__main__":
    asyncio.run(main())
