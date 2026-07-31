from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm, load_prompt
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
            # Final Assessment (Mandatory) or default: fetch plan assessment settings for manager overrides
            plan_settings_res = execute_query(
                "SELECT is_final_unlocked, final_deadline_extension_days FROM kt_plans WHERE id = %s",
                (plan_id,)
            )
            is_unlocked = False
            deadline_days = 90
            if plan_settings_res:
                is_unlocked = bool(plan_settings_res[0].get('is_final_unlocked'))
                days_val = plan_settings_res[0].get('final_deadline_extension_days')
                if days_val is not None:
                    try:
                        deadline_days = int(days_val)
                    except (ValueError, TypeError):
                        deadline_days = 90

            completed_topics_res = execute_query(
                "SELECT topic, last_updated FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100",
                (plan_id,)
            )
            if not completed_topics_res:
                # If no topics 100% completed yet, check partial completion (> 0%)
                partial_topics = execute_query(
                    "SELECT topic, last_updated FROM completion_tracking WHERE plan_id = %s AND completion_percent > 0",
                    (plan_id,)
                )
                if partial_topics:
                    completed_topics_res = partial_topics
                else:
                    return jsonify({
                        "success": False,
                        "message": "No completed KT topics are available for assessment yet for this plan."
                    }), 400

            target_topics = [row['topic'] for row in completed_topics_res]
            from datetime import datetime, timedelta
            timestamps = [row['last_updated'] for row in completed_topics_res if row.get('last_updated')]
            if timestamps and not is_unlocked:
                latest_completed = max(timestamps)
                if isinstance(latest_completed, str):
                    try:
                        latest_completed = datetime.fromisoformat(latest_completed.replace('Z', ''))
                    except Exception:
                        latest_completed = None
                if latest_completed and datetime.now() > (latest_completed + timedelta(days=deadline_days)):
                    return jsonify({
                        "success": False,
                        "message": f"Final Assessment deadline has expired. It must be taken within {deadline_days} days of plan completion."
                    }), 400

        topics_str = "\n\n".join(target_topics)
        
        from rag_service import query_knowledge, extract_day_key

        # Check if knowledge documents are uploaded for this plan / day in DB
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
            if not docs_exist:
                return jsonify({
                    "success": False,
                    "message": f"Knowledge document for '{day_label}' is not uploaded."
                }), 400
        else:
            # Final Assessment: Check documents for ALL completed / required days
            plan_topics_rows = execute_query(
                "SELECT day_label, topic_name FROM plan_topics WHERE plan_id = %s",
                (plan_id,)
            )
            
            # Map completed topics to their corresponding day_label
            days_map = {}
            if plan_topics_rows:
                for pt in plan_topics_rows:
                    d_label = (pt.get('day_label') or 'General').strip()
                    t_name = (pt.get('topic_name') or '').strip().lower()
                    if d_label not in days_map:
                        days_map[d_label] = []
                    if t_name:
                        days_map[d_label].append(t_name)

            target_topics_set = set(t.strip().lower() for t in target_topics)

            required_days = []
            if days_map:
                for d_label, t_list in days_map.items():
                    d_label_lower = d_label.lower()
                    has_completed_topic = any(tn in target_topics_set for tn in t_list)
                    is_day_completed = d_label_lower in target_topics_set
                    is_partial_match = any(ct in d_label_lower or d_label_lower in ct or any(ct in tn or tn in ct for tn in t_list) for ct in target_topics_set)
                    
                    if has_completed_topic or is_day_completed or is_partial_match:
                        required_days.append(d_label)

            if not required_days and days_map:
                required_days = list(days_map.keys())

            all_docs = execute_query("SELECT id, kt_day FROM knowledge_documents WHERE plan_id = %s", (plan_id,)) or []
            uploaded_day_keys = set()
            for d in all_docs:
                d_key = extract_day_key(d.get('kt_day', ''))
                if d_key:
                    uploaded_day_keys.add(d_key)

            missing_days = []
            for req_day in required_days:
                req_key = extract_day_key(req_day)
                if req_key:
                    has_doc = any(req_key == uk or req_key in uk or uk in req_key for uk in uploaded_day_keys)
                    if not has_doc:
                        missing_days.append(req_day)

            if missing_days:
                missing_str = ", ".join(missing_days)
                return jsonify({
                    "success": False,
                    "message": f"Knowledge documents for the following day(s) are missing: {missing_str}. All completed day documents must be uploaded before starting the Final Assessment."
                }), 400

        context_texts = []
        target_day_filter = day_label if assessment_type == 'day_wise' else None
        
        for topic in target_topics[:15]:
            results = query_knowledge(topic, plan_id=plan_id, kt_day=target_day_filter, n_results=3)
            for r in results:
                if r['text'] not in context_texts:
                    context_texts.append(r['text'])

        context_str = "\n---\n".join(context_texts) if context_texts else ""

        if not context_str.strip():
            return jsonify({
                "success": False,
                "message": "Documents are not uploaded or contain no extractable text."
            }), 400

        mode_desc = f"Day-wise Assessment (Optional) for '{day_label}'" if assessment_type == 'day_wise' and day_label else "Final Comprehensive Assessment (Mandatory)"

        prompt = load_prompt(
            "assessment_question_generation.txt",
            mode_desc=mode_desc,
            topics_str=topics_str,
            context_str=context_str,
            question_count=Config.ASSESSMENT_QUESTION_COUNT
        )

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
        prompt = load_prompt("assessment_answer_scoring.txt", question=data['question'], answer=data['answer'])
        
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
        
        prompt = load_prompt("assessment_overall_feedback.txt", summary_str=summary_str)
        
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

@assessment_bp.route('/plan/<int:plan_id>/settings', methods=['GET'])
def get_plan_assessment_settings(plan_id):
    try:
        query = "SELECT is_final_unlocked, final_deadline_extension_days FROM kt_plans WHERE id = %s"
        res = execute_query(query, (plan_id,))
        if not res:
            return jsonify({"success": False, "message": "Plan not found"}), 404
        
        row = res[0]
        is_unlocked = bool(row.get('is_final_unlocked', False))
        days = row.get('final_deadline_extension_days')
        if days is None:
            days = 90
            
        return jsonify({
            "success": True,
            "data": {
                "is_final_unlocked": is_unlocked,
                "final_deadline_extension_days": int(days)
            }
        }), 200
    except Exception:
        return jsonify({
            "success": True,
            "data": {
                "is_final_unlocked": False,
                "final_deadline_extension_days": 90
            }
        }), 200


@assessment_bp.route('/plan/<int:plan_id>/settings', methods=['PUT'])
def update_plan_assessment_settings(plan_id):
    data = request.json or {}
    try:
        is_unlocked = 1 if data.get('is_final_unlocked') else 0
        days = int(data.get('final_deadline_extension_days', 90))
        
        if days <= 0:
            return jsonify({"success": False, "message": "Deadline window days must be a positive integer."}), 400

        # Calculate elapsed days since completion to ensure new deadline window > elapsed days
        completed_topics_res = execute_query(
            "SELECT last_updated FROM completion_tracking WHERE plan_id = %s AND completion_percent = 100",
            (plan_id,)
        )
        plan_topics_cnt = execute_query(
            "SELECT COUNT(*) as cnt FROM plan_topics WHERE plan_id = %s",
            (plan_id,)
        )
        total_topics = plan_topics_cnt[0]['cnt'] if plan_topics_cnt else 0
        all_completed = total_topics > 0 and len(completed_topics_res or []) >= total_topics

        if all_completed and completed_topics_res:
            from datetime import datetime
            timestamps = [row['last_updated'] for row in completed_topics_res if row.get('last_updated')]
            if timestamps:
                latest_completed = max(timestamps)
                if isinstance(latest_completed, str):
                    try:
                        latest_completed = datetime.fromisoformat(latest_completed.replace('Z', ''))
                    except Exception:
                        latest_completed = None
                if latest_completed:
                    elapsed_days = max(0, (datetime.now() - latest_completed).days)
                    if days <= elapsed_days:
                        return jsonify({
                            "success": False,
                            "message": f"Invalid Deadline! Already {elapsed_days} day(s) have passed since plan completion. The new deadline window must be greater than {elapsed_days} day(s)."
                        }), 400

        query = """
            UPDATE kt_plans 
            SET is_final_unlocked = %s, final_deadline_extension_days = %s
            WHERE id = %s
        """
        execute_write(query, (is_unlocked, days, plan_id))

        # Trigger Final Assessment email reminder check if settings updated/unlocked
        try:
            from services.notification_service import trigger_final_assessment_reminder
            trigger_final_assessment_reminder(plan_id)
        except Exception:
            pass

        return jsonify({
            "success": True,
            "message": "Manager assessment settings updated successfully.",
            "data": {
                "is_final_unlocked": bool(is_unlocked),
                "final_deadline_extension_days": days
            }
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@assessment_bp.route('/plan/<int:plan_id>/notify-reminder', methods=['POST'])
def send_final_assessment_reminder_route(plan_id):
    try:
        from services.notification_service import trigger_final_assessment_reminder
        trigger_final_assessment_reminder(plan_id)
        return jsonify({
            "success": True,
            "message": "Final Assessment email reminder triggered successfully to pending Knowledge Receivers."
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

