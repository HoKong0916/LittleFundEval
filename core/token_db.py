"""API Token SQLite 持久层 —— 每次操作独立连接，无全局状态。

表结构:
    api_tokens(token, user_id, name, tier, expires_at, is_revoked, created_at, last_used)
"""

import os
import sqlite3
import uuid
from typing import Optional

from config import TOKEN_DB_PATH

SEED_ADMIN_TOKEN = "sk-admin-seed-001"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS api_tokens (
    token       TEXT    PRIMARY KEY,
    user_id     TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    tier        TEXT    NOT NULL DEFAULT 'visitor',
    expires_at  TEXT,
    is_revoked  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_used   TEXT
);
"""


def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(TOKEN_DB_PATH), exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    """打开连接，确保表存在 + 空库种子数据。调用方负责关闭。"""
    _ensure_data_dir()
    conn = sqlite3.connect(TOKEN_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(CREATE_TABLE)

    count = conn.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0]
    if count == 0:
        conn.execute(
            """INSERT INTO api_tokens (token, user_id, name, tier, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (SEED_ADMIN_TOKEN, "admin", "管理员(种子)", "admin", None),
        )
        conn.commit()

    return conn


# ── 公共接口 ────────────────────────────────────────────────

TokenRow = dict[str, object]


def get_token(token: str) -> TokenRow | None:
    """按 token 主键查询一行记录，返回 dict 或 None。"""
    with _get_conn() as db:
        row = db.execute("SELECT * FROM api_tokens WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def touch_last_used(token: str) -> None:
    """更新 token 的最后使用时间为当前本地时间。"""
    with _get_conn() as db:
        db.execute(
            "UPDATE api_tokens SET last_used = datetime('now', 'localtime') WHERE token = ?",
            (token,),
        )
        db.commit()


def create_token(user_id: str, name: str, tier: str = "visitor",
                 expires_at: Optional[str] = None) -> str:
    """创建新 token 并持久化，返回生成的 token 字符串。"""
    token = f"sk-{user_id}-{uuid.uuid4().hex[:6]}"
    with _get_conn() as db:
        db.execute(
            """INSERT INTO api_tokens (token, user_id, name, tier, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token, user_id, name, tier, expires_at),
        )
        db.commit()
    return token


def list_tokens() -> list[TokenRow]:
    """列出所有 token，按创建时间倒序返回。"""
    with _get_conn() as db:
        rows = db.execute("SELECT * FROM api_tokens ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def revoke_token(token: str) -> bool:
    """软删除：将 token 标记为已吊销。返回是否命中。"""
    with _get_conn() as db:
        cursor = db.execute("UPDATE api_tokens SET is_revoked = 1 WHERE token = ?", (token,))
        db.commit()
        return cursor.rowcount > 0


def delete_token(token: str) -> bool:
    """物理删除 token 记录。返回是否命中。"""
    with _get_conn() as db:
        cursor = db.execute("DELETE FROM api_tokens WHERE token = ?", (token,))
        db.commit()
        return cursor.rowcount > 0
