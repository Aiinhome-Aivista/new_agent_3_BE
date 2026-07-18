from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm
from config import Config
import json

assessment_bp = Blueprint('assessment_bp', __name__)

@assessment_bp.route('/generate-questions', methods=['POST'])
def generate_questions():
    data = request.json
    if 'plan_id' not in data:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    plan_id = data['plan_id']
    try:
        # Fetch completed topics for the selected plan
        query = "SELECT topic FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100"
        completed_topics_res = execute_query(query, (plan_id,))
        
        if not completed_topics_res:
            return jsonify({
                "success": False,
                "message": "No completed topics available for assessment."
            }), 400
            
        completed_topics_list = [row['topic'] for row in completed_topics_res]
        topics_str = "\n\n".join(completed_topics_list)
        
        prompt = f"""
        Completed Topics
        
        {topics_str}
        
        Generate exactly {Config.ASSESSMENT_QUESTION_COUNT} assessment questions.
        
        IMPORTANT
        
        Generate questions ONLY from the completed KT topics above.
        
        Do NOT generate questions from unfinished topics.
        
        Do NOT assume any missing knowledge.
        
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
        
        # Store question and answer only.
        # asmt_id (FK to assessment_results.id) is set to NULL now and
        # updated in bulk when complete_assessment is called.
        query = """
            INSERT INTO assessments (plan_id, stakeholder_id, question, answer)
            VALUES (%s, %s, %s, %s)
        """
        params = (data['plan_id'], data['stakeholder_id'], data['question'], data['answer'])
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
        stakeholder_id = request.args.get('stakeholder_id')
        if stakeholder_id:
            query = """
                SELECT ar.*, s.name as stakeholder_name, s.email as stakeholder_email
                FROM assessment_results ar
                JOIN stakeholders s ON ar.stakeholder_id = s.id
                WHERE ar.plan_id = %s AND ar.stakeholder_id = %s
                ORDER BY ar.created_at DESC
            """
            results = execute_query(query, (plan_id, stakeholder_id))
        else:
            query = """
                SELECT ar.*, s.name as stakeholder_name, s.email as stakeholder_email
                FROM assessment_results ar
                JOIN stakeholders s ON ar.stakeholder_id = s.id
                WHERE ar.plan_id = %s
                ORDER BY ar.created_at DESC
            """
            results = execute_query(query, (plan_id,))
        return jsonify({"success": True, "data": results}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@assessment_bp.route('/complete', methods=['POST'])
def complete_assessment():
    data = request.json
    required = ['asid', 'plan_id', 'stakeholder_id', 'question_scores', 'questions_data']
    for req in required:
        if req not in data:
            return jsonify({"success": False, "message": f"Missing {req}"}), 400
            
    asid = data['asid']
    plan_id = data['plan_id']
    stakeholder_id = data['stakeholder_id']
    # Scores array collected in React state: [int, int, ...]
    question_scores = data.get('question_scores', [])
    # Q+A data array from React state: [{question, answer}, ...]
    questions_data = data.get('questions_data', [])

    try:
        count = len(questions_data)
        if count == 0:
            return jsonify({"success": False, "message": "No question data provided."}), 400

        # Compute overall score from frontend-supplied scores (cumulative total)
        if question_scores:
            total_score = sum(int(s) for s in question_scores)
            score_count = len(question_scores)
        else:
            total_score = 0
            score_count = count
            
        overall_score = float(total_score)
        
        # Build Q+A summary for the overall LLM feedback prompt
        q_a_summaries = []
        for i, row in enumerate(questions_data):
            score_val = question_scores[i] if i < len(question_scores) else 0
            q_a_summaries.append(
                f"Q{i+1}: {row.get('question', '')}\nA{i+1}: {row.get('answer', '')}\nScore: {score_val}/10"
            )
            
        summary_str = "\n\n".join(q_a_summaries)
        
        prompt = f"""
        Analyze the candidate's performance across the following conversational assessment questions and answers:
        
        {summary_str}
        
        Generate a cohesive, constructive summary feedback paragraph for the overall assessment.
        Highlight areas of strength and areas where further knowledge transfer might be needed.
        Keep it concise and professional (maximum 3-4 sentences).
        """
        
        overall_feedback = call_llm(prompt)
        overall_feedback = overall_feedback.strip()
        
        # Save parent summary row into assessment_results
        insert_query = """
            INSERT INTO assessment_results (asid, plan_id, stakeholder_id, overall_score, feedback)
            VALUES (%s, %s, %s, %s, %s)
        """
        execute_write(insert_query, (asid, plan_id, stakeholder_id, overall_score, overall_feedback))
        
        # Fetch the new assessment_results.id to back-fill assessments.asmt_id
        id_query = "SELECT id FROM assessment_results WHERE asid = %s"
        id_res = execute_query(id_query, (asid,))
        if id_res:
            result_id = id_res[0]['id']
            # Link the most recent NULL asmt_id rows for this stakeholder+plan to this result
            update_query = """
                UPDATE assessments
                SET asmt_id = %s
                WHERE stakeholder_id = %s AND plan_id = %s AND asmt_id IS NULL
            """
            execute_write(update_query, (result_id, stakeholder_id, plan_id))
            
            # Update the assessment_results.asid to be the string value of the PK id
            update_asid_query = """
                UPDATE assessment_results
                SET asid = %s
                WHERE id = %s
            """
            execute_write(update_asid_query, (str(result_id), result_id))
            asid = str(result_id)
        
        return jsonify({
            "success": True,
            "data": {
                "asid": asid,
                "overall_score": overall_score,
                "feedback": overall_feedback
            },
            "message": "Assessment summary results saved successfully."
        }), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@assessment_bp.route('/attempt/<string:asid>/details', methods=['GET'])
def get_attempt_details(asid):
    try:
        # Fetch the parent summary row
        res_query = """
            SELECT ar.*, s.name as stakeholder_name 
            FROM assessment_results ar
            JOIN stakeholders s ON ar.stakeholder_id = s.id
            WHERE ar.asid = %s
        """
        results_info = execute_query(res_query, (asid,))
        if not results_info:
            return jsonify({"success": False, "message": "Assessment result record not found."}), 404
            
        overall = results_info[0]
        result_id = overall['id']
        
        # Fetch child question rows linked by asmt_id FK
        ass_query = "SELECT * FROM assessments WHERE asmt_id = %s ORDER BY id ASC"
        questions = execute_query(ass_query, (result_id,))
        
        return jsonify({
            "success": True,
            "data": {
                "overall": overall,
                "questions": questions
            }
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
