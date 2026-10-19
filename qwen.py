import re
import requests
from pathlib import Path

# ---- Настройки ----
URL = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen3.8-4b-distill"
OUT_FILE = Path(r"C:\dev\diplom\qwen_solutions.txt")
MAX_TOKENS = 6000

SYSTEM_PROMPT = """You are an expert mathematician and step-by-step problem solver.
Your task is to solve the given math problem strictly adhering to the exact format below.

Format:
Problem: <State the problem clearly>

Solution:
Step 1. <First intermediate algebraic step or equality>
Step 2. <Second intermediate algebraic step or equality>
...
Answer: <Final exact answer>

Rules:
1. Write each solution step as a clear, formal intermediate equality or logical transition so that it can be directly converted into a Lean 4 `have` statement.
2. Do not output any introductory or concluding conversational text, markdown banners, or extra explanations outside of the requested template.
3. Keep mathematical steps rigorous, explicit, and minimalist.
"""

TEST_PROBLEMS = [
    "Find the derivative of f(x) = x^2 * sin x.",
    
]


def ask_qwen(problem_statement: str):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Solve this problem:\n{problem_statement}"},
        ],
        "temperature": 0.6,
        "max_tokens": MAX_TOKENS,
    }
    response = requests.post(URL, json=payload, timeout=1800)
    response.raise_for_status()
    choice = response.json()["choices"][0]
    msg = choice["message"]

    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    finish = choice.get("finish_reason")

    # если рассуждения пришли прямо в тексте ответа, вырезаем их
    if "<think>" in content and "</think>" not in content:
        reasoning, content = content, ""          # рассуждения не закончились
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content, len(reasoning), finish


def is_complete(content: str, finish) -> bool:
    return finish == "stop" and "Solution:" in content and "Answer:" in content


def main():
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    good_blocks = []
    failed = []

    print(f"Генерация решений для {len(TEST_PROBLEMS)} задач через Qwen...")
    print("=" * 70)

    for i, problem in enumerate(TEST_PROBLEMS, 1):
        print(f"[{i}/{len(TEST_PROBLEMS)}] {problem}")
        try:
            content, reasoning_len, finish = ask_qwen(problem)
        except Exception as e:
            print(f"Ошибка запроса: {e}")
            failed.append(i)
            continue

        print(f"finish_reason = {finish}, символов рассуждений = {reasoning_len}, "
              f"символов ответа = {len(content)}")
        print("--- Ответ Qwen ---")
        print(content if content else "(пусто)")
        print("=" * 70)

        if is_complete(content, finish):
            good_blocks.append(content)
        else:
            print("НЕПОЛНЫЙ ОТВЕТ: в файл не записан")
            failed.append(i)

    OUT_FILE.write_text("\n\n---\n\n".join(good_blocks), encoding="utf-8")
    print(f"\nУспешно: {len(good_blocks)}, с проблемами: {len(failed)} {failed}")
    print(f"Файл: {OUT_FILE}")


if __name__ == "__main__":
    main()