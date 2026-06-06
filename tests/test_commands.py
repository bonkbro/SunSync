import unittest

from sunshine.sunshine import build_game_command


class BuildGameCommandTests(unittest.TestCase):
    def test_ryubing_quotes_game_id(self) -> None:
        cmd = build_game_command("foo; rm -rf ~", "Ryubing")
        self.assertIsNotNone(cmd)
        # The metacharacters survive only inside a single-quoted argument.
        self.assertIn("'foo; rm -rf ~'", cmd)

    def test_bottles_fallback_quotes_both_fields(self) -> None:
        cmd = build_game_command("game'name", "bottle'name")
        self.assertIsNotNone(cmd)
        self.assertIn("bottles-cli", cmd)
        # shlex.quote escapes embedded single quotes as the '"'"' sequence.
        self.assertIn("'\"'\"'", cmd)


if __name__ == "__main__":
    unittest.main()
