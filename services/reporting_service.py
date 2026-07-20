from db import execute_query, execute_write
from llm_service import call_llm
import os
from datetime import datetime
try:
    from docx import Document
except ImportError:
    Document = None

REPORTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports_output')
os.makedirs(REPORTS_DIR, exist_ok=True)

import re

def generate_report_doc(title, content, filename):
    if not Document:
        raise Exception("python-docx is not installed")
    doc = Document()
    doc.add_heading(title, 0)
    
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        if line.startswith('### '):
            doc.add_heading(line[4:], level=3)
        elif line.startswith('## '):
            doc.add_heading(line[3:], level=2)
        elif line.startswith('# '):
            doc.add_heading(line[2:], level=1)
        elif line.startswith('- ') or line.startswith('* '):
            p = doc.add_paragraph(style='List Bullet')
            parts = re.split(r'(\*\*.*?\*\*)', line[2:])
            for part in parts:
                if part.startswith('**') and part.endswith('**'):
                    p.add_run(part[2:-2]).bold = True
                else:
                    p.add_run(part)
        else:
            p = doc.add_paragraph()
            parts = re.split(r'(\*\*.*?\*\*)', line)
            for part in parts:
                if part.startswith('**') and part.endswith('**'):
                    p.add_run(part[2:-2]).bold = True
                else:
                    p.add_run(part)
                    
    filepath = os.path.join(REPORTS_DIR, filename)
    doc.save(filepath)
    return filepath

def generate_weekly_service(plan_id):
    # Gather context for LLM
    comp_query = """
        SELECT 
            (SELECT COUNT(*) FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100) as completed_topics,
            (SELECT COUNT(*) FROM plan_topics WHERE plan_id = %s) as total_topics
        FROM DUAL
    """
    comp_res = execute_query(comp_query, (plan_id, plan_id))
    
    if comp_res and comp_res[0]['total_topics'] and int(comp_res[0]['total_topics']) > 0:
        avg_comp = (float(comp_res[0]['completed_topics']) / float(comp_res[0]['total_topics'])) * 100.0
    else:
        avg_comp = 0.0
    
    risk_query = "SELECT description, severity FROM risks WHERE plan_id = %s AND status != 'resolved'"
    risks = execute_query(risk_query, (plan_id,))
    
    prompt = f"""
    Write a concise narrative summary for a Weekly KT Status Report.
    Overall Completion: {avg_comp}%
    Open Risks: {risks}
    
    Structure the summary with: Progress, Risks & Issues, and Next Steps.
    """
    
    summary = call_llm(prompt)
    
    filename = f"Weekly_Report_{plan_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
    filepath = generate_report_doc(f"Weekly KT Report (Plan {plan_id})", summary, filename)
    
    # Save to DB
    query = "INSERT INTO reports (plan_id, report_type, file_path) VALUES (%s, %s, %s)"
    report_id = execute_write(query, (plan_id, 'weekly', filename))
    
    return {"id": report_id, "filename": filename}

def generate_final_service(plan_id):
    # Gather extensive context for final report
    plan_query = "SELECT application_name FROM kt_plans WHERE id = %s"
    app_name = execute_query(plan_query, (plan_id,))[0]['application_name']
    
    # Fetch completed/covered topics (100% complete)
    topics_query = "SELECT topic FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100"
    completed_topics_res = execute_query(topics_query, (plan_id,))
    completed_topics_list = [row['topic'] for row in completed_topics_res]
    source_type = "completed topics (100% progress)"
    
    # Fallback 1: If no topics are 100% complete, fall back to topics with completion_percent > 0
    if not completed_topics_list:
        fallback_query = "SELECT topic FROM completion_tracking WHERE plan_id = %s AND completion_percent > 0"
        completed_topics_res = execute_query(fallback_query, (plan_id,))
        completed_topics_list = [row['topic'] for row in completed_topics_res]
        source_type = "partially covered topics (progress > 0%)"
        
    # Fallback 2: If still no topics have any tracked completion, fall back to all plan topics
    if not completed_topics_list:
        fallback_query = "SELECT topic_name FROM plan_topics WHERE plan_id = %s"
        completed_topics_res = execute_query(fallback_query, (plan_id,))
        completed_topics_list = [row['topic_name'] for row in completed_topics_res]
        source_type = "all topics in the plan"
        
    topics_text = "\n".join([f"- {t}" for t in completed_topics_list]) if completed_topics_list else "No topics found"
    
    prompt = f"""
    Write a comprehensive Final KT Assessment Report for '{app_name}'.
    
    The report MUST be generated based ONLY on the following covered topics of the selected plan ({source_type}):
    {topics_text}
    
    CRITICAL:
    - Only include information, assessment of readiness, and content directly relating to these covered topics.
    - Do NOT include, mention, or describe any other topics, modules, or content. Absolutely no extra content is allowed outside of these covered topics.
    
    Structure the report with:
    - Executive Summary (focusing ONLY on the covered topics)
    - Detailed Assessment of Readiness (for each covered topic listed above)
    - Sign-off Section
    """
    
    content = call_llm(prompt)
    
    filename = f"Final_Report_{plan_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
    filepath = generate_report_doc(f"Final KT Report - {app_name}", content, filename)
    
    query = "INSERT INTO reports (plan_id, report_type, file_path) VALUES (%s, %s, %s)"
    report_id = execute_write(query, (plan_id, 'final', filename))
    
    return {"id": report_id, "filename": filename}
