import os
import math
from openai import OpenAI
from dotenv import load_dotenv


def _usage_metadata(completion):
    """Extract provider usage without retaining prompts, responses, or keys."""
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

class OpenRouterGenerator:
    """A client for generating text using the OpenRouter API."""

    def __init__(self, api_key: str | None = None):
        """
        Initializes the OpenRouter client.

        Args:
            api_key: The OpenRouter API key. If not provided, it will be
                     loaded from the OPENROUTER_API_KEY environment variable.
        """
        if not api_key:
            load_dotenv()
            api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("TOKENROUTER_API_KEY") or os.getenv("TOKENROUTER_KEY")
            if not api_key:
                raise ValueError(
                    "Error: OPENROUTER_API_KEY / TOKENROUTER_KEY not found. "
                    "Provide it as an argument to the constructor or set it in the .env file."
                )

        # OpenRouter is compatible with the OpenAI SDK.
        # We configure the client with the OpenRouter base URL and our key.
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self.last_usage = {}

    def generate(self, messages: list, model: str = "mistralai/mistral-nemo", **kwargs) -> str:
        """
        Generates text using a specified model on OpenRouter.

        Args:
            messages: A list of message dictionaries (e.g., [{"role": "user", "content": "..."}]).
            model: The model identifier from OpenRouter.
            **kwargs: Additional arguments to pass to the API (e.g., temperature, seed).

        Returns:
            The generated text from the model or an error message.
        """
        try:
            completion = self.client.chat.completions.create(
                model=model,
                messages=messages,
                **kwargs
            )
            self.last_usage = _usage_metadata(completion)
            return completion.choices[0].message.content
        except Exception as e:
            self.last_usage = {}
            return f"An error occurred with the OpenRouter API: {e}"

# --- Main execution ---
if __name__ == "__main__":
    try:
        # Initialize the generator once
        generator = OpenRouterGenerator()
        model_to_use = "mistralai/mistral-nemo"
        log_file = "chat_log.txt"

        # Initialize conversation history with a system prompt to guide the AI
        conversation_history = [
            {
                "role": "system",
                "content": "You are a helpful coding assistant. When asked to create code, provide the code in a markdown block.",
            }
        ]
 
        print("--- OpenRouter Coding Assistant ---")
        print(f"Using model: {model_to_use}")
        print(f"Conversation will be saved to {log_file}")
        print('Type /modify <filename> to edit a file, or "quit" to exit.\n')

        while True:
            # Get user input from the terminal
            input_prompt = input("You: ")

            # Check for an exit command
            if input_prompt.lower() in ["quit", "exit"]:
                print("Goodbye!")
                break

            # --- Feature: Modify an existing file ---
            if input_prompt.strip().startswith("/modify"):
                try:
                    parts = input_prompt.split()
                    if len(parts) < 2:
                        print("System: Usage: /modify <filename>")
                        continue
                    target_file = parts[1]

                    if not os.path.exists(target_file):
                        print(f"System: Error - File '{target_file}' not found.")
                        continue

                    with open(target_file, 'r', encoding='utf-8') as f:
                        file_content = f.read()

                    modification_instruction = input(f"How should I modify {target_file}? ")
                    if not modification_instruction:
                        print("System: Modification cancelled.")
                        continue

                    # Construct a detailed prompt for the LLM
                    full_prompt = (
                        f"The user wants to modify the file '{target_file}'.\n\n"
                        f"Here is the original content:\n```\n{file_content}\n```\n\n"
                        f"Apply the following instruction: '{modification_instruction}'\n\n"
                        "Provide ONLY the complete, updated file content in your response."
                    )

                    print("AI: ... (modifying file)")
                    # Use a separate, temporary message list for file modification
                    modification_messages = [{"role": "user", "content": full_prompt}]
                    modified_content = generator.generate(modification_messages, model=model_to_use)

                    # Extract content if it's wrapped in a markdown block
                    code_match = re.search(r"```(?:\w*\n)?(.*)```", modified_content, re.DOTALL)
                    final_content = code_match.group(1).strip() if code_match else modified_content

                    with open(target_file, 'w', encoding='utf-8') as f:
                        f.write(final_content)

                    print(f"AI: Successfully modified and saved '{target_file}'.\n")
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(f"You: {input_prompt}\n")
                        f.write(f"You (instruction): {modification_instruction}\n")
                        f.write(f"AI: Modified and saved {target_file}.\n\n")

                except Exception as e:
                    print(f"System: An error occurred during file modification: {e}")
                continue

            # --- Regular chat generation ---
            print("AI: ...")
            ai_response = generator.generate(input_prompt, model=model_to_use)
            print(f"AI: {ai_response}\n")

            # Save the conversation to the log file
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"You: {input_prompt}\n")
                f.write(f"AI: {ai_response}\n\n")

            # --- Feature: Detect and offer to save code blocks ---
            code_match = re.search(r"```(\w*)\n(.*?)```", ai_response, re.DOTALL)
            if code_match:
                language = code_match.group(1).lower().strip() or "text"
                code_content = code_match.group(2).strip()
                lang_to_ext = {'python': '.py', 'javascript': '.js', 'html': '.html', 'css': '.css', 'java': '.java', 'sql': '.sql', 'bash': '.sh', 'shell': '.sh'}
                extension = lang_to_ext.get(language, '.txt')

                print(f"--- Code Detected ({language}) ---")
                filename_prompt = input(f"Enter a filename to save (e.g., script{extension}) or press Enter to skip: ")

                if filename_prompt:
                    try:
                        with open(filename_prompt, 'w', encoding='utf-8') as f:
                            f.write(code_content)
                        print(f"System: Code saved to '{filename_prompt}'.\n")
                        with open(log_file, 'a', encoding='utf-8') as f:
                            f.write(f"System: Saved code block to {filename_prompt}.\n\n")
                    except Exception as e:
                        print(f"System: Error saving file: {e}\n")
    except ValueError as e:
        print(e)
    except KeyboardInterrupt:
        print("\nExiting chat. Goodbye!")
