"""Guard against the encoding trap that broke setup.ps1 on Windows.

Windows PowerShell 5.1 (the built-in "Windows PowerShell", not PowerShell 7)
decodes .ps1 files using the system ANSI codepage unless the file carries a
UTF-8 BOM. On a Western install that is cp1252, where a UTF-8 em dash decodes
to 'a<euro>"' - and that trailing character is U+201D, which PowerShell 5.1
accepts as a string delimiter. One em dash in a comment therefore terminates a
string early and the whole script fails to parse.

Two belts, one pair of braces: the scripts stay pure ASCII, AND they carry a
BOM so a future non-ASCII character still decodes correctly.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPTS = sorted((Path(__file__).resolve().parents[1] / "scripts").glob("*.ps1"))
BOM = b"\xef\xbb\xbf"


class TestPowerShellScripts(unittest.TestCase):
    def test_there_are_scripts_to_check(self):
        self.assertTrue(SCRIPTS, "expected at least one .ps1 under scripts/")

    def test_scripts_start_with_utf8_bom(self):
        for path in SCRIPTS:
            with self.subTest(script=path.name):
                self.assertEqual(
                    path.read_bytes()[:3], BOM,
                    f"{path.name} needs a UTF-8 BOM or Windows PowerShell 5.1 will "
                    "decode it as cp1252")

    def test_scripts_are_pure_ascii(self):
        for path in SCRIPTS:
            with self.subTest(script=path.name):
                body = path.read_bytes()[3:]
                offenders = sorted({bytes([b]) for b in body if b > 127})
                self.assertEqual(
                    offenders, [],
                    f"{path.name} contains non-ASCII bytes {offenders}. Use plain "
                    "ASCII (- not em dash, straight quotes not curly).")

    def test_no_curly_quotes_anywhere(self):
        """Belt and braces: PS 5.1 treats these as real string delimiters."""
        for path in SCRIPTS:
            with self.subTest(script=path.name):
                text = path.read_bytes()[3:].decode("utf-8")
                for bad in ("‘", "’", "“", "”"):
                    self.assertNotIn(bad, text)

    def test_quotes_balance_on_every_line(self):
        """A lone double quote is how the original failure presented."""
        for path in SCRIPTS:
            text = path.read_bytes()[3:].decode("utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if line.count('"') % 2:
                    with self.subTest(script=path.name, line=lineno):
                        self.fail(f"{path.name}:{lineno} has an odd number of "
                                  f'double quotes: {line.strip()[:80]}')

    def test_braces_and_parens_balance(self):
        for path in SCRIPTS:
            text = path.read_bytes()[3:].decode("utf-8")
            body = text.split("#>", 1)[-1]  # skip the comment-based help block
            for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
                with self.subTest(script=path.name, pair=opener + closer):
                    self.assertEqual(body.count(opener), body.count(closer))


if __name__ == "__main__":
    unittest.main()
