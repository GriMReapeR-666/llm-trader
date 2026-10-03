import os
import math
from openai import OpenAI
from dotenv import load_dotenv


def _usage_metadata(completion):
    """Extract provider usage only; never persist prompt/response content."""
    usage = getattr(completion, "usage", None)
    if usage is None:
        return {}
    result = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        try:
            if value is not None and int(value) >= 0:
                result[name] = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
    cost = getattr(completion, "cost", None)
    if cost is None:
        cost = usage.get("cost") if isinstance(usage, dict) else getattr(usage, "cost", None)
    try:
        if cost is not None and math.isfinite(float(cost)) and float(cost) >= 0:
            result["cost"] = float(cost)
    except (TypeError, ValueError, OverflowError):
        pass
    if result:
        result["source"] = "provider_response_usage"
    return result

class CerebrasGenerator:
    """A client for generating text using the Cerebras API."""

    def __init__(self, api_key: str | None = None):
        """
        Initializes the Cerebras client.

        Args:
            api_key: The Cerebras API key. If not provided, it will be
                     loaded from the CEREBRAS_API_KEY environment variable.
        """
        if not api_key:
            load_dotenv()
            api_key = os.getenv("CEREBRAS_API_KEY")
            
        # We don't raise strict error on init to allow soft-fail if not used
        self.api_key = api_key
        self.client = None
        self.last_usage = {}
        if self.api_key:
            self.client = OpenAI(
                base_url="https://api.cerebras.ai/v1",
                api_key=api_key,
            )

    # Rate Limiting Logic (Free Tier verified: 30 RPM)
    _request_timestamps = []
    _rpm_limit = 30  
    # Note: Free Tier also has Token limits (e.g. 60K-90K TPM depending on model).
    # Enforcement of RPM is the primary defense against 429s for chat interactions.

    def _wait_for_rate_limit(self):
        """Enforce RPM limit (30 requests/minute)."""
        import time
        current_time = time.time()
        
        # Remove timestamps older than 60 seconds
        self._request_timestamps = [t for t in self._request_timestamps if current_time - t < 60]
        
        if len(self._request_timestamps) >= self._rpm_limit:
            sleep_time = 60 - (current_time - self._request_timestamps[0]) + 1
            if sleep_time > 0:
                print(f"⏳ Rate Limit Reached ({self._rpm_limit} RPM). Sleeping {sleep_time:.2f}s...")
                time.sleep(sleep_time)
        
        self._request_timestamps.append(time.time())

    def generate(self, messages: list, model: str = "llama3.1-70b", **kwargs) -> str:
        """
        Generates text using a specified model on Cerebras with Rate Limiting & Retries.
        """
        if not self.client:
             return "Error: CEREBRAS_API_KEY not configured."

        import time
        max_retries = 5
        base_delay = 2

        for attempt in range(max_retries):
            try:
                # 1. Enforce Rate Limit before request
                self._wait_for_rate_limit()

                # Clean kwargs
                completion = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs
                )
                self.last_usage = _usage_metadata(completion)
                return completion.choices[0].message.content

            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "rate limit" in error_str
                is_server_error = "500" in error_str or "502" in error_str or "503" in error_str

                if is_rate_limit or is_server_error:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)  # Exponential Backoff
                        print(f"⚠️ API Error ({'429' if is_rate_limit else '5xx'}). Retrying in {delay}s... (Attempt {attempt+1}/{max_retries})")
                        time.sleep(delay)
                        continue
                
                # If strictly not a transient error, or retries exhausted, return error functionality
                self.last_usage = {}
                return f"An error occurred with the Cerebras API: {e}"
        
        self.last_usage = {}
        return "Error: Max retries exceeded for Cerebras API."

if __name__ == "__main__":
    # Test
    gen = CerebrasGenerator()
    if gen.client:
        print("Testing Cerebras...")
        res = gen.generate([{"role": "user", "content": "Hello"}])
        print(res)
    else:
        print("Skipping test: No API Key")
