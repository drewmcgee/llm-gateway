import asyncio
import sys

import db as db_module


async def main(label):
    conn = await db_module.connect_keys()
    try:
        raw_key = await db_module.create_api_key(conn, label)
    finally:
        await conn.close()
    print(raw_key)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python create_key.py <label>", file=sys.stderr)
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1]))
