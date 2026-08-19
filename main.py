import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx

app = FastAPI()
ENDPOINT = "https://api.openai.com"

@app.post("/v1/{path:path}")
async def proxy(path: str, request: Request):
    body = await request.body()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{ENDPOINT}/v1/{path}",
            content=body,
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            }
        )
    return JSONResponse(status_code=r.status_code, content=r.json())