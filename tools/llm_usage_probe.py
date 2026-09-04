"""Ask the LLM endpoint what it will tell us about the key and its limits.

build.nvidia.com has no account-usage API -- no "credits remaining", no
month-to-date token total. What it does answer is: whether the key is live,
which models it may call, what a single call cost in tokens, and whatever
rate-limit headers it chooses to attach. This prints all four, and dumps every
response header so a limit header we don't know about yet still shows up.

    python -m tools.llm_usage_probe            # key check + model list only
    python -m tools.llm_usage_probe --call     # ...plus one real (tiny) call
"""

import json
import sys

import requests

from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

# Anything a provider uses to report quota state. Matched case-insensitively as
# a substring, because the exact spelling differs per provider and per tier.
LIMIT_HINTS = ("ratelimit", "rate-limit", "quota", "retry-after", "x-request-id",
               "credit", "usage", "limit")


def _headers():
    return {"Authorization": f"Bearer {LLM_API_KEY}", "Accept": "application/json"}


def _show_limit_headers(resp):
    hits = {k: v for k, v in resp.headers.items()
            if any(h in k.lower() for h in LIMIT_HINTS)}
    if hits:
        print("  limit-ish headers:")
        for k, v in sorted(hits.items()):
            print(f"    {k}: {v}")
    else:
        print("  limit-ish headers: none returned")
    print(f"  all headers: {sorted(resp.headers.keys())}")


def check_key():
    """GET /models -- cheapest possible proof the key works, and costs no tokens."""
    url = f"{LLM_BASE_URL.rstrip('/')}/models"
    print(f"GET {url}")
    resp = requests.get(url, headers=_headers(), timeout=30)
    print(f"  HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:500]}")
        _show_limit_headers(resp)
        return False
    models = [m.get("id") for m in resp.json().get("data", [])]
    print(f"  key is live; {len(models)} models visible")
    print(f"  configured LLM_MODEL={LLM_MODEL!r} -> "
          f"{'present' if LLM_MODEL in models else 'NOT in list'}")
    _show_limit_headers(resp)
    return True


def probe_call():
    """One deliberately tiny completion, to read `usage` and the live headers."""
    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    body = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    print(f"\nPOST {url}  (model={LLM_MODEL})")
    resp = requests.post(url, headers={**_headers(), "Content-Type": "application/json"},
                         json=body, timeout=180)
    print(f"  HTTP {resp.status_code}")
    _show_limit_headers(resp)
    if resp.status_code != 200:
        print(f"  body: {resp.text[:1000]}")
        return
    data = resp.json()
    print(f"  reply: {data['choices'][0]['message'].get('content','')!r}")
    print(f"  usage: {json.dumps(data.get('usage', {}), indent=4)}")


if __name__ == "__main__":
    if not LLM_API_KEY:
        sys.exit("LLM_API_KEY is not set -- check .env")
    print(f"base_url = {LLM_BASE_URL}")
    print(f"key      = {LLM_API_KEY[:8]}...{LLM_API_KEY[-4:]} ({len(LLM_API_KEY)} chars)\n")
    if check_key() and "--call" in sys.argv:
        probe_call()
