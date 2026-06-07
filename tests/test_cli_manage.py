import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import sunsync


class ListCommandTests(unittest.TestCase):
    @mock.patch("sunsync.get_existing_apps_full")
    def test_list_prints_sorted(self, apps) -> None:
        apps.return_value = [
            {"index": 2, "name": "Zelda"},
            {"index": 0, "name": "Apex"},
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sunsync._print_app_list()
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertLess(out.index("Apex"), out.index("Zelda"))

    @mock.patch("sunsync.get_existing_apps_full", return_value=[])
    def test_list_empty(self, _apps) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sunsync._print_app_list()
        self.assertEqual(rc, 0)
        self.assertIn("No apps", buf.getvalue())


class RemoveCommandTests(unittest.TestCase):
    @mock.patch("sunsync.delete_app", return_value=(True, ""))
    @mock.patch("sunsync.get_existing_apps_full")
    def test_remove_found(self, apps, delete) -> None:
        apps.return_value = [{"index": 5, "name": "Doom"}]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sunsync._remove_app_by_name("Doom")
        self.assertEqual(rc, 0)
        delete.assert_called_once_with(5)
        self.assertIn("Removed", buf.getvalue())

    @mock.patch("sunsync.delete_app")
    @mock.patch("sunsync.get_existing_apps_full", return_value=[{"index": 1, "name": "Other"}])
    def test_remove_not_found(self, _apps, delete) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sunsync._remove_app_by_name("Missing")
        self.assertEqual(rc, 1)
        delete.assert_not_called()

    @mock.patch("sunsync.delete_app", return_value=(False, "boom"))
    @mock.patch("sunsync.get_existing_apps_full", return_value=[{"index": 3, "name": "Game"}])
    def test_remove_api_error(self, _apps, _delete) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sunsync._remove_app_by_name("Game")
        self.assertEqual(rc, 1)
        self.assertIn("boom", buf.getvalue())


class ArgParsingTests(unittest.TestCase):
    def test_remove_requires_name(self) -> None:
        args = sunsync.parse_args(["remove", "My Game"])
        self.assertEqual(args.command, "remove")
        self.assertEqual(args.name, "My Game")

    def test_list_command(self) -> None:
        self.assertEqual(sunsync.parse_args(["list"]).command, "list")


if __name__ == "__main__":
    unittest.main()
