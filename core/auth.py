"""API Token 鉴权依赖 —— Bearer Token + SQLite 校验。"""

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.token_db import get_token, touch_last_used


_bearer = HTTPBearer(auto_error=False)


@dataclass
class TokenInfo:
    """鉴权通过后的 Token 信息。"""
    token: str
    user_id: str
    name: str
    tier: str           # "admin" | "visitor"


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenInfo:
    """校验 Bearer Token：查库 → 吊销 → 过期 → 返回 TokenInfo。"""
    if credentials == None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization 头，格式: Bearer <token>",
        )

    token = credentials.credentials
    row = get_token(token)

    if row == None:
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
    if expires_at != None:
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
