"""Create a private, self-contained server handoff; never include article text or Git credentials."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from src.fetch_market import ROOT, MARKET, SYMBOLS
from src.prepare import PREPARED
from src.server_setup import MANIFEST, verify


def bundle_files(snapshot):
    files = {ROOT / name for name in snapshot}
    files.update((ROOT / "requirements-server.txt", ROOT / "README.md",
                  ROOT / "졸업논문 연구설계.md", ROOT / "선행연구 검토.md", MANIFEST,
                  ROOT / "docs/monthly-prices.png", PREPARED / "folds.csv", PREPARED / "inventory.json"))
    for folder, patterns in (("src", ["*.py"]), ("tests", ["test_*.py"]), ("docs", ["*.md"]),
                             ("research", ["*.json", "*.psv", "*.csv", "*.md", "*.py"])):
        for pattern in patterns:
            files.update((ROOT / folder).glob(pattern))
    for symbol in SYMBOLS:
        files.update((PREPARED / f"{symbol}.csv", MARKET / "exchange" / f"{symbol}.csv",
                      MARKET / "actions" / f"{symbol}.json"))
    # Preserve optional collection diagnostics when building the original offline handoff.
    for path in (MARKET / "exchange/audit.json", MARKET / "sources.json"):
        if path.is_file():
            files.add(path)
    return sorted(files)


def write_entry(archive, name, content):
    entry = ZipInfo(name)  # The frozen archive is independent of packaging time.
    entry.compress_type = ZIP_DEFLATED
    entry.create_system = 3
    entry.external_attr = 0o100644 << 16
    archive.writestr(entry, content)


def main():
    snapshot = json.loads(MANIFEST.read_text(encoding="utf-8"))
    verify(snapshot)
    files = bundle_files(snapshot)
    dest = ROOT / "data/local/server-handoff.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=dest.parent, prefix="server-handoff-", suffix=".tmp", delete=False) as handle:
        temp = Path(handle.name)
    try:
        hashes = {}
        with ZipFile(temp, "w") as archive:
            for path in files:
                entry = path.relative_to(ROOT).as_posix()
                content = path.read_bytes()
                hashes[entry] = hashlib.sha256(content).hexdigest()
                write_entry(archive, entry, content)
            if any(hashes.get(k) != v for k, v in snapshot.items()):
                raise ValueError("Training inputs changed during packaging")
            write_entry(archive, "SHA256.json", json.dumps(hashes, indent=2, sort_keys=True))
        with ZipFile(temp) as archive:
            if archive.testzip() is not None or any(hashlib.sha256(archive.read(k)).hexdigest() != v for k,v in hashes.items()):
                raise IOError("Damaged bundle")
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
    print(f"{dest}: {len(files)} verified files. SHA256 {hashlib.sha256(dest.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
