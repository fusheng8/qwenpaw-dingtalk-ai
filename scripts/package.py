"""Build a small, deterministic ZIP with an explicit source allowlist."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / "plugin.json").read_text())
version = manifest["version"]
out = ROOT / "dist"
out.mkdir(exist_ok=True)
files = ["plugin.json", "__init__.py", "plugin.py", "channel.py", "state.py", "transport.py", "routes.py",
         "ui/index.js", "cards/dingtalk-ai-card.json", "README.md", "LICENSE"]
archive = out / f"dingtalk-ai-{version}.zip"
with ZipFile(archive, "w", compression=ZIP_DEFLATED) as z:
    for name in sorted(files):
        info = ZipInfo(name, date_time=(2026, 9, 21, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        z.writestr(info, (ROOT / name).read_bytes())
card = out / "dingtalk-ai-card.json"
card.write_bytes((ROOT / "cards/dingtalk-ai-card.json").read_bytes())
(out / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in [archive, card]))
print(archive)
