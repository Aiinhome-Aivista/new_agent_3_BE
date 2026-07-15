from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm
import json

risk_bp = Blueprint('risk_bp', __name__)

@risk_bp.route('/detect', methods=['POST'])
def detect_risks():
    data = request.json
    if 'plan_id' not in data:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    plan_id = data['plan_id']
    
    try:
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
        
        prompt = f"""
        Analyze the following Knowledge Transfer (KT) data and identify potential risks.
        
        Plan Info: {plan_data[0] if plan_data else 'N/A'}
        Topic Completions: {comp_data}
        Attendance Records: {att_data}
        
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
        for risk in risks:
            desc = risk.get('description', 'Unknown risk')
            severity = risk.get('severity', 'medium').lower()
            if severity not in ['low', 'medium', 'high', 'critical']:
                severity = 'medium'
                
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
            
        return jsonify({"success": True, "data": saved_risks, "message": "Risks detected and logged"}), 200
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@risk_bp.route('/', methods=['GET'])
def get_risks():
    plan_id = request.args.get('plan_id')
    try:
        if plan_id:
            query = "SELECT * FROM risks WHERE plan_id = %s ORDER BY created_at DESC"
            risks = execute_query(query, (plan_id,))
        else:
            query = "SELECT * FROM risks ORDER BY created_at DESC"
            risks = execute_query(query)
        return jsonify({"success": True, "data": risks}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@risk_bp.route('/<int:id>/escalate', methods=['PUT'])
def escalate_risk(id):
    try:
        query = "UPDATE risks SET status = 'escalated' WHERE id = %s"
        execute_write(query, (id,))
        return jsonify({"success": True, "message": "Risk escalated"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
