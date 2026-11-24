import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src import server_setup


class TestFrozenSetup(unittest.TestCase):
    def test_changed_remote_index_leaves_previous_file_intact(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            market = root / "data/local/market"
            index = market / "raw/KOSPI.csv"
            index.parent.mkdir(parents=True)
            index.write_bytes(b"previous local copy")
            expected = {"data/local/market/raw/KOSPI.csv": hashlib.sha256(b"frozen version").hexdigest()}
            with patch.object(server_setup, "ROOT", root), patch.object(server_setup, "MARKET", market), \
                    patch.object(server_setup, "naver_bars", return_value=([], "unused", "unused")):
                with self.assertRaisesRegex(ValueError, "different KOSPI snapshot"):
                    server_setup.restore(expected)
            self.assertEqual(index.read_bytes(), b"previous local copy")

    def test_source_event_dates_survive_restoration(self):
        with TemporaryDirectory() as folder, patch.object(server_setup, "MARKET", Path(folder)):
            server_setup.restore_actions()
            audit = json.loads(server_setup.AUDITED_ACTIONS.read_text(encoding="utf-8"))
            for symbol, evidence in audit["symbols"].items():
                restored = json.loads((Path(folder) / "actions" / f"{symbol}.json").read_text(encoding="utf-8"))
                self.assertEqual([r["date"] for r in restored], [r["date"] for r in evidence["dividend_ex_dates"]])
                self.assertTrue(all(r["kind"] == "dividends" for r in restored))
