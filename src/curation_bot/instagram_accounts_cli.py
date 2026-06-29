from __future__ import annotations

import argparse
import json

from .instagram_accounts import AccountConfigError, get_active_account, list_accounts, set_active_account


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    set_cmd = sub.add_parser("set-active", help="Set the active Instagram account profile id for future login/automation.")
    set_cmd.add_argument("--account-id", required=True, help="Local account profile id, e.g. test, main, backup")
    sub.add_parser("current", help="Show current active Instagram account profile id.")
    sub.add_parser("list", help="List local Instagram account profile ids known on this VM.")
    args = parser.parse_args()

    try:
        if args.command == "set-active":
            ref = set_active_account(args.account_id)
            print(json.dumps({"active_account_id": ref.account_id, "profile_dir": str(ref.profile_dir)}, indent=2))
            return 0
        if args.command == "current":
            ref = get_active_account()
            print(json.dumps({"active_account_id": ref.account_id, "profile_dir": str(ref.profile_dir)}, indent=2))
            return 0
        if args.command == "list":
            print(json.dumps({"accounts": list_accounts()}, indent=2))
            return 0
    except AccountConfigError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, indent=2))
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
