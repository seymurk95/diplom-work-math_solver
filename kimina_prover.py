import json
import re
import subprocess
import time
import requests
from pathlib import Path

# ---- настройки ----
PROJECT = Path(r"C:\dev\my_project")
STATEMENTS_FILE = Path(r"C:\dev\diplom\statements.json")
RESULTS_FILE = Path(r"C:\dev\diplom\prover_results.json")
URL = "http://localhost:1234/v1/chat/completions"
MODEL = "kimina-prover-distill-8b"
OUT_DIR = PROJECT / "kimina_proofs"
OUT_DIR.mkdir(exist_ok=True)

ATTEMPTS = 3               # независимых попыток на задачу
MAX_TOKENS = 8096          # как в карточке модели
USE_SOLUTION_HINT = False  # True: добавить решение Qwen как подсказку
ONLY_IDS = [101]            # например [1, 2], чтобы взять только эти задачи

SYSTEM = "You are an expert in mathematics and Lean 4."
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def build_prompt(entry):
    p = "Think about and solve the following problem step by step in Lean 4."
    p += f"\n# Problem:{entry['problem']}"
    if USE_SOLUTION_HINT:
        p += ("\n# Informal solution (written by another model, may contain mistakes):\n"
              + entry["solution_block"])
    p += f"\n# Formal statement:\n```lean4\n{entry['prover_statement']}\n```\n"
    return p


def ask_prover(prompt):
    r = requests.post(URL, json={
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "top_p": 0.95,
        "max_tokens": MAX_TOKENS,
    }, timeout=3600)
    r.raise_for_status()
    choice = r.json()["choices"][0]
    content = choice["message"].get("content") or ""
    finish = choice.get("finish_reason")
    if "<think>" in content and "</think>" not in content:
        content = ""   # рассуждения не закончились
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content, finish


def last_lean_block(text):
    blocks = re.findall(r"```[A-Za-z0-9_]*[ \t]*\n(.*?)```", text, re.DOTALL)
    blocks = [b for b in blocks if "theorem" in b or "lemma" in b]
    return blocks[-1].strip() if blocks else None


SIG_RE = r"theorem\s+\S+(.*?):=\s*by"


def sig_norm(code):
    m = re.search(SIG_RE, code, re.DOTALL)
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1).replace("λ", "fun"))


def assemble(statement, prover_code):
    """Наше утверждение (из файла) + тело доказательства от Prover."""
    m = re.search(r"theorem\s+\S+.*?:=\s*by", prover_code, re.DOTALL)
    if not m:
        return None
    body = prover_code[m.end():]
    return statement.rstrip() + body


def run_lean(code, name):
    thm_m = re.search(r"theorem\s+(\S+)", code)
    thm = thm_m.group(1) if thm_m else "my_favorite_theorem"
    full = code.rstrip() + f"\n\n#print axioms {thm}\n"
    path = OUT_DIR / f"{name}.lean"
    path.write_text(full, encoding="utf-8")
    p = subprocess.run(["lake", "env", "lean", str(path)], cwd=PROJECT,
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=1800)
    out = p.stdout + p.stderr
    ok = p.returncode == 0 and "error" not in out.lower()
    return ok, out


def axioms_ok(out):
    m = re.search(r"depends on axioms:\s*\[(.*?)\]", out, re.DOTALL)
    if m:
        used = {a.strip() for a in m.group(1).split(",")}
        bad = sorted(used - ALLOWED_AXIOMS)
        return (not bad), bad
    if "does not depend on any axioms" in out:
        return True, []
    return False, ["не удалось прочитать список аксиом"]


entries = json.loads(STATEMENTS_FILE.read_text(encoding="utf-8"))
results = []

for e in entries:
    if ONLY_IDS and e["id"] not in ONLY_IDS:
        continue
    print("=" * 70)
    print(f"[{e['id']}] {e['problem'][:150]}")
    if not e.get("prover_statement"):
        print("ПРОПУСК: для этой задачи нет готового утверждения "
              "(код не скомпилировался на предыдущем этапе)")
        results.append({"id": e["id"], "status": "no_statement"})
        continue
    print("Утверждение:")
    print(e["prover_statement"])

    status = "not_proved"
    for attempt in range(1, ATTEMPTS + 1):
        print(f"--- попытка {attempt}/{ATTEMPTS} ---")
        t0 = time.time()
        content, finish = ask_prover(build_prompt(e))
        dt = time.time() - t0
        raw_path = OUT_DIR / f"s{e['id']}_prover_try{attempt}_raw.txt"
        raw_path.write_text(content, encoding="utf-8")
        print(f"finish_reason = {finish}, время {dt:.0f} с, символов ответа {len(content)}")

        if finish != "stop" or not content:
            print("НЕ ЗАСЧИТАНО: ответ обрезан или пуст "
                  "(рассуждения не уложились в лимит токенов)")
            continue

        prover_code = last_lean_block(content)
        if prover_code is None:
            print("НЕ ЗАСЧИТАНО: в ответе нет блока с Lean-кодом")
            continue

        full = assemble(e["prover_statement"], prover_code)
        if full is None:
            print("НЕ ЗАСЧИТАНО: в коде нет теоремы")
            continue

        if sig_norm(prover_code) != sig_norm(e["prover_statement"]):
            print("Замечание: Prover переписал формулировку теоремы. Проверяю его "
                  "доказательство под ИСХОДНОЕ утверждение.")

        print("Доказательство от Prover (после ':= by'):")
        print(full[len(e["prover_statement"].rstrip()):].strip()[:1500])

        ok, out = run_lean(full, f"s{e['id']}_proof_try{attempt}")
        if not ok:
            print("Lean отклонил доказательство:")
            print(out[:800])
            continue
        good, bad = axioms_ok(out)
        if not good:
            print("НЕ ЗАСЧИТАНО: недопустимые аксиомы или sorry:", bad)
            continue

        print("ВЕРДИКТ: ДОКАЗАНО (Lean принял доказательство, стандартные аксиомы)")
        status = "proved"
        break

    if status != "proved":
        print(f"ВЕРДИКТ: НЕ ДОКАЗАНО за {ATTEMPTS} попыток "
              "(это 'неизвестно', а не 'ответ неверен')")
    results.append({"id": e["id"], "status": status})

RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
print("=" * 70)
print("ИТОГ:", {r["id"]: r["status"] for r in results})