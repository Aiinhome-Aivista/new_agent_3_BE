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
    assessment_type = data.get('assessment_type', 'final')
    day_label = data.get('day_label')
    stakeholder_id = data.get('stakeholder_id')

    try:
        # Check if assessment has already been completed by this stakeholder
        if stakeholder_id:
            if assessment_type == 'day_wise' and day_label:
                existing = execute_query(
                    "SELECT id FROM assessment_results WHERE plan_id = %s AND stakeholder_id = %s AND (day_label = %s OR (assessment_type = 'day_wise' AND day_label = %s))",
                    (plan_id, stakeholder_id, day_label, day_label)
                )
                if existing:
                    return jsonify({
                        "success": False,
                        "message": f"Assessment for '{day_label}' has already been completed."
                    }), 400
            elif assessment_type == 'final':
                existing_final = execute_query(
                    "SELECT id FROM assessment_results WHERE plan_id = %s AND stakeholder_id = %s AND assessment_type = 'final'",
                    (plan_id, stakeholder_id)
                )
                if existing_final:
                    return jsonify({
                        "success": False,
                        "message": "Final Assessment for this plan has already been completed."
                    }), 400

        if assessment_type == 'day_wise' and day_label:
            # Day-wise Assessment (Optional): fetch topics specific to this day_label
            day_topics_res = execute_query(
                "SELECT topic_name FROM plan_topics WHERE plan_id = %s AND day_label = %s",
                (plan_id, day_label)
            )
            if day_topics_res:
                target_topics = [row['topic_name'] for row in day_topics_res]
            else:
                target_topics = [day_label]
        else:
            # Final Assessment (Mandatory) or default: fetch all completed topics
            completed_topics_res = execute_query(
                "SELECT topic, last_updated FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100",
                (plan_id,)
            )
            if not completed_topics_res:
                # Fallback to all plan topics if completion tracking isn't populated yet
                all_topics = execute_query("SELECT topic_name FROM plan_topics WHERE plan_id = %s", (plan_id,))
                if all_topics:
                    target_topics = [row['topic_name'] for row in all_topics]
                else:
                    return jsonify({
                        "success": False,
                        "message": "No topics available for assessment."
                    }), 400
            else:
                target_topics = [row['topic'] for row in completed_topics_res]
                # Enforce 1-week (7 days) deadline from plan completion date for Final Assessment
                from datetime import datetime, timedelta
                timestamps = [row['last_updated'] for row in completed_topics_res if row.get('last_updated')]
                if timestamps:
                    latest_completed = max(timestamps)
                    if isinstance(latest_completed, str):
                        try:
                            latest_completed = datetime.fromisoformat(latest_completed.replace('Z', ''))
                        except Exception:
                            latest_completed = None
                    if latest_completed and datetime.now() > (latest_completed + timedelta(days=7)):
                        return jsonify({
                            "success": False,
                            "message": "Final Assessment deadline has expired. It must be taken within 1 week of plan completion."
                        }), 400

        topics_str = "\n\n".join(target_topics)
        
        from rag_service import query_knowledge, extract_day_key

        # Check if any knowledge documents are uploaded for this plan / day in DB
        if assessment_type == 'day_wise' and day_label:
            target_key = extract_day_key(day_label)
            all_docs = execute_query("SELECT id, kt_day FROM knowledge_documents WHERE plan_id = %s", (plan_id,))
            docs_exist = False
            if all_docs:
                for d in all_docs:
                    doc_day_key = extract_day_key(d.get('kt_day', ''))
                    if target_key and doc_day_key and (target_key == doc_day_key or target_key in doc_day_key or doc_day_key in target_key):
                        docs_exist = True
                        break
        else:
            doc_query = "SELECT id FROM knowledge_documents WHERE plan_id = %s LIMIT 1"
            docs_exist = bool(execute_query(doc_query, (plan_id,)))
        
        if not docs_exist:
            return jsonify({
                "success": False,
                "message": "Documents are not uploaded."
            }), 400

        context_texts = []
        target_day_filter = day_label if assessment_type == 'day_wise' else None
        
        for topic in target_topics[:5]:
            results = query_knowledge(topic, plan_id=plan_id, kt_day=target_day_filter, n_results=3)
            for r in results:
                if r['text'] not in context_texts:
                    context_texts.append(r['text'])

        context_str = "\n---\n".join(context_texts) if context_texts else ""

        if not context_str.strip():
            return jsonify({
                "success": False,
                "message": "Documents are not uploaded."
            }), 400

        mode_desc = f"Day-wise Assessment (Optional) for '{day_label}'" if assessment_type == 'day_wise' and day_label else "Final Comprehensive Assessment (Mandatory)"

        prompt = f"""
        Assessment Mode: {mode_desc}

        Target Topics:
        {topics_str}
        
        Knowledge Base Context:
        {context_str}
        
        Generate exactly {Config.ASSESSMENT_QUESTION_COUNT} assessment questions.
        
        IMPORTANT:
        - You MUST generate questions STRICTLY based on the provided Knowledge Base Context.
        - The questions must also relate to the Target Topics above.
        - Do NOT generate questions using outside knowledge or generally. ONLY use the provided Context.
        - Return ONLY a JSON array of strings, where each string is a question.
        """

        # =========================================================================
        # COMMENTED OUT (Per User Requirement):
        # Previous feature that generated questions manually from LLM when no documents were uploaded.
        # Required Behavior: If no documents are uploaded, return "Documents are not uploaded."
        # =========================================================================
        # else:
        #     prompt = f"""
        #     Assessment Mode: {mode_desc}
        #
        #     Target Topics:
        #     {topics_str}
        #     
        #     Generate exactly {Config.ASSESSMENT_QUESTION_COUNT} assessment questions.
        #     
        #     IMPORTANT:
        #     - Generate questions ONLY from the target topics above.
        #     - Do NOT generate questions from unfinished or unrelated topics.
        #     - Do NOT assume any missing knowledge.
        #     - Return ONLY a JSON array of strings, where each string is a question.
        #     """

        llm_response = call_llm(prompt)
        
        try:
            clean_json = llm_response.replace('```json', '').replace('```', '').strip()
            questions = json.loads(clean_json)
        except json.JSONDecodeError:
            questions = [
                f"What are the main objectives covered in {day_label if day_label else 'this KT plan'}?",
                "Can you describe the primary architecture components discussed?",
                "What are the key technical concepts and workflows?",
                "How do you handle error cases or edge scenarios for these topics?",
                "Who are the key points of contact and resources for this domain?"
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
        limit = request.args.get('limit', type=int)  # optional, e.g. limit=5
        if stakeholder_id:
            if limit and limit > 0:
                query = """
                    SELECT ar.*, s.name as stakeholder_name, s.email as stakeholder_email
                    FROM assessment_results ar
                    JOIN stakeholders s ON ar.stakeholder_id = s.id
                    WHERE ar.plan_id = %s AND ar.stakeholder_id = %s
                    ORDER BY ar.created_at DESC
                    LIMIT %s
                """
                results = execute_query(query, (plan_id, stakeholder_id, limit))
            else:
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
        
        assessment_type = data.get('assessment_type', 'final')
        day_label = data.get('day_label')

        # Capture completed topics snapshot at completion time for specific assessment_type / day_label
        passed_topics = data.get('covered_topics')
        if passed_topics is not None and (not isinstance(passed_topics, list) or len(passed_topics) > 0):
            covered_topics_json = json.dumps(passed_topics) if isinstance(passed_topics, list) else passed_topics
        else:
            if assessment_type == 'day_wise' and day_label:
                from rag_service import extract_day_key
                target_key = extract_day_key(day_label)
                all_plan_topics = execute_query("SELECT topic_name, day_label FROM plan_topics WHERE plan_id = %s", (plan_id,))
                covered_topics_list = []
                if all_plan_topics:
                    for pt in all_plan_topics:
                        d_key = extract_day_key(pt.get('day_label', ''))
                        if target_key and d_key and (target_key == d_key or target_key in d_key or d_key in target_key):
                            covered_topics_list.append(pt['topic_name'])
                if not covered_topics_list:
                    covered_topics_list = [day_label]
            else:
                completed_topics_res = execute_query(
                    "SELECT topic FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100",
                    (plan_id,)
                )
                covered_topics_list = [row['topic'] for row in completed_topics_res] if completed_topics_res else []
            covered_topics_json = json.dumps(covered_topics_list)

        # Save parent summary row into assessment_results (try with assessment_type & day_label, fallback if columns not present yet)
        try:
            insert_query = """
                INSERT INTO assessment_results (asid, plan_id, stakeholder_id, assessment_type, day_label, overall_score, feedback, covered_topics)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            execute_write(insert_query, (asid, plan_id, stakeholder_id, assessment_type, day_label, overall_score, overall_feedback, covered_topics_json))
        except Exception:
            insert_query = """
                INSERT INTO assessment_results (asid, plan_id, stakeholder_id, overall_score, feedback, covered_topics)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            execute_write(insert_query, (asid, plan_id, stakeholder_id, overall_score, overall_feedback, covered_topics_json))
        
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
