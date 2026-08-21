from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm
import json

planning_bp = Blueprint('planning_bp', __name__)

@planning_bp.route('/generate', methods=['POST'])
def generate_plan():
    data = request.json
    required_fields = ['application_name', 'scope_description', 'plan_type']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/plans/generate")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    app_name = data['application_name']
    scope = data['scope_description']
    plan_type = data['plan_type']
    reverse_kt_focus = data.get('reverse_kt_focus') # Optional
    
    # Extract user identity from JWT if present
    user_email = None
    user_full_name = None
    user_role = None
    
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        try:
            import jwt
            from config import Config
            payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
            user_email = payload.get('email')
            user_role = payload.get('role')
            user_id = payload.get('sub')
            if user_id:
                users = execute_query("SELECT full_name FROM users WHERE id = %s", (user_id,))
                if users:
                    user_full_name = users[0]['full_name']
        except Exception:
            pass # fallback to None if invalid token
    
    project_config = data.get('project_config')
    project_id = data.get('project_id')
    try:
        from services.plan_service import generate_plan_service
        result_data = generate_plan_service(app_name, scope, plan_type, user_email, user_full_name, user_role, reverse_kt_focus, project_config, project_id)
        
        return jsonify({
            "success": True, 
            "data": result_data,
            "message": "Plan generated successfully"
        }), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/extract-from-doc', methods=['POST'])
def extract_plan_info_from_doc():
    uploaded_files = []
    if 'files' in request.files:
        uploaded_files = request.files.getlist('files')
    elif 'file' in request.files:
        uploaded_files = request.files.getlist('file')
    
    uploaded_files = [f for f in uploaded_files if f and f.filename != '']
    if not uploaded_files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400

    try:
        from services.plan_service import extract_plan_info_from_doc_service
        extracted_info = extract_plan_info_from_doc_service(uploaded_files)
        return jsonify({
            "success": True,
            "data": extracted_info,
            "message": "Document(s) analyzed successfully"
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/', methods=['GET'])
def get_plans():
    try:
        for_dropdown = request.args.get('for_dropdown') == 'true'
        user_email = None
        user_full_name = None
        user_role = None
        
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                import jwt
                from config import Config
                payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
                user_email = payload.get('email')
                user_role = payload.get('role')
                user_id = payload.get('sub')
                if user_id:
                    users = execute_query("SELECT full_name FROM users WHERE id = %s", (user_id,))
                    if users:
                        user_full_name = users[0]['full_name']
            except Exception:
                pass

        if user_role == 'Delivery / Engagement Manager':
            from services.plan_service import resolve_stakeholder_for_user
            stakeholder_id = None
            if user_email:
                stakeholder_id = resolve_stakeholder_for_user(user_email, user_full_name, user_role)
            
            if stakeholder_id:
                if for_dropdown:
                    query = "SELECT * FROM kt_plans WHERE (approved_by = %s OR approved_by IS NULL) AND status IN ('approved', 'closed') ORDER BY created_at DESC"
                    plans = execute_query(query, (stakeholder_id,))
                else:
                    query = "SELECT * FROM kt_plans WHERE created_by = %s OR approved_by = %s OR approved_by IS NULL ORDER BY created_at DESC"
                    plans = execute_query(query, (stakeholder_id, stakeholder_id))
            else:
                query = "SELECT * FROM kt_plans ORDER BY created_at DESC"
                plans = execute_query(query)
        elif user_role and ('incoming' in user_role.lower() or 'receiver' in user_role.lower() or 'giver' in user_role.lower() or 'outgoing' in user_role.lower() or 'sme' in user_role.lower()):
            from services.plan_service import resolve_stakeholder_for_user
            stakeholder_id = None
            if user_email:
                stakeholder_id = resolve_stakeholder_for_user(user_email, user_full_name, user_role)
            
            if stakeholder_id:
                union_query = """
                    SELECT DISTINCT plan_id FROM (
                        SELECT m.plan_id FROM meetings m JOIN attendance a ON m.id = a.meeting_id WHERE a.stakeholder_id = %s AND m.plan_id IS NOT NULL
                        UNION
                        SELECT plan_id FROM assessments WHERE stakeholder_id = %s AND plan_id IS NOT NULL
                        UNION
                        SELECT plan_id FROM assessment_results WHERE stakeholder_id = %s AND plan_id IS NOT NULL
                        UNION
                        SELECT plan_id FROM stakeholders WHERE id = %s AND plan_id IS NOT NULL
                    ) AS user_plans
                """
                plan_rows = execute_query(union_query, (stakeholder_id, stakeholder_id, stakeholder_id, stakeholder_id))
                assigned_plan_ids = [r['plan_id'] for r in plan_rows if r.get('plan_id')]

                if assigned_plan_ids:
                    format_strings = ','.join(['%s'] * len(assigned_plan_ids))
                    if for_dropdown:
                        query = f"SELECT * FROM kt_plans WHERE id IN ({format_strings}) AND status IN ('approved', 'closed') ORDER BY created_at DESC"
                    else:
                        query = f"SELECT * FROM kt_plans WHERE id IN ({format_strings}) ORDER BY created_at DESC"
                    plans = execute_query(query, tuple(assigned_plan_ids))
                else:
                    plans = []
            else:
                plans = []
        else:
            query = "SELECT * FROM kt_plans ORDER BY created_at DESC"
            plans = execute_query(query)
        for p in plans:
            pid = p['id']
            try:
                shadow_map = execute_query("SELECT stakeholder_id FROM resource_mapping WHERE plan_id = %s AND is_shadow = 1", (pid,))
                p['shadow_stakeholder_ids'] = [r['stakeholder_id'] for r in shadow_map] if shadow_map else []
            except Exception as e:
                p['shadow_stakeholder_ids'] = []
            
            try:
                eligible_query = """
                    SELECT DISTINCT s.stakeholder_id
                    FROM sud_documents s
                    JOIN assessment_results ar ON s.stakeholder_id = ar.stakeholder_id AND s.plan_id = ar.plan_id
                    WHERE s.plan_id = %s 
                      AND ar.assessment_type = 'final' 
                      AND ar.overall_score >= 40
                """
                eligible_map = execute_query(eligible_query, (pid,))
                p['shadow_eligible_stakeholder_ids'] = [r['stakeholder_id'] for r in eligible_map] if eligible_map else []
                
                sud_map = execute_query("SELECT DISTINCT stakeholder_id FROM sud_documents WHERE plan_id = %s", (pid,))
                p['sud_submitted_stakeholder_ids'] = [r['stakeholder_id'] for r in sud_map] if sud_map else []
                
                asmt_map = execute_query("SELECT DISTINCT stakeholder_id FROM assessment_results WHERE plan_id = %s AND assessment_type = 'final' AND overall_score >= 40", (pid,))
                p['assessment_passed_stakeholder_ids'] = [r['stakeholder_id'] for r in asmt_map] if asmt_map else []
                
            except Exception as e:
                print(f"Error fetching shadow eligible stakeholders for plan {pid}: {e}")
                p['shadow_eligible_stakeholder_ids'] = []
                p['sud_submitted_stakeholder_ids'] = []
                p['assessment_passed_stakeholder_ids'] = []

        return jsonify({"success": True, "data": plans}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:plan_id>', methods=['GET'])
def get_plan(plan_id):
    try:
        query = "SELECT * FROM kt_plans WHERE id = %s"
        plan = execute_query(query, (plan_id,))
        if not plan:
            return jsonify({"success": False, "message": "Plan not found"}), 404
        return jsonify({"success": True, "data": plan[0]}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:plan_id>/assign-manager', methods=['PUT'])
def assign_manager(plan_id):
    data = request.json
    if 'stakeholder_id' not in data:
        return jsonify({"success": False, "message": "Missing stakeholder_id"}), 400
    try:
        execute_write("UPDATE kt_plans SET created_by = %s WHERE id = %s", (data['stakeholder_id'], plan_id))
        return jsonify({"success": True, "message": "Manager assigned"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/workflow', methods=['POST'])
def run_full_workflow():
    data = request.json
    required_fields = ['application_name', 'scope_description', 'plan_type']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/plans/workflow")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
        
    app_name = data['application_name']
    scope = data['scope_description']
    plan_type = data['plan_type']
    reverse_kt_focus = data.get('reverse_kt_focus')
    
    try:
        from orchestrator import run_workflow
        final_state = run_workflow(app_name, scope, plan_type, reverse_kt_focus)
        return jsonify({"success": True, "message": "Workflow completed", "data": final_state}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:id>/approve', methods=['PUT'])
def approve_plan(id):
    try:
        from services.plan_service import resolve_stakeholder_for_user
        stakeholder_id = None

        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                import jwt
                from config import Config
                payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
                user_email = payload.get('email')
                user_role = payload.get('role')
                user_id = payload.get('sub')
                user_full_name = None
                if user_id:
                    users = execute_query("SELECT full_name FROM users WHERE id = %s", (user_id,))
                    if users:
                        user_full_name = users[0]['full_name']
                if user_email:
                    stakeholder_id = resolve_stakeholder_for_user(user_email, user_full_name, user_role)
            except Exception:
                pass

        if stakeholder_id:
            query = "UPDATE kt_plans SET status = 'approved', approved_by = %s WHERE id = %s"
            execute_write(query, (stakeholder_id, id))
        else:
            query = "UPDATE kt_plans SET status = 'approved' WHERE id = %s"
            execute_write(query, (id,))

        return jsonify({"success": True, "message": "Plan approved successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:id>/close', methods=['PUT'])
def close_plan(id):
    try:
        from services.plan_service import resolve_stakeholder_for_user
        stakeholder_id = None

        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                import jwt
                from config import Config
                payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
                user_email = payload.get('email')
                user_role = payload.get('role')
                user_id = payload.get('sub')
                user_full_name = None
                if user_id:
                    users = execute_query("SELECT full_name FROM users WHERE id = %s", (user_id,))
                    if users:
                        user_full_name = users[0]['full_name']
                if user_email:
                    stakeholder_id = resolve_stakeholder_for_user(user_email, user_full_name, user_role)
            except Exception:
                pass

        if stakeholder_id:
            query = "UPDATE kt_plans SET status = 'closed' WHERE id = %s AND (created_by = %s OR approved_by = %s)"
            execute_write(query, (id, stakeholder_id, stakeholder_id))
        else:
            query = "UPDATE kt_plans SET status = 'closed' WHERE id = %s"
            execute_write(query, (id,))

        return jsonify({"success": True, "message": "Plan closed successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:plan_id>/link-project', methods=['PUT'])
def link_project(plan_id):
    data = request.json
    project_id = data.get('project_id')
    if not project_id:
        return jsonify({"success": False, "message": "Missing project_id"}), 400
    try:
        execute_write("UPDATE kt_plans SET project_id = %s WHERE id = %s", (project_id, plan_id))
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:plan_id>/topics', methods=['GET'])
def get_plan_topic_list(plan_id):
    try:
        query = "SELECT id, day_label, topic_name, estimated_duration_hours FROM plan_topics WHERE plan_id = %s ORDER BY id ASC"
        topics = execute_query(query, (plan_id,))
        return jsonify({"success": True, "data": topics}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:plan_id>/topics/resync', methods=['POST'])
def resync_plan_topics(plan_id):
    try:
        plan_query = "SELECT generated_content FROM kt_plans WHERE id = %s"
        plan = execute_query(plan_query, (plan_id,))
        if not plan:
            return jsonify({"success": False, "message": "Plan not found"}), 404

        from services.plan_service import extract_and_save_topics
        count = extract_and_save_topics(plan_id, plan[0]['generated_content'])
        return jsonify({"success": True, "message": f"Re-synced {count} topics", "data": {"count": count}}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@planning_bp.route('/<int:plan_id>/edit', methods=['PUT'])
def update_plan(plan_id):
    data = request.json
    if 'generated_content' not in data:
        return jsonify({"success": False, "message": "Missing generated_content"}), 400
    try:
        new_content = data['generated_content']
        query = "UPDATE kt_plans SET generated_content = %s WHERE id = %s"
        execute_write(query, (new_content, plan_id))
        
        # Auto re-sync plan_topics when plan content is edited
        from services.plan_service import extract_and_save_topics
        topic_count = extract_and_save_topics(plan_id, new_content)
        
        return jsonify({"success": True, "message": "Plan updated successfully", "topic_count": topic_count}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/<int:plan_id>/topics', methods=['POST'])
def add_plan_topic(plan_id):
    data = request.json or {}
    topic_name = data.get('topic_name')
    if not topic_name:
        return jsonify({"success": False, "message": "Missing topic_name"}), 400
    try:
        from services.plan_service import add_topic_service
        topic_id = add_topic_service(
            plan_id=plan_id,
            day_label=data.get('day_label', 'General'),
            topic_name=topic_name,
            estimated_duration_hours=data.get('estimated_duration_hours', 'N/A')
        )
        return jsonify({"success": True, "message": "Topic added successfully", "data": {"id": topic_id}}), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/topics/<int:topic_id>', methods=['PUT'])
def update_plan_topic(topic_id):
    data = request.json or {}
    try:
        from services.plan_service import update_topic_service
        update_topic_service(
            topic_id=topic_id,
            day_label=data.get('day_label'),
            topic_name=data.get('topic_name'),
            estimated_duration_hours=data.get('estimated_duration_hours')
        )
        return jsonify({"success": True, "message": "Topic updated successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/topics/<int:topic_id>', methods=['DELETE'])
def delete_plan_topic(topic_id):
    try:
        from services.plan_service import delete_topic_service
        delete_topic_service(topic_id)
        return jsonify({"success": True, "message": "Topic deleted successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

