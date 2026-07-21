import json
import logging
import re
from db import execute_write, execute_query
from llm_service import call_llm

def resolve_stakeholder_for_user(user_email, user_full_name, user_role):
    """Find an existing stakeholder matching this user's email, or create one."""
    existing = execute_query("SELECT id FROM stakeholders WHERE email = %s", (user_email,))
    if existing:
        return existing[0]['id']

    # Map the users.role value to the stakeholders.role ENUM
    role_map = {
        'leadership': 'leadership',
        'engagement_manager': 'engagement_manager',
        'manager': 'engagement_manager',
        'outgoing_sme': 'outgoing_sme',
        'incoming_member': 'incoming_member',
    }
    mapped_role = role_map.get(user_role, 'engagement_manager')

    new_id = execute_write(
        "INSERT INTO stakeholders (name, email, role) VALUES (%s, %s, %s)",
        (user_full_name, user_email, mapped_role)
    )
    return new_id

def generate_plan_service(application_name, scope_description, plan_type, user_email=None, user_full_name=None, user_role=None, reverse_kt_focus=None):
    created_by = None
    if user_email and user_full_name and user_role:
        created_by = resolve_stakeholder_for_user(user_email, user_full_name, user_role)

    focus_text = f"\n    Reverse KT Focus Area: {reverse_kt_focus}" if reverse_kt_focus and plan_type == 'Reverse-KT' else ""
    
    prompt = f"""
    You are an expert IT Project Manager. Create a detailed {plan_type} plan for the application '{application_name}'.
    Scope & Topics: {scope_description}{focus_text}
    
    STRICT TIMELINE ALGORITHM (ABSOLUTE REQUIREMENT):
    You MUST execute the following continuous timeline calculation for every day:

    1. Daily Target: Every daily session MUST be filled with EXACTLY 2 HOURS (120 MINUTES) of sub-topic content (only the final day can have less than 120 minutes).
    
    2. Continuous Cumulative Filling:
       - Maintain a running count of minutes for the current day starting at 0 up to EXACTLY 120 minutes.
       - When adding sub-topics to the current day, if a sub-topic pushes the daily sum past 120 minutes, YOU MUST SPLIT THAT SUB-TOPIC:
         - Place (120 - current_sum) minutes into Part 1 of the sub-topic on the CURRENT day so that the current day reaches EXACTLY 120 minutes.
         - Place the remaining minutes into Part 2 of the sub-topic at the start of the NEXT day.
    
    3. ABSOLUTE PROHIBITION RULES:
       - NEVER end a day at 45m, 60m, 75m, 90m, or 105m! If a day has not reached 120 minutes, YOU ARE FORBIDDEN from starting a new day. You MUST split the next sub-topic to fill the exact remaining minutes.
       - NEVER push a sub-topic to the next day if the current day has unfilled minutes.
    
    4. MANDATORY DAY FORMAT:
       For every day (except the final day), include an explicit daily total line at the end showing the sum equals 120 minutes:

       Day 1: [Time: 2 hours]
       1. Introduction to Django REST Framework (DRF)
       • Overview of DRF (30 minutes)
       • Setting Up a DRF Project (45 minutes)
       • Serializers in DRF (Part 1 - 45 minutes)
       (Daily Total: 30 + 45 + 45 = 120 minutes / 2 hours)

       Day 2: [Time: 2 hours]
       1. Introduction to Django REST Framework (DRF) (continued)
       • Serializers in DRF (Part 2 - 15 minutes)
       • ViewSets and Routers (60 minutes)
       2. Advanced Concepts in Django ORM
       • QuerySet Methods (45 minutes)
       (Daily Total: 15 + 60 + 45 = 120 minutes / 2 hours)

    5. Prefix main topics with consecutive numbers (1. Topic, 2. Topic). If a topic continues onto the next day, keep the same number and append '(continued)'.

    Format the output as a clean, structured Markdown document including:
    1. Objectives
    2. Target Audience
    3. Sessions / Topics Breakdown (MUST follow the 120-minute daily algorithm above)
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

def add_topic_service(plan_id, day_label, topic_name, estimated_duration_hours="N/A"):
    query = "INSERT INTO plan_topics (plan_id, day_label, topic_name, estimated_duration_hours) VALUES (%s, %s, %s, %s)"
    topic_id = execute_write(query, (plan_id, day_label or 'General', topic_name, estimated_duration_hours or 'N/A'))
    return topic_id

def update_topic_service(topic_id, day_label=None, topic_name=None, estimated_duration_hours=None):
    updates = []
    params = []
    if day_label is not None:
        updates.append("day_label = %s")
        params.append(day_label)
    if topic_name is not None:
        updates.append("topic_name = %s")
        params.append(topic_name)
    if estimated_duration_hours is not None:
        updates.append("estimated_duration_hours = %s")
        params.append(estimated_duration_hours)
    
    if not updates:
        return False
        
    params.append(topic_id)
    query = f"UPDATE plan_topics SET {', '.join(updates)} WHERE id = %s"
    execute_write(query, tuple(params))
    return True

def delete_topic_service(topic_id):
    query = "DELETE FROM plan_topics WHERE id = %s"
    execute_write(query, (topic_id,))
    return True
