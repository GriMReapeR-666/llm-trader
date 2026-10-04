"""PU-0146: TokenRouter key identity check against OpenRouter (no secrets)."""
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
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
tr_key = ENV.get("TOKENROUTER_KEY", "")


def get(url, key):
    r = urllib.request.Request(url)
    r.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


print("=== PU-0146 tokenrouter key identity @ %s UTC ==="
      % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
print("TOKENROUTER_KEY fingerprint: %s..." % tr_key[:6])

status, body = get("https://openrouter.ai/api/v1/key", tr_key)
print("GET /api/v1/key -> %s" % status)
try:
    data = json.loads(body).get("data", {})
    print("  recognized-key fields: %s" % json.dumps(
        {k: data.get(k) for k in ("label", "is_free_tier", "usage", "limit") if k in data}))
except Exception:
    print("  body: %s" % body[:300])

# Raw urllib chat with explicit header (bypasses SDK entirely) - rules out SDK header handling
r = urllib.request.Request(
    "https://openrouter.ai/api/v1/chat/completions",
    data=json.dumps({"model": "GLM5.2",
                     "messages": [{"role": "user", "content": "ping"}],
                     "max_tokens": 1}).encode(),
    method="POST")
r.add_header("Authorization", "Bearer " + tr_key)
r.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(r, timeout=30) as resp:
        print("raw chat -> %s %s" % (resp.status, resp.read()[:120]))
except urllib.error.HTTPError as e:
    print("raw chat -> %s %s" % (e.code, e.read().decode("utf-8", "replace")[:200]))
except Exception as e:
    print("raw chat -> %s: %s" % (type(e).__name__, e))

print("PU-0146-OK")
