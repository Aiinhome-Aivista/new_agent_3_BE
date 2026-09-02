import requests
import os
from config import Config

LLM_API_URL = Config.LLM_API_URL
LLM_MODEL = Config.LLM_MODEL

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), 'prompts')

def load_prompt(filename: str, **kwargs) -> str:
    prompt_path = os.path.join(PROMPTS_DIR, filename)
    with open(prompt_path, 'r', encoding='utf-8') as f:
        template = f.read()
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template

def call_llm(prompt, stream=False):
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": stream
    }
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("call_llm") as span:
            span.set_attribute("model_name", LLM_MODEL)
            span.set_attribute("prompt_length", len(prompt))
            
            response = requests.post(LLM_API_URL, json=payload, timeout=3000)
            response.raise_for_status()
        data = response.json()
        result = data.get("response", "")
        
        from guardrails import output_rail
        passed, _ = output_rail(result, "llm_service.call_llm")
        if not passed:
            return ""
            
        return result
    except Exception as e:
        print(f"Error calling LLM: {e}")
        return ""
