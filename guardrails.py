import re
from llm_service import call_llm
from db import execute_write

def log_guardrail(rail_type: str, passed: bool, reason: str, endpoint: str):
    query = """
        INSERT INTO guardrail_logs (rail_type, passed, reason, endpoint)
        VALUES (%s, %s, %s, %s)
    """
    try:
        execute_write(query, (rail_type, passed, reason, endpoint))
    except Exception as e:
        print(f"Failed to log guardrail: {e}")

def input_rail(payload: dict, required_fields: list, endpoint: str) -> tuple[bool, str]:
    if not payload:
        reason = "Empty payload"
        log_guardrail("input", False, reason, endpoint)
        return False, reason
        
    for field in required_fields:
        if field not in payload or payload[field] is None or payload[field] == "":
            reason = f"Missing or empty required field: {field}"
            log_guardrail("input", False, reason, endpoint)
            return False, reason
            
    for k, v in payload.items():
        if isinstance(v, str):
            if len(v) > 5000:
                reason = f"Field {k} exceeds 5000 characters"
                log_guardrail("input", False, reason, endpoint)
                return False, reason
            if "ignore previous instructions" in v.lower():
                reason = f"Prompt injection detected in {k}"
                log_guardrail("input", False, reason, endpoint)
                return False, reason
                
    log_guardrail("input", True, "Passed", endpoint)
    return True, ""

def dialog_rail(question: str, endpoint: str) -> tuple[bool, str]:
    prompt = f"Is this question about KT plans, schedules, risks, or assessments? Answer only YES or NO.\nQuestion: {question}"
    llm_resp = call_llm(prompt)
    
    if not llm_resp:
        # Fail open
        log_guardrail("dialog", True, "LLM failed, failing open", endpoint)
        return True, ""
        
    if "yes" in llm_resp.lower():
        log_guardrail("dialog", True, "Passed", endpoint)
        return True, ""
    else:
        reason = "Off-topic question detected"
        log_guardrail("dialog", False, reason, endpoint)
        return False, reason

def retrieval_rail(chunks: list[dict], threshold: float = 0.8, endpoint: str = "retrieval") -> tuple[bool, str]:
    if not chunks:
        log_guardrail("retrieval", True, "No chunks retrieved", endpoint)
        return True, ""
        
    all_above = all(c.get("distance", 1.0) > threshold for c in chunks)
    if all_above:
        reason = "All retrieved chunks have distance above threshold (irrelevant)"
        log_guardrail("retrieval", False, reason, endpoint)
        return False, reason
        
    log_guardrail("retrieval", True, "Passed", endpoint)
    return True, ""

def execution_rail(action: str, payload: dict, endpoint: str) -> tuple[bool, str]:
    if action == "risk_severity":
        severity = payload.get("severity", "").lower()
        if severity not in ['low', 'medium', 'high', 'critical']:
            reason = f"Invalid risk severity: {severity}"
            log_guardrail("execution", False, reason, endpoint)
            return False, reason
    elif action == "assessment_score":
        score = payload.get("score")
        if not isinstance(score, int) or score < 0 or score > 10:
            reason = f"Invalid assessment score: {score}"
            log_guardrail("execution", False, reason, endpoint)
            return False, reason
            
    log_guardrail("execution", True, "Passed", endpoint)
    return True, ""

def output_rail(text: str, endpoint: str) -> tuple[bool, str]:
    if not text:
        reason = "Empty LLM output"
        log_guardrail("output", False, reason, endpoint)
        return False, reason
        
    email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    phone_pattern = r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'
    
    if re.search(email_pattern, text) or re.search(phone_pattern, text):
        reason = "Output contains PII (email/phone)"
        log_guardrail("output", False, reason, endpoint)
        return False, reason
        
    log_guardrail("output", True, "Passed", endpoint)
    return True, ""
