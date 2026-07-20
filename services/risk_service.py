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
        SELECT m.title as topic_title, s.name as stakeholder_name, s.role, a.attended 
        FROM attendance a
        JOIN meetings m ON a.meeting_id = m.id
        JOIN stakeholders s ON a.stakeholder_id = s.id
        WHERE m.plan_id = %s
    """
    detailed_att_data = execute_query(att_query, (plan_id,))
    
    assess_query = """
        SELECT question, answer, s.name as stakeholder_name
        FROM assessments a
        JOIN stakeholders s ON a.stakeholder_id = s.id
        WHERE a.plan_id = %s
    """
    assess_data = execute_query(assess_query, (plan_id,))
    
    from rag_service import query_knowledge
    # Query with broader terms and more results to capture document context
    rag_chunks = query_knowledge("risks issues problems challenges gaps delays", plan_id, n_results=10)
    from guardrails import retrieval_rail
    # Relax threshold for L2 distance to ensure documents are not wrongly discarded
    retrieval_passed, _ = retrieval_rail(rag_chunks, threshold=1.5, endpoint="/api/risks/detect")
    if not retrieval_passed:
        rag_chunks = []
    rag_context = "\n".join([chunk["text"] for chunk in rag_chunks]) if rag_chunks else "None"
    
    prompt = f"""
    Analyze the following Knowledge Transfer (KT) tracking data AND the uploaded Knowledge Base documents to identify potential risks.
    
    Plan Info: {plan_data[0] if plan_data else 'N/A'}
    Topic Completions: {comp_data}
    Detailed Meeting/Topic Attendance (Role, Name, Attended): {detailed_att_data}
    Knowledge Receiver Assessment Results: {assess_data}
    Uploaded Knowledge Base Context: {rag_context}
    
    CRITICAL INSTRUCTION: You MUST generate risks strictly on a PER-TOPIC basis. 
    Evaluate the following for each topic/meeting:
    1. Is the Knowledge Giver (Outgoing SME) taking KTs according to the schedule? (Look at their attendance in the meetings).
    2. Which Knowledge Receivers (Incoming Members) have low attendance or missed KTs for the topic?
    3. Which Knowledge Receivers have poor/failed assessment results for the topic?
    
    Identify up to 5 major risks based specifically on the giver's scheduling/attendance, receivers' low attendance, and receivers' poor assessment results per topic. Also consider any gaps mentioned in the Uploaded Knowledge Base Context.
    For each risk, assign a severity ('low', 'medium', 'high', 'critical').
    Return ONLY a JSON array of objects with keys "description" (string) and "severity" (string). Do not return markdown.
    """
    
    llm_response = call_llm(prompt)
    
    # Try to parse JSON from LLM
    try:
        # Strip markdown code blocks if any
        clean_json = llm_response.replace('```json', '').replace('```', '').strip()
        risks = json.loads(clean_json)
    except json.JSONDecodeError:
        import logging
        logging.warning(f"Risk detection LLM parse failure for plan {plan_id}: {llm_response[:200]}")
        return []
        
    saved_risks = []
    
    existing_query = "SELECT id, description, severity, status, detected_by FROM risks WHERE plan_id = %s AND status = 'open'"
    existing_risks = execute_query(existing_query, (plan_id,))
    
    def clean_words(s):
        return set(''.join(c if c.isalnum() or c.isspace() else ' ' for c in s.lower()).split())
        
    from guardrails import execution_rail
    for risk in risks:
        desc = risk.get('description', 'Unknown risk')
        severity = risk.get('severity', 'medium').lower()
        
        exec_passed, _ = execution_rail("risk_severity", {"severity": severity}, "/api/risks/detect")
        if not exec_passed:
            severity = 'medium'
            
        desc_words = clean_words(desc)
        duplicate_found = False
        
        for er in existing_risks:
            er_words = clean_words(er['description'])
            if not desc_words or not er_words:
                continue
            intersection = desc_words.intersection(er_words)
            if len(intersection) / len(desc_words) >= 0.6 or len(intersection) / len(er_words) >= 0.6:
                duplicate_found = True
                saved_risks.append({
                    "id": er['id'],
                    "description": er['description'],
                    "severity": er['severity'],
                    "status": er['status'],
                    "detected_by": er['detected_by']
                })
                break
                
        if duplicate_found:
            continue
            
        query = """
            INSERT INTO risks (plan_id, description, severity, detected_by)
            VALUES (%s, %s, %s, 'ai')
        """
        risk_id = execute_write(query, (plan_id, desc, severity))
            
        saved_risks.append({
            "id": risk_id,
            "description": desc,
            "severity": severity,
            "status": "open",
            "detected_by": "ai"
        })
        
    return saved_risks

def escalate_risk_service(risk_id):
    risk_query = "SELECT description, severity, plan_id FROM risks WHERE id = %s"
    risk_data = execute_query(risk_query, (risk_id,))
    if not risk_data:
        raise Exception("Risk not found")
        
    desc = risk_data[0]['description']
    severity = risk_data[0]['severity']
    plan_id = risk_data[0]['plan_id']
    
    from connectors import JiraConnector
    jira = JiraConnector()
    jira_ref = jira.push_risk_to_jira(desc, severity, plan_id)
    
    query = "UPDATE risks SET status = 'escalated', jira_ticket_ref = %s WHERE id = %s"
    execute_write(query, (jira_ref, risk_id))
    
    return {"escalated": True, "jira_ticket_ref": jira_ref}
