from pathlib import Path
import shutil

ROOT = Path(".")
EXTS = {".py", ".html", ".css", ".js", ".txt"}

def looks_broken(text: str) -> bool:
    markers = ["", "", "", "", "\x9d", "", "", "", "Men", "Secci", "Producci"]
    return any(m in text for m in markers)

def repair_text(text: str) -> str:
    candidates = []

    # Caso tpico: UTF-8 ledo como latin1/cp1252
    for enc in ("latin1", "cp1252"):
        try:
            fixed = text.encode(enc, errors="ignore").decode("utf-8", errors="ignore")
            candidates.append(fixed)
        except Exception:
            pass

    # Elige el que reduzca ms los marcadores rotos
    best = text
    best_score = sum(best.count(x) for x in ["", "", "", ""])
    for c in candidates:
        score = sum(c.count(x) for x in ["", "", "", ""])
        if score < best_score:
            best = c
            best_score = score
    return best

fixed_files = []

for path in ROOT.rglob("*"):
    if path.suffix.lower() not in EXTS:
        continue
    if any(part in {"venv", "__pycache__", ".git", "node_modules"} for part in path.parts):
        continue

    try:
        raw = path.read_bytes()
    except Exception:
        continue

    text = None
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            pass

    if text is None or not looks_broken(text):
        continue

    repaired = repair_text(text)

    if repaired != text:
        backup = path.with_suffix(path.suffix + ".bak_encoding")
        if not backup.exists():
            shutil.copy2(path, backup)
        path.write_text(repaired, encoding="utf-8", newline="")
        fixed_files.append(str(path))

print(f"Archivos reparados: {len(fixed_files)}")
for f in fixed_files:
    print(f)