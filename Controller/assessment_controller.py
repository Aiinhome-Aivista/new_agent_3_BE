from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm
import json

assessment_bp = Blueprint('assessment_bp', __name__)

@assessment_bp.route('/generate-questions', methods=['POST'])
def generate_questions():
    data = request.json
    if 'plan_id' not in data:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    plan_id = data['plan_id']
    try:
        # Fetch plan content
        query = "SELECT generated_content FROM kt_plans WHERE id = %s"
        plan = execute_query(query, (plan_id,))
        plan_content = plan[0]['generated_content'] if plan else "General Knowledge Transfer"
        
        prompt = f"""
        Based on the following KT Plan content, generate exactly 5 assessment questions to test a stakeholder's understanding.
        
        Plan Content:
        {plan_content[:1500]}
        
        Return ONLY a JSON array of strings, where each string is a question.
        """
        
        llm_response = call_llm(prompt)
        
        try:
            clean_json = llm_response.replace('```json', '').replace('```', '').strip()
            questions = json.loads(clean_json)
        except json.JSONDecodeError:
            questions = [
                "What are the main objectives of this KT?",
                "Can you describe the primary architecture components?",
                "What are the known risks in this domain?",
                "How do you handle deployment for this application?",
                "Who are the key points of contact?"
            ]
            
        return jsonify({"success": True, "data": questions}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@assessment_bp.route('/submit', methods=['POST'])
def submit_answer():
    data = request.json
    required = ['plan_id', 'stakeholder_id', 'question', 'answer']
    from guardrails import input_rail
    passed, reason = input_rail(data, required, "/api/assessments/submit")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    try:
        prompt = f"""
        Score the following answer to an assessment question on a scale of 0 to 10.
        Question: {data['question']}
        Answer: {data['answer']}
        
        Provide constructive feedback.
        Return ONLY a JSON object with keys "score" (integer 0-10) and "feedback" (string).
        """
        
        llm_response = call_llm(prompt)
        
        try:
            clean_json = llm_response.replace('```json', '').replace('```', '').strip()
            result = json.loads(clean_json)
            score = int(result.get('score', 0))
            feedback = result.get('feedback', 'No feedback provided.')
        except:
            score = 5
            feedback = "AI could not parse score. Manual review needed."
            
        from guardrails import execution_rail
        exec_passed, exec_reason = execution_rail("assessment_score", {"score": score}, "/api/assessments/submit")
        if not exec_passed:
            score = 0
            feedback = f"Guardrail blocked score: {exec_reason}"
            
        query = """
            INSERT INTO assessments (plan_id, stakeholder_id, question, answer, ai_score, feedback)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        params = (data['plan_id'], data['stakeholder_id'], data['question'], data['answer'], score, feedback)
        execute_write(query, params)
        
        return jsonify({
            "success": True, 
            "data": {"score": score, "feedback": feedback},
            "message": "Answer scored and saved"
        }), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@assessment_bp.route('/plan/<int:plan_id>/results', methods=['GET'])
def get_results(plan_id):
    try:
        query = """
            SELECT a.*, s.name as stakeholder_name 
            FROM assessments a
            JOIN stakeholders s ON a.stakeholder_id = s.id
            WHERE a.plan_id = %s
            ORDER BY a.created_at DESC
        """
        results = execute_query(query, (plan_id,))
        return jsonify({"success": True, "data": results}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
