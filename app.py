from __future__ import annotations

from fastapi import Depends, FastAPI

from auth import AuthContext, verify_api_key

app = FastAPI(title="Server-side SDK API")


@app.get("/v1/auth/verify")
async def auth_verify(context: AuthContext = Depends(verify_api_key)) -> dict:
    """Lightweight verification endpoint used by the SDK client.

    Returns the minimal identity object the client expects: `user_id` and
    `api_key_id`. The endpoint intentionally exposes no sensitive details.
    """
    return {"user_id": str(context.user_id), "api_key_id": str(context.api_key_id)}
