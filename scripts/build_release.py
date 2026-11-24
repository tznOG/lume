"""Build a clean distributable, excluding data, credentials and environments."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
destination = ROOT / "dist"
destination.mkdir(exist_ok=True)
filename = destination / "Lume-SDR.zip"
files = [p for p in ROOT.iterdir() if p.is_file() and (p.suffix in (".txt", ".md", ".py", ".bat", ".yaml") or p.name in ("Dockerfile", ".dockerignore", ".env.example", ".gitignore"))]
for folder in ("app", "scripts", "tests", "docs"):
    files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".key"))
with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, "Lume-SDR/" + path.relative_to(ROOT).as_posix())
digest = hashlib.sha256(filename.read_bytes()).hexdigest()
(destination / "SHA256.txt").write_text(f"{digest}  {filename.name}\n", encoding="utf-8")
print(f"Package: {filename}\nFiles: {len(files)}\nSHA256: {digest}")
