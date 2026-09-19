#!/usr/bin/env python3
"""Diagnose and repair dangling CC Switch Codex OAuth provider bindings.

The script never prints OAuth token material. Mutations require CC Switch to be
stopped, create a timestamped database backup, and only touch one explicitly
selected Codex provider.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cc-switch-dir",
        type=Path,
        default=Path.home() / ".cc-switch",
        help="CC Switch data directory (default: ~/.cc-switch)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("diagnose", help="inspect accounts and Codex bindings without writing")
    detach = sub.add_parser("detach", help="remove one dangling accountId binding")
    detach.add_argument("--provider-id", required=True)
    rebind = sub.add_parser("rebind", help="replace one dangling binding with an explicit LocalId")
    rebind.add_argument("--provider-id", required=True)
    rebind.add_argument("--account-id", required=True)
    return parser.parse_args(argv)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"required file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return value


def load_accounts(auth_path: Path) -> dict[str, dict[str, Any]]:
    store = load_json(auth_path)
    accounts = store.get("accounts")
    if not isinstance(accounts, dict):
        raise RuntimeError("OAuth account store has no valid accounts object")
    if not all(isinstance(key, str) and isinstance(value, dict) for key, value in accounts.items()):
        raise RuntimeError("OAuth account store contains an invalid account entry")
    return accounts


def jwt_claims(token: Any) -> dict[str, Any]:
    if not isinstance(token, str):
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def identity_from_claims(claims: dict[str, Any]) -> tuple[Any, Any, Any]:
    auth = claims.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        auth = {}
    workspace = claims.get("chatgpt_account_id") or auth.get("chatgpt_account_id")
    return claims.get("email"), workspace, claims.get("sub")


def sanitized_accounts(accounts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for local_id, account in sorted(accounts.items()):
        claims = jwt_claims(account.get("id_token"))
        email, workspace, _subject = identity_from_claims(claims)
        result.append(
            {
                "local_id": local_id,
                "email": account.get("email") or email,
                "workspace_id": account.get("chatgpt_account_id") or workspace,
                "ready": bool(account.get("id_token") and account.get("refresh_token")),
            }
        )
    return result


def live_identity(accounts: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    path = Path.home() / ".codex" / "auth.json"
    if not path.is_file():
        return None
    try:
        live = load_json(path)
    except RuntimeError:
        return {"auth_file": str(path), "readable": False, "matched_local_ids": []}
    tokens = live.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    email, workspace, subject = identity_from_claims(jwt_claims(tokens.get("id_token")))
    matches = []
    for local_id, account in accounts.items():
        managed = identity_from_claims(jwt_claims(account.get("id_token")))
        if subject and managed[2] == subject and managed[1] == workspace:
            matches.append(local_id)
    return {
        "auth_file": str(path),
        "readable": True,
        "auth_mode": live.get("auth_mode"),
        "email": email,
        "workspace_id": workspace,
        "matched_local_ids": sorted(matches),
    }


def readonly_connection(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise RuntimeError(f"required database not found: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def database_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA quick_check").fetchone()
    return str(row[0]) if row else "no result"


def codex_providers(connection: sqlite3.Connection, account_ids: set[str]) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, name, is_current, meta FROM providers WHERE app_type='codex' "
        "ORDER BY is_current DESC, name, id"
    ).fetchall()
    result = []
    for provider_id, name, is_current, raw_meta in rows:
        try:
            meta = json.loads(raw_meta or "{}")
        except json.JSONDecodeError:
            result.append(
                {
                    "provider_id": provider_id,
                    "name": name,
                    "is_current": bool(is_current),
                    "status": "invalid_meta_json",
                }
            )
            continue
        binding = meta.get("authBinding")
        if not isinstance(binding, dict) or binding.get("authProvider") != "codex_oauth":
            status = "unbound_or_not_managed"
            account_id = None
        else:
            account_id = binding.get("accountId")
            if not account_id:
                status = "unbound"
            elif account_id in account_ids:
                status = "valid"
            else:
                status = "dangling"
        result.append(
            {
                "provider_id": provider_id,
                "name": name,
                "is_current": bool(is_current),
                "auth_provider": binding.get("authProvider") if isinstance(binding, dict) else None,
                "account_id": account_id,
                "status": status,
            }
        )
    return result


def cc_switch_running() -> bool:
    try:
        if sys.platform == "win32":
            output = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.lower()
            return any(name in output for name in ('"cc-switch.exe"', '"cc switch.exe"'))
        output = subprocess.run(
            ["ps", "-axo", "comm=,args="],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.lower()
        return any(
            "/cc switch.app/contents/macos/cc-switch" in line
            or line.strip().startswith("cc-switch ")
            for line in output.splitlines()
        )
    except OSError:
        return True


def diagnose(cc_switch_dir: Path) -> dict[str, Any]:
    db_path = cc_switch_dir / "cc-switch.db"
    accounts = load_accounts(cc_switch_dir / "codex_oauth_auth.json")
    with readonly_connection(db_path) as connection:
        check = database_check(connection)
        providers = codex_providers(connection, set(accounts))
    return {
        "cc_switch_dir": str(cc_switch_dir),
        "database_check": check,
        "cc_switch_running": cc_switch_running(),
        "accounts": sanitized_accounts(accounts),
        "live_codex_identity": live_identity(accounts),
        "codex_providers": providers,
        "dangling_provider_ids": [
            item["provider_id"] for item in providers if item["status"] == "dangling"
        ],
    }


def mutate(
    cc_switch_dir: Path,
    provider_id: str,
    mode: str,
    account_id: str | None = None,
) -> dict[str, Any]:
    if cc_switch_running():
        raise RuntimeError("CC Switch appears to be running; quit the app and tray process first")
    db_path = cc_switch_dir / "cc-switch.db"
    accounts = load_accounts(cc_switch_dir / "codex_oauth_auth.json")
    if mode == "rebind" and account_id not in accounts:
        raise RuntimeError(
            "account ID is not a local managed account; use accounts[].local_id, not workspace_id"
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = cc_switch_dir / f"cc-switch.db.bak.cc-fix-bug-{stamp}"
    shutil.copy2(db_path, backup_path)
    connection = sqlite3.connect(db_path)
    try:
        before_check = database_check(connection)
        if before_check != "ok":
            raise RuntimeError(f"database quick_check failed before repair: {before_check}")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT name, meta FROM providers WHERE id=? AND app_type='codex'",
            (provider_id,),
        ).fetchone()
        if not row:
            raise RuntimeError(f"Codex provider not found: {provider_id}")
        name, raw_meta = row
        meta = json.loads(raw_meta or "{}")
        binding = meta.get("authBinding")
        if not isinstance(binding, dict) or binding.get("authProvider") != "codex_oauth":
            raise RuntimeError("selected provider is not a managed Codex OAuth binding")
        previous_id = binding.get("accountId")
        if not previous_id:
            raise RuntimeError("selected provider has no dangling binding")
        if previous_id in accounts:
            raise RuntimeError("bound account still exists; refusing to modify a valid binding")

        if mode == "detach":
            binding.pop("accountId", None)
            new_id = None
        elif mode == "rebind":
            binding["accountId"] = account_id
            new_id = account_id
        else:
            raise RuntimeError(f"unsupported mutation mode: {mode}")

        encoded = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
        cursor = connection.execute(
            "UPDATE providers SET meta=? WHERE id=? AND app_type='codex'",
            (encoded, provider_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"unexpected update count: {cursor.rowcount}")
        connection.commit()
        after_check = database_check(connection)
        if after_check != "ok":
            raise RuntimeError(f"database quick_check failed after repair: {after_check}")
        saved = connection.execute(
            "SELECT meta FROM providers WHERE id=? AND app_type='codex'", (provider_id,)
        ).fetchone()
        saved_binding = (json.loads(saved[0]).get("authBinding") or {}) if saved else {}
        if saved_binding.get("accountId") != new_id:
            raise RuntimeError("post-repair verification failed")
        return {
            "result": "success",
            "mode": mode,
            "provider_id": provider_id,
            "provider_name": name,
            "previous_account_id": previous_id,
            "new_account_id": new_id,
            "backup": str(backup_path),
            "database_check_after": after_check,
        }
    except Exception:
        connection.rollback()
        connection.close()
        shutil.copy2(backup_path, db_path)
        raise
    finally:
        try:
            connection.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    directory = args.cc_switch_dir.expanduser().resolve()
    try:
        if args.command == "diagnose":
            result = diagnose(directory)
        elif args.command == "detach":
            result = mutate(directory, args.provider_id, "detach")
        else:
            result = mutate(directory, args.provider_id, "rebind", args.account_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"result": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
