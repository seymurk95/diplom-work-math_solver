import json
import re
import subprocess
import requests
from pathlib import Path

# ---- настройки ----
PROJECT = Path(r"C:\dev\my_project")
SOLUTIONS_FILE = Path(r"C:\dev\diplom\qwen_solutions.txt")
STATEMENTS_FILE = Path(r"C:\dev\diplom\statements.json")
URL = "http://localhost:1234/v1/chat/completions"
MODEL = "ai-mo.kimina-autoformalizer-7b"
OUT_DIR = PROJECT / "kimina_proofs"
OUT_DIR.mkdir(exist_ok=True)

SYSTEM = "You are an expert in mathematics and Lean 4."
STAT_PREFIX = ("Please autoformalize the following problem in Lean 4 with a header. "
               "Use the following theorem names: my_favorite_theorem.\n\n")


def load_blocks(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    blocks = re.split(r"\n\s*---\s*\n", text)
    return [b.strip() for b in blocks if b.strip()]


def problem_of(block):
    m = re.search(r"Problem:\s*(.*?)\s*\n\s*Solution:", block, re.DOTALL)
    if m:
        return m.group(1).strip()
    return block.split("\n")[0].replace("Problem:", "").strip()


def ask_model(prompt):
    r = requests.post(URL, json={
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "top_p": 0.95,
        "max_tokens": 2048,
    }, timeout=900)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def extract_lean(text):
    blocks = re.findall(r"```[A-Za-z0-9_]*[ \t]*\n(.*?)```", text, re.DOTALL)
    blocks = [b for b in blocks if "theorem" in b]
    return (blocks[-1] if blocks else text).strip()


def lean_compiles(code, name):
    path = OUT_DIR / f"{name}.lean"
    path.write_text(code + "\n", encoding="utf-8")
    p = subprocess.run(["lake", "env", "lean", str(path)], cwd=PROJECT,
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=900)
    out = p.stdout + p.stderr
    return (p.returncode == 0 and "error" not in out.lower()), out


def to_open_statement(code):
    """'... := by sorry' -> '... := by' (для Prover). None, если не получилось."""
    new, n = re.subn(r":=\s*(?:by\s*)?sorry\s*$", ":= by", code.strip())
    return new if n == 1 else None


blocks = load_blocks(SOLUTIONS_FILE)
print("Читаю файл:", SOLUTIONS_FILE)
print("Задач:", len(blocks))

entries = []
for i, block in enumerate(blocks, 1):
    print("=" * 70)
    print(f"[{i}] {block[:150].replace(chr(10), ' ')}...")
    raw = ask_model(STAT_PREFIX + block)
    code = extract_lean(raw)
    print(code)

    ok, out = lean_compiles(code, f"s{i}_statement")
    open_stmt = to_open_statement(code) if ok else None
    if not ok:
        print("РЕЗУЛЬТАТ: код не компилируется")
        print(out[:800])
    elif open_stmt is None:
        print("РЕЗУЛЬТАТ: компилируется, но не удалось убрать sorry в конце")
    else:
        print("РЕЗУЛЬТАТ: утверждение записано и компилируется")

    entries.append({
        "id": i,
        "problem": problem_of(block),
        "solution_block": block,
        "autoformalizer_code": code,
        "compiles": ok,
        "prover_statement": open_stmt,
    })

STATEMENTS_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                           encoding="utf-8")
n_ready = sum(1 for e in entries if e["prover_statement"])
print("=" * 70)
print(f"Готово для Prover: {n_ready} из {len(entries)}. Файл: {STATEMENTS_FILE}")