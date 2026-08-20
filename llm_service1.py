import requests
import os
import time
from config import Config

LLM_API_URL = Config.LLM_API_URL
LLM_MODEL = Config.LLM_MODEL

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), 'prompts')


class LLMServiceError(RuntimeError):
    """Raised when the configured LLM service cannot provide a response."""

def load_prompt(filename: str, **kwargs) -> str:
    prompt_path = os.path.join(PROMPTS_DIR, filename)
    with open(prompt_path, 'r', encoding='utf-8') as f:
        template = f.read()
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template

def call_llm(
    prompt,
    stream=False,
    timeout_seconds=None,
    max_output_tokens=None,
    temperature=None,
):
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": stream
    }
    if Config.LLM_KEEP_ALIVE:
        payload["keep_alive"] = Config.LLM_KEEP_ALIVE
    options = {}
    if max_output_tokens is not None:
        options["num_predict"] = int(max_output_tokens)
    if temperature is not None:
        options["temperature"] = float(temperature)
    if options:
        payload["options"] = options

    from opentelemetry import trace
    tracer = trace.get_tracer(__name__)
    read_timeout = timeout_seconds or Config.LLM_READ_TIMEOUT
    attempts = Config.LLM_MAX_RETRIES + 1
    last_error = None
    for attempt in range(attempts):
        try:
            with tracer.start_as_current_span("call_llm") as span:
                span.set_attribute("model_name", LLM_MODEL)
                span.set_attribute("prompt_length", len(prompt))
                span.set_attribute("attempt", attempt + 1)
                response = requests.post(
                    LLM_API_URL,
                    json=payload,
                    timeout=(Config.LLM_CONNECT_TIMEOUT, read_timeout),
                )
                response.raise_for_status()
            data = response.json()
            result = data.get("response", "")

            from guardrails import output_rail
            passed, _ = output_rail(result, "llm_service.call_llm")
            if not passed:
                return ""
            return result
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            print(f"LLM request attempt {attempt + 1} failed: {exc}")
            if attempt < attempts - 1:
                time.sleep(0.25)
        except requests.RequestException as exc:
            raise LLMServiceError(f"LLM service request failed: {exc}") from exc
        except (ValueError, TypeError) as exc:
            raise LLMServiceError(f"LLM service returned an invalid response: {exc}") from exc

    raise LLMServiceError(
        f"LLM service did not respond after {attempts} attempts: {last_error}"
    ) from last_error
