"""
LLM first-token latency under a fail-fast-and-retry strategy.

    python -m tools.llm_latency_bench

A scratch benchmark, kept because the number it produces is the reason
grading abandons a hung request at BUDGET seconds and retries rather than
waiting on it. It is not part of the pipeline and nothing imports it.

IT SPENDS REAL TOKENS. It reads the live LLM_API_KEY and issues 10 streaming
completions across 5 workers. Run it when you mean to, never on import --
which is why the benchmark body now sits behind main() rather than at module
level, where it used to fire the moment anything touched this file.
"""

import time, requests, statistics, concurrent.futures as cf
from backend import config as C
url = C.LLM_BASE_URL.rstrip('/') + "/chat/completions"
h = {"Authorization": f"Bearer {C.LLM_API_KEY}", "Content-Type": "application/json"}
BUDGET = 55          # abandon if no first token by here
RETRIES = 5

def once(i, budget):
    """One attempt. Returns seconds to first token, or None if it hung."""
    p = {"model": C.LLM_MODEL,
         "messages":[{"role":"user","content":f"Write exactly 150 words about topic {i}."}],
         "max_tokens": 300, "temperature": 0.2, "stream": True}
    if C.LLM_REASONING_EFFORT: p["reasoning_effort"] = C.LLM_REASONING_EFFORT
    t = time.time()
    try:
        with requests.post(url, headers=h, json=p, timeout=(10, budget), stream=True) as r:
            if r.status_code != 200: return None
            for line in r.iter_lines():
                if line: return time.time() - t          # first token -> alive
    except Exception:
        return None
    return None

def graded(i):
    """Fail-fast + immediate retry, the proposed strategy."""
    t = time.time()
    for a in range(RETRIES):
        if once(i, BUDGET) is not None:
            return time.time() - t, a + 1
    return None, RETRIES

def main():
    print(f"strategy: abandon at {BUDGET}s, retry immediately, {RETRIES} attempts, 5 workers\n")
    t = time.time()
    with cf.ThreadPoolExecutor(5) as ex:
        rs = list(ex.map(graded, range(10)))
    wall = time.time() - t
    ok = [(d, a) for d, a in rs if d]
    for i, (d, a) in enumerate(rs):
        print(f"  #{i}: {'%.0fs' % d if d else 'FAILED':>8}  attempts={a}")
    if ok:
        ds = [d for d, _ in ok]
        print(f"\n  success {len(ok)}/10 | median {statistics.median(ds):.0f}s | max {max(ds):.0f}s")
        print(f"  attempts needed: {[a for _, a in ok]}")
    print(f"  WALL {wall:.0f}s for 10 candidates -> {wall/10:.0f}s per candidate effective")


if __name__ == "__main__":
    main()
