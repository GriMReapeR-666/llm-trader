"""PU-0144: direct provider probes to classify NVIDIA 403 / Cerebras 402.
Read-only GET /models per key + single 1-token chat per provider.
Never prints secret values - masked fingerprints only.
"""
import json
import time
import urllib.request
import urllib.error


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
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            env[k] = v
    return env


ENV = load_env()


def mask(k):
    if not k:
        return "(none)"
    return k[:4] + "..." + k[-4:] + "(len=%d)" % len(k)


def req(method, url, key, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Bearer " + key)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, {}, "%s: %s" % (type(e).__name__, e)


def summarize_body(txt, max_len=400):
    try:
        j = json.loads(txt)
    except Exception:
        return txt[:max_len]
    if isinstance(j, dict) and isinstance(j.get("data"), list):
        ids = [m.get("id") for m in j["data"] if isinstance(m, dict)]
        return {"models_count": len(ids), "first_ids": ids[:3]}
    return txt[:max_len]


def chat_probe(label, url, key, model):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }
    status, headers, txt = req("POST", url, key, body)
    interesting = {
        h: headers.get(h)
        for h in ("x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens",
                  "x-ratelimit-reset", "x-request-id", "retry-after")
        if headers.get(h)
    }
    print("[%s] chat status=%s model=%s" % (label, status, model))
    print("  body: %s" % json.dumps(summarize_body(txt))[:600])
    if interesting:
        print("  headers: %s" % json.dumps(interesting))


out = {"generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
print("=== PU-0144 provider probes @ %s UTC ===" % out["generated_at_utc"])

# ---------- NVIDIA NIM ----------
print("\n--- NVIDIA NIM (integrate.api.nvidia.com) ---")
nv_keys = []
for i in range(1, 6):
    k = ENV.get("NVIDIA_KEY_%d" % i)
    if k:
        nv_keys.append(("NVIDIA_KEY_%d" % i, k))
if not nv_keys and ENV.get("NVIDIA_API_KEY"):
    nv_keys.append(("NVIDIA_API_KEY", ENV["NVIDIA_API_KEY"]))
out["nvidia_keys_loaded"] = [n for n, _ in nv_keys]
print("keys loaded: %s" % out["nvidia_keys_loaded"])

nv_models_ok = []
for name, key in nv_keys:
    status, headers, txt = req("GET", "https://integrate.api.nvidia.com/v1/models", key)
    body = summarize_body(txt)
    print("[%s=%s] GET /models status=%s -> %s" % (name, mask(key), status, json.dumps(body)[:300]))
    if status == 200:
        nv_models_ok.append(name)

nv_chat_key = None
for name, key in nv_keys:
    if not nv_models_ok or name in nv_models_ok:
        nv_chat_key = (name, key)
        break
if nv_chat_key:
    chat_probe("nvidia/%s" % nv_chat_key[0], "https://integrate.api.nvidia.com/v1/chat/completions",
               nv_chat_key[1], ENV.get("LLM_ROUTER_MODEL_NVIDIA_NIM", "meta/llama-3.2-11b-vision-instruct"))
else:
    print("nvidia: no key produced a non-403 /models response; skipping chat probe")

# ---------- Cerebras ----------
print("\n--- Cerebras (api.cerebras.ai) ---")
cb_key = ENV.get("CEREBRAS_API_KEY")
print("key: %s" % mask(cb_key))
if cb_key:
    status, headers, txt = req("GET", "https://api.cerebras.ai/v1/models", cb_key)
    print("GET /models status=%s -> %s" % (status, json.dumps(summarize_body(txt))[:300]))
    chat_probe("cerebras", "https://api.cerebras.ai/v1/chat/completions", cb_key,
               ENV.get("LLM_ROUTER_MODEL_CEREBRAS", "qwen-3.8-27b"))

# ---------- OpenRouter (quota-state check; key validity) ----------
print("\n--- OpenRouter (openrouter.ai) ---")
or_key = ENV.get("OPENROUTER_API_KEY")
print("key: %s" % mask(or_key))
if or_key:
    chat_probe("openrouter", "https://openrouter.ai/api/v1/chat/completions", or_key,
               ENV.get("LLM_ROUTER_MODEL_OPENROUTER", "nvidia/nemotron-3-super-120b-a12b:free"))

# ---------- TokenRouter (potential 4th provider) ----------
print("\n--- TokenRouter (base_url=openrouter.ai/api/v1, TOKENROUTER_KEY) ---")
tr_key = ENV.get("TOKENROUTER_KEY")
print("key: %s" % mask(tr_key))
if tr_key:
    status, headers, txt = req("GET", "https://openrouter.ai/api/v1/models", tr_key)
    print("GET /models status=%s -> %s" % (status, json.dumps(summarize_body(txt))[:200]))

print("\nPU-0144-OK")
