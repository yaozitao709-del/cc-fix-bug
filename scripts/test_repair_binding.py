#!/usr/bin/env python3

from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest import mock

import repair_binding


class RepairBindingTests(unittest.TestCase):
    def fixture(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "codex_oauth_auth.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "accounts": {
                        "local-new": {
                            "email": "person@example.test",
                            "chatgpt_account_id": "workspace-not-local",
                            "refresh_token": "secret",
                            "id_token": "secret",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        connection = sqlite3.connect(root / "cc-switch.db")
        connection.execute(
            "CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, "
            "is_current INTEGER, meta TEXT)"
        )
        connection.execute(
            "INSERT INTO providers VALUES (?, 'codex', 'OpenAI Official', 1, ?)",
            (
                "codex-official",
                json.dumps(
                    {
                        "providerType": "codex_oauth",
                        "authBinding": {
                            "source": "managed_account",
                            "authProvider": "codex_oauth",
                            "accountId": "local-old",
                        },
                    }
                ),
            ),
        )
        connection.commit()
        connection.close()
        return temp, root

    def test_diagnose_marks_missing_local_id_as_dangling(self) -> None:
        temp, root = self.fixture()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(repair_binding, "cc_switch_running", return_value=False):
            report = repair_binding.diagnose(root)
        self.assertEqual(report["database_check"], "ok")
        self.assertEqual(report["dangling_provider_ids"], ["codex-official"])
        self.assertEqual(report["accounts"][0]["local_id"], "local-new")
        self.assertEqual(report["accounts"][0]["workspace_id"], "workspace-not-local")

    def test_detach_only_removes_dangling_account_id(self) -> None:
        temp, root = self.fixture()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(repair_binding, "cc_switch_running", return_value=False):
            result = repair_binding.mutate(root, "codex-official", "detach")
        self.assertEqual(result["result"], "success")
        connection = sqlite3.connect(root / "cc-switch.db")
        meta = json.loads(connection.execute("SELECT meta FROM providers").fetchone()[0])
        connection.close()
        self.assertNotIn("accountId", meta["authBinding"])
        self.assertEqual(meta["authBinding"]["authProvider"], "codex_oauth")
        self.assertTrue(Path(result["backup"]).is_file())

    def test_rebind_requires_local_id_not_workspace_id(self) -> None:
        temp, root = self.fixture()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(repair_binding, "cc_switch_running", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not a local managed account"):
                repair_binding.mutate(
                    root,
                    "codex-official",
                    "rebind",
                    "workspace-not-local",
                )

    def test_rebind_uses_explicit_existing_local_id(self) -> None:
        temp, root = self.fixture()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(repair_binding, "cc_switch_running", return_value=False):
            result = repair_binding.mutate(
                root,
                "codex-official",
                "rebind",
                "local-new",
            )
        self.assertEqual(result["new_account_id"], "local-new")
        connection = sqlite3.connect(root / "cc-switch.db")
        meta = json.loads(connection.execute("SELECT meta FROM providers").fetchone()[0])
        connection.close()
        self.assertEqual(meta["authBinding"]["accountId"], "local-new")

    def test_refuses_to_change_valid_binding(self) -> None:
        temp, root = self.fixture()
        self.addCleanup(temp.cleanup)
        connection = sqlite3.connect(root / "cc-switch.db")
        meta = json.loads(connection.execute("SELECT meta FROM providers").fetchone()[0])
        meta["authBinding"]["accountId"] = "local-new"
        connection.execute("UPDATE providers SET meta=?", (json.dumps(meta),))
        connection.commit()
        connection.close()
        with mock.patch.object(repair_binding, "cc_switch_running", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "still exists"):
                repair_binding.mutate(root, "codex-official", "detach")


if __name__ == "__main__":
    unittest.main()
