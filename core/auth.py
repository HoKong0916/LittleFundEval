"""API Token 鉴权依赖 —— SQLite 后端，支持多 Token + 过期校验。

用法:
    @app.post("/chat/stream")
    async def chat(..., token_info: TokenInfo = Depends(verify_token)):
        print(token_info.user_id, token_info.tier)
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.token_db import get_token, touch_last_used


_bearer = HTTPBearer(auto_error=False)


@dataclass
class TokenInfo:
    """鉴权通过后的 Token 信息，注入到端点函数中。"""
    token: str
    user_id: str
    name: str
    tier: str           # "admin" | "visitor"


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenInfo:
    """从 Authorization: Bearer <token> 验证 Token 有效性。

    校验链: 提取 → 查库 → 吊销检查 → 过期检查 → 返回 TokenInfo
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization 头，格式: Bearer <token>",
        )

    token = credentials.credentials
    row = get_token(token)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效 Token",
        )

    if row["is_revoked"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 已被吊销",
        )

    expires_at = row["expires_at"]
    if expires_at is not None:
        from datetime import datetime, timezone
        try:
            expiry = datetime.fromisoformat(str(expires_at))
            if datetime.now(timezone.utc).astimezone() > expiry.astimezone():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token 已过期",
                )
        except (ValueError, TypeError):
            pass

    touch_last_used(token)

    return TokenInfo(
        token=token,
        user_id=row["user_id"],
        name=row["name"],
        tier=row["tier"],
    )
