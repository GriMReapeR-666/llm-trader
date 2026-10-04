"""PU-0145: production-faithful provider probes via OpenAI SDK.
- Cerebras: SDK chat + models (distinguishes 402 account vs 1010 UA block)
- NVIDIA:   payload-size ladder 1K..200K chars (is 403 body-size-triggered?)
- TokenRouter: SDK chat (4th provider viability)
- OpenRouter: /api/v1/key status (free tier vs credits)
No secret values printed.
"""
import json
import os
import time


def load_env(path=".env"):
    env = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
from openai import OpenAI  # noqa: E402  (same SDK the router uses)


def mask(k):
    return (k[:4] + "..." + k[-4:] + "(len=%d)" % len(k)) if k else "(none)"


def sdk_chat(label, base_url, key, model, n_chars, max_tokens=1):
    client = OpenAI(base_url=base_url, api_key=key, timeout=60, max_retries=0)
    body = dict(
        model=model,
        messages=[
            {"role": "system", "content": "You are a probe. Reply with one word."},
            {"role": "user", "content": "ping " + ("x" * n_chars)},
        ],
        temperature=0,
        max_tokens=max_tokens,
    )
    t0 = time.time()
    try:
        resp = client.chat.completions.create(**body)
        dt = time.time() - t0
        txt = (resp.choices[0].message.content or "")[:40] if resp.choices else ""
        print("[%s] %s chars -> 200 (%.1fs) reply=%r" % (label, n_chars, dt, txt))
        return True
    except Exception as e:
        dt = time.time() - t0
        s = str(e)
        print("[%s] %s chars -> ERROR (%.1fs): %s" % (label, n_chars, dt, s[:400]))
        return False


print("=== PU-0145 SDK probes @ %s UTC ===" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

# ---------- Cerebras via SDK ----------
print("\n--- Cerebras (OpenAI SDK, api.cerebras.ai/v1) ---")
cb_key = ENV.get("CEREBRAS_API_KEY")
cb_model = ENV.get("LLM_ROUTER_MODEL_CEREBRAS", "qwen-3.8-27b")
print("key: %s model: %s" % (mask(cb_key), cb_model))
if cb_key:
    try:
        c = OpenAI(base_url="https://api.cerebras.ai/v1", api_key=cb_key, timeout=30, max_retries=0)
        models = c.models.list()
        ids = [m.id for m in models.data[:8]]
        print("GET /models -> 200, count=%d, sample=%s" % (len(models.data), ids))
    except Exception as e:
        print("GET /models -> ERROR: %s" % str(e)[:400])
    sdk_chat("cerebras", "https://api.cerebras.ai/v1", cb_key, cb_model, 0)

# ---------- NVIDIA payload ladder via SDK ----------
print("\n--- NVIDIA NIM payload-size ladder (meta/llama-3.2-11b-vision-instruct) ---")
nv_key = ENV.get("NVIDIA_KEY_1")
nv_model = ENV.get("LLM_ROUTER_MODEL_NVIDIA_NIM", "meta/llama-3.2-11b-vision-instruct")
print("key: %s" % mask(nv_key))
if nv_key:
    for n_chars in (0, 1_000, 10_000, 50_000, 100_000, 150_000, 200_000):
        ok = sdk_chat("nvidia", "https://integrate.api.nvidia.com/v1", nv_key, nv_model, n_chars)
        if not ok and n_chars >= 50_000:
            # one retry at next rung anyway; ladder continues
            pass

# ---------- TokenRouter via SDK ----------
print("\n--- TokenRouter (sk key against openrouter.ai/api/v1) ---")
tr_key = ENV.get("TOKENROUTER_KEY")
tr_model = ENV.get("LLM_ROUTER_MODEL_TOKENROUTER", "GLM5.2")
print("key: %s model: %s" % (mask(tr_key), tr_model))
if tr_key:
    sdk_chat("tokenrouter", "https://openrouter.ai/api/v1", tr_key, tr_model, 0)

# ---------- OpenRouter key status ----------
print("\n--- OpenRouter key status (/api/v1/key) ---")
or_key = ENV.get("OPENROUTER_API_KEY")
if or_key:
    try:
        c = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=or_key, timeout=30, max_retries=0)
        import urllib.request
        req = urllib.request.Request("https://openrouter.ai/api/v1/key")
        req.add_header("Authorization", "Bearer " + or_key)
        with urllib.request.urlopen(req, timeout=30) as r:
            info = json.loads(r.read().decode("utf-8", "replace"))
        data = info.get("data", info)
        safe = {k: data.get(k) for k in ("label", "is_free_tier", "limit", "usage", "limit_remaining", "is_blocked") if k in data}
        print("key info: %s" % json.dumps(safe))
    except Exception as e:
        print("key info -> ERROR: %s" % str(e)[:300])

print("\nPU-0145-OK")
