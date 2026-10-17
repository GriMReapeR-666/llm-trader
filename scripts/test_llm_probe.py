import os
import sys
import time
import json
from dotenv import load_dotenv

load_dotenv()

def test_all():
    print("=" * 60)
    print("LLM PROVIDER LIVE PROBE & CATALOGUE TEST")
    print("=" * 60)

    # 1. Inspect environment keys
    keys = {
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY"),
        "TOKENROUTER_KEY": os.getenv("TOKENROUTER_KEY"),
        "CEREBRAS_API_KEY": os.getenv("CEREBRAS_API_KEY"),
        "NVIDIA_KEY_1": os.getenv("NVIDIA_KEY_1"),
        "NVIDIA_KEY_2": os.getenv("NVIDIA_KEY_2"),
        "ROUTESME_KEY": os.getenv("ROUTESME_KEY"),
        "OMNIROUTER_KEY": os.getenv("OMNIROUTER_KEY"),
    }
    for k, v in keys.items():
        if v:
            print(f"Key {k}: PRESENT ({v[:6]}...{v[-4:]}, len {len(v)})")
        else:
            print(f"Key {k}: ABSENT")
    print()

    # 2. Test OpenRouter
    or_key = keys["OPENROUTER_API_KEY"]
    if or_key:
        from openai import OpenAI
        print("--> Testing OpenRouter...")
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=or_key, timeout=20)
        try:
            t0 = time.time()
            models = client.models.list()
            m_ids = [m.id for m in models.data]
            print(f"  [OpenRouter Catalogue] SUCCESS: {len(m_ids)} models available in {time.time()-t0:.2f}s")
            free_models = [m for m in m_ids if ":free" in m or "free" in m.lower()]
            print(f"  [OpenRouter Free Models] Found {len(free_models)} free models. Sample: {free_models[:5]}")
        except Exception as e:
            print(f"  [OpenRouter Catalogue] FAILED: {e}")

        # Test generation with reasoning
        test_model = "nvidia/nemotron-3-super-120b-a12b:free"
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=test_model,
                messages=[
                    {"role": "system", "content": "You are a quant trader. Think step by step and output JSON with reasoning and action."},
                    {"role": "user", "content": "EURUSD is breaking 1.0900 resistance with heavy buy volume. Decide BUY, SELL, or HOLD."}
                ],
                max_tokens=350,
            )
            elapsed = time.time() - t0
            msg = resp.choices[0].message
            print(f"  [OpenRouter Generation] SUCCESS in {elapsed:.2f}s with {test_model}")
            msg_dict = msg.model_dump()
            reasoning = msg_dict.get("reasoning") or msg_dict.get("reasoning_content")
            print(f"  [OpenRouter Reasoning] Extracted reasoning ({len(str(reasoning))} chars): {str(reasoning)[:200]}...")
            print(f"  [OpenRouter Content] Extracted content ({len(str(msg.content))} chars): {str(msg.content)[:200]}...")
            usage = resp.usage.model_dump() if resp.usage else {}
            print(f"  [OpenRouter Usage] {usage}")
        except Exception as e:
            print(f"  [OpenRouter Generation] FAILED: {e}")
    print()

    # 3. Test NVIDIA NIM
    nv_key = keys["NVIDIA_KEY_1"]
    if nv_key:
        from openai import OpenAI
        print("--> Testing NVIDIA NIM...")
        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=nv_key, timeout=20)
        try:
            t0 = time.time()
            models = client.models.list()
            m_ids = [m.id for m in models.data]
            print(f"  [NVIDIA NIM Catalogue] SUCCESS: {len(m_ids)} models available in {time.time()-t0:.2f}s")
        except Exception as e:
            print(f"  [NVIDIA NIM Catalogue] FAILED: {e}")

        # Test generation with fast reasoning model
        test_model = "nvidia/nemotron-3-super-120b-a12b"
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=test_model,
                messages=[
                    {"role": "system", "content": "You are a quantitative trading system. Return JSON: {\"action\": \"BUY\"|\"SELL\"|\"HOLD\", \"reasoning\": \"...\", \"confidence\": float}."},
                    {"role": "user", "content": "EURUSD breaking 1.0900 with high momentum. Give trade decision."}
                ],
                max_tokens=350,
            )
            elapsed = time.time() - t0
            msg = resp.choices[0].message
            print(f"  [NVIDIA NIM Generation] SUCCESS in {elapsed:.2f}s with {test_model}")
            msg_dict = msg.model_dump()
            reasoning = msg_dict.get("reasoning_content") or msg_dict.get("reasoning")
            print(f"  [NVIDIA NIM Reasoning] Extracted reasoning ({len(str(reasoning))} chars): {str(reasoning)[:200]}...")
            print(f"  [NVIDIA NIM Content] Extracted content ({len(str(msg.content))} chars): {str(msg.content)[:200]}...")
            usage = resp.usage.model_dump() if resp.usage else {}
            print(f"  [NVIDIA NIM Usage] {usage}")
        except Exception as e:
            print(f"  [NVIDIA NIM Generation] FAILED: {e}")
    print()

    # 4. Test Cerebras
    cer_key = keys["CEREBRAS_API_KEY"]
    if cer_key:
        from openai import OpenAI
        print("--> Testing Cerebras...")
        client = OpenAI(base_url="https://api.cerebras.ai/v1", api_key=cer_key, timeout=15)
        try:
            t0 = time.time()
            models = client.models.list()
            m_ids = [m.id for m in models.data]
            print(f"  [Cerebras Catalogue] SUCCESS: {len(m_ids)} models available: {m_ids}")
        except Exception as e:
            print(f"  [Cerebras Catalogue] FAILED: {e}")

        test_model = "qwen-3.8-27b"
        try:
            resp = client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10
            )
            print(f"  [Cerebras Generation] SUCCESS: {resp.choices[0].message.content}")
        except Exception as e:
            print(f"  [Cerebras Generation] EXPECTED BILLING / STATUS: {e}")
    print()

    # 5. Test TokenRouter
    tr_key = keys["TOKENROUTER_KEY"]
    if tr_key:
        from openai import OpenAI
        print("--> Testing TokenRouter...")
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=tr_key, timeout=15)
        try:
            resp = client.chat.completions.create(
                model="mistralai/mistral-nemo",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10
            )
            print(f"  [TokenRouter Generation] SUCCESS: {resp.choices[0].message.content}")
        except Exception as e:
            print(f"  [TokenRouter Generation] KEY STATUS: {e}")

if __name__ == "__main__":
    test_all()
