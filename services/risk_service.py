from db import execute_query, execute_write
from llm_service import call_llm
import json

def detect_risks_service(plan_id):
    # Gather data for LLM
    plan_query = "SELECT application_name, scope_description FROM kt_plans WHERE id = %s"
    plan_data = execute_query(plan_query, (plan_id,))
    
    comp_query = "SELECT topic, completion_percent FROM completion_tracking WHERE plan_id = %s"
    comp_data = execute_query(comp_query, (plan_id,))
    
    att_query = """
        SELECT s.name, a.attended 
        FROM attendance a
        JOIN meetings m ON a.meeting_id = m.id
        JOIN stakeholders s ON a.stakeholder_id = s.id
        WHERE m.plan_id = %s
    """
    att_data = execute_query(att_query, (plan_id,))
    
    from rag_service import query_knowledge
    rag_chunks = query_knowledge("risks issues problems", plan_id)
    from guardrails import retrieval_rail
    retrieval_passed, _ = retrieval_rail(rag_chunks, endpoint="/api/risks/detect")
    if not retrieval_passed:
        rag_chunks = []
    rag_context = "\n".join([chunk["text"] for chunk in rag_chunks]) if rag_chunks else "None"
    
    prompt = f"""
    Analyze the following Knowledge Transfer (KT) data and identify potential risks.
    
    Plan Info: {plan_data[0] if plan_data else 'N/A'}
    Topic Completions: {comp_data}
    Attendance Records: {att_data}
    Related Knowledge Base Context: {rag_context}
    
    Identify up to 3 major risks. For each, assign a severity ('low', 'medium', 'high', 'critical').
    Return ONLY a JSON array of objects with keys "description" (string) and "severity" (string).
    """
    
    llm_response = call_llm(prompt)
    
    # Try to parse JSON from LLM
    try:
        # Strip markdown code blocks if any
        clean_json = llm_response.replace('```json', '').replace('```', '').strip()
        risks = json.loads(clean_json)
    except json.JSONDecodeError:
        # Fallback if parsing fails
        risks = [{"description": "AI generated risk analysis failed to parse. Raw response: " + llm_response[:100], "severity": "medium"}]
        
    saved_risks = []
    from guardrails import execution_rail
    for risk in risks:
        desc = risk.get('description', 'Unknown risk')
        severity = risk.get('severity', 'medium').lower()
        
        exec_passed, _ = execution_rail("risk_severity", {"severity": severity}, "/api/risks/detect")
        if not exec_passed:
            severity = 'medium'
            
        query = """
            INSERT INTO risks (plan_id, description, severity, detected_by)
            VALUES (%s, %s, %s, 'ai')
        """
        risk_id = execute_write(query, (plan_id, desc, severity))
        
        if severity == 'critical':
            from connectors import JiraConnector
            jira = JiraConnector()
            jira.push_risk_to_jira(desc, severity, plan_id)
            
        saved_risks.append({
            "id": risk_id,
            "description": desc,
            "severity": severity,
            "status": "open",
            "detected_by": "ai"
        })
        
    return saved_risks

def escalate_risk_service(risk_id):
    query = "UPDATE risks SET status = 'escalated' WHERE id = %s"
    execute_write(query, (risk_id,))
