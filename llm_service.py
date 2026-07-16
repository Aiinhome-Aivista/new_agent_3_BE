import requests
import os
from config import Config

LLM_API_URL = Config.LLM_API_URL
LLM_MODEL = Config.LLM_MODEL

def call_llm(prompt, stream=False):
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": stream
    }
    try:
        response = requests.post(LLM_API_URL, json=payload, timeout=500)
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")
    except Exception as e:
        print(f"Error calling LLM: {e}")
        return ""
