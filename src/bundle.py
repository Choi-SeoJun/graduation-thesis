"""Create a private, self-contained server handoff; never include article text or Git credentials."""
import hashlib
import json
import os
from zipfile import ZipFile, ZIP_DEFLATED

from src.fetch_market import ROOT, MARKET, SYMBOLS
from src.prepare import NEWS_DAILY, PREPARED
from src.rights import load_sessions
from src.server_train import read_panel


def main():
    for symbol in SYMBOLS:
        panel = read_panel(symbol,5)
        if not any(r["news_status"] == "complete" for r in panel) or len(load_sessions(symbol)) != len(panel):
            raise ValueError(f"Incomplete prepared panel: {symbol}")
    files = [ROOT/"requirements-server.txt", ROOT/"research/dividends.psv", ROOT/"README.md",
             ROOT/"졸업논문 연구설계.md", ROOT/"선행연구 검토.md",
             ROOT/"docs/data-notes.md", ROOT/"docs/pretraining.md", ROOT/"docs/monthly-prices.png",
             ROOT/"docs/input-fingerprints.json",
             MARKET/"raw/KOSPI.csv", MARKET/"exchange/audit.json", MARKET/"sources.json",
             PREPARED/"folds.csv", PREPARED/"inventory.json", NEWS_DAILY]
    files.extend(sorted((ROOT/"src").glob("*.py")))
    files.extend(sorted((ROOT/"tests").glob("test_*.py")))
    files.extend(sorted((ROOT/"research").glob("*.json")))
    for symbol in SYMBOLS:
        files.extend((PREPARED/f"{symbol}.csv", MARKET/"exchange"/f"{symbol}.csv",
                      MARKET/"actions"/f"{symbol}.json"))
    dest = ROOT/"data/local/server-handoff.zip"
    temp = dest.with_suffix(".tmp")
    hashes = {}
    with ZipFile(temp,"w",compression=ZIP_DEFLATED) as archive:
        for path in files:
            entry = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            hashes[entry] = hashlib.sha256(content).hexdigest()
            archive.writestr(entry,content)
        snapshot = json.loads((ROOT/"docs/input-fingerprints.json").read_text(encoding="utf-8"))
        if any(hashes.get(k) != v for k,v in snapshot.items()):
            raise ValueError("Inputs differ from the published QA report; regenerate and review it")
        archive.writestr("SHA256.json",json.dumps(hashes,indent=2,sort_keys=True))
    with ZipFile(temp) as archive:
        if archive.testzip() is not None or any(hashlib.sha256(archive.read(k)).hexdigest()!=v for k,v in hashes.items()):
            raise IOError("Damaged bundle")
    os.replace(temp,dest)
    print(f"{dest}: {len(files)} verified files. SHA256 {hashlib.sha256(dest.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
