"""API Token 管理 CLI。

用法:
    python manage_token.py create --user-id bob --name "张三" [--tier admin] [--expires 2027-06-01]
    python manage_token.py list
    python manage_token.py revoke <token>
    python manage_token.py delete <token>
"""

import argparse
import sys

from core.token_db import create_token, list_tokens, revoke_token, delete_token


def cmd_create(args: argparse.Namespace) -> None:
    token = create_token(
        user_id=args.user_id,
        name=args.name,
        tier=args.tier,
        expires_at=args.expires,
    )
    print(f"\n  ✅ Token 已生成: {token}")
    print(f"  user_id: {args.user_id}")
    print(f"  name:    {args.name}")
    print(f"  tier:    {args.tier}")
    print(f"  过期:    {args.expires or '永不过期'}\n")


def cmd_list(args: argparse.Namespace) -> None:
    tokens = list_tokens()
    if not tokens:
        print("  (无 Token)")
        return

    print(f"\n  {'Token':<32} {'用户':<12} {'名称':<10} {'Tier':<8} {'过期':<20} {'状态'}")
    print(f"  {'='*90}")
    for t in tokens:
        status = "吊销" if t["is_revoked"] else "有效"
        expires = t["expires_at"] or "永不过期"
        print(f"  {t['token']:<32} {t['user_id']:<12} {t['name']:<10} {t['tier']:<8} {expires:<20} {status}")
    print()


def cmd_revoke(args: argparse.Namespace) -> None:
    ok = revoke_token(args.token)
    if ok:
        print(f"\n  ✅ 已吊销: {args.token}\n")
    else:
        print(f"\n  ❌ Token 不存在: {args.token}\n")
        sys.exit(1)


def cmd_delete(args: argparse.Namespace) -> None:
    ok = delete_token(args.token)
    if ok:
        print(f"\n  ✅ 已删除: {args.token}\n")
    else:
        print(f"\n  ❌ Token 不存在: {args.token}\n")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="API Token 管理")
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="签发新 Token")
    p_create.add_argument("--user-id", required=True, help="用户标识")
    p_create.add_argument("--name", required=True, help="人类可读名称")
    p_create.add_argument("--tier", default="visitor", choices=["admin", "visitor"])
    p_create.add_argument("--expires", default=None, help="过期时间 (ISO 8601)，如 2027-06-01T00:00:00，不填则永不过期")

    # list
    sub.add_parser("list", help="列出所有 Token")

    # revoke
    p_revoke = sub.add_parser("revoke", help="吊销 Token（软删除）")
    p_revoke.add_argument("token", help="要吊销的 Token 字符串")

    # delete
    p_delete = sub.add_parser("delete", help="硬删除 Token")
    p_delete.add_argument("token", help="要删除的 Token 字符串")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "revoke":
        cmd_revoke(args)
    elif args.command == "delete":
        cmd_delete(args)


if __name__ == "__main__":
    main()
