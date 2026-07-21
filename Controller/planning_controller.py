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
    
    try:
        from services.plan_service import generate_plan_service
        result_data = generate_plan_service(app_name, scope, plan_type, user_email, user_full_name, user_role, reverse_kt_focus)
        
        return jsonify({
            "success": True, 
            "data": result_data,
            "message": "Plan generated successfully"
        }), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@planning_bp.route('/', methods=['GET'])
def get_plans():
    try:
        query = "SELECT * FROM kt_plans ORDER BY created_at DESC"
        plans = execute_query(query)
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

