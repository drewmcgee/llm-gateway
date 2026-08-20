import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone

import db as db_module

MODELS = ["gpt-5-mini", "gpt-4o", "gpt-4o-mini"]
STATUSES = [200, 200, 200, 200, 400, 401, 500]


async def main(count):
    conn = await db_module.connect()
    now = datetime.now(timezone.utc)
    try:
        for i in range(count):
            model = random.choice(MODELS)
            status = random.choice(STATUSES)
            prompt_tokens = random.randint(5, 50)
            completion_tokens = random.randint(10, 300)
            created_at = (now - timedelta(seconds=count - i)).isoformat()
            await db_module.insert_log(
                conn,
                created_at=created_at,
                method="POST",
                url="http://localhost:8000/v1/chat/completions",
                request_headers={"content-type": "application/json"},
                request_body=f'{{"model": "{model}", "messages": []}}'.encode(),
                status_code=status,
                response_headers={"content-type": "application/json"},
                response_body=b'{"id": "chatcmpl-seed"}',
                ttfb_ms=random.uniform(50, 900),
                total_ms=random.uniform(100, 2000),
                source="upstream",
                api_key_label="seed-data",
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            )
    finally:
        await conn.close()
    print(f"Inserted {count} synthetic log rows.")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    asyncio.run(main(count))
