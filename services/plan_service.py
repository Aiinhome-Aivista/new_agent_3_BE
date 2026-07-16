from db import execute_write
from llm_service import call_llm

def generate_plan_service(application_name, scope_description, plan_type, created_by=None):
    prompt = f"""
    You are an expert IT Project Manager. Create a detailed {plan_type} plan for the application '{application_name}'.
    Scope: {scope_description}
    
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
    
    return {
        "id": plan_id,
        "generated_content": generated_content,
        "status": "draft"
    }
