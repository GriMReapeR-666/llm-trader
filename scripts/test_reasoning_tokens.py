import os
import time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
key = os.getenv("NVIDIA_KEY_1")
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key, timeout=30)
t0 = time.time()
resp = client.chat.completions.create(
    model="nvidia/nemotron-3-super-120b-a12b",
    messages=[
        {"role": "system", "content": 'You are a quantitative trading system. Return JSON: {"action": "BUY"|"SELL"|"HOLD", "reasoning": "...", "confidence": float}.'},
        {"role": "user", "content": "EURUSD breaking 1.0900 with high momentum. Give trade decision in JSON."}
    ],
    max_tokens=800,
)
msg = resp.choices[0].message
print(f"Elapsed: {time.time() - t0:.2f}s")
details = getattr(resp.usage, "completion_tokens_details", None)
print("Reasoning tokens:", getattr(details, "reasoning_tokens", None))
print("Reasoning length:", len(getattr(msg, "reasoning_content", "") or ""))
print("Content length:", len(getattr(msg, "content", "") or ""))
print("--- FULL REASONING TRACE ---")
print(getattr(msg, "reasoning_content", ""))
print("--- FULL CONTENT ---")
print(msg.content)
