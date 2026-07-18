import json
import logging
from db import execute_write
from llm_service import call_llm

def generate_plan_service(application_name, scope_description, plan_type, created_by=None, reverse_kt_focus=None):
    focus_text = f"\n    Reverse KT Focus Area: {reverse_kt_focus}" if reverse_kt_focus and plan_type == 'Reverse-KT' else ""
    
    prompt = f"""
    You are an expert IT Project Manager. Create a detailed {plan_type} plan for the application '{application_name}'.
    Scope: {scope_description}{focus_text}
    
    Format the output as a clean, structured Markdown document including:
    1. Objectives
    2. Target Audience
    3. Sessions / Topics Breakdown (with estimated duration in hours)
    4. Expected Outcomes
    
    Only output the markdown content, no conversational filler.
    """
    
    # Call LLM
    generated_content = call_llm(prompt)
    
    # Save to DB as draft
    query = """
        INSERT INTO kt_plans (application_name, scope_description, plan_type, generated_content, status, created_by)
        VALUES (%s, %s, %s, %s, 'draft', %s)
    """
    params = (application_name, scope_description, plan_type, generated_content, created_by)
    plan_id = execute_write(query, params)
    
    # Extract topics
    extract_and_save_topics(plan_id, generated_content)
    
    return {
        "id": plan_id,
        "generated_content": generated_content,
        "status": "draft"
    }

def extract_and_save_topics(plan_id, generated_content):
    extraction_prompt = f"""
Below is a Knowledge Transfer plan. Extract every individual topic/session line item listed in its
"Sessions / Topics Breakdown" tables (ignore the tables' header row and separator lines).

Plan content:
{generated_content}

Return ONLY a JSON array of objects, each with keys:
- "day_label" (string, e.g. "Day 1: Python Fundamentals and Core Concepts" — the section heading this topic falls under, or "General" if there are no day sections)
- "topic_name" (string, the topic/row name, e.g. "Data Types and Variables")
- "estimated_duration_hours" (string, e.g. "1" — use "N/A" if not specified)

Do not include any explanation, only the JSON array.
"""
    try:
        extraction_response = call_llm(extraction_prompt)
        
        # strip markdown code block fences if present
        clean_json = extraction_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.startswith("```"):
            clean_json = clean_json[3:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
        clean_json = clean_json.strip()
            
        topics = json.loads(clean_json)
        
        # Clear existing topics if this is a resync
        execute_write("DELETE FROM plan_topics WHERE plan_id = %s", (plan_id,))
        
        count = 0
        for item in topics:
            query = "INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)"
            execute_write(query, (plan_id, item.get('day_label', 'General'), item.get('topic_name'), item.get('estimated_duration_hours', 'N/A')))
            count += 1
            
        return count
    except Exception as e:
        logging.warning(f"Failed to extract topics for plan {plan_id}: {e}")
        return 0
