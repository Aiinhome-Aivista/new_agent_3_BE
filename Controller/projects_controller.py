from flask import Blueprint, request, jsonify
from services.project_service import create_project, get_projects, get_project_by_id, update_project

projects_bp = Blueprint('projects_bp', __name__)

@projects_bp.route('/', methods=['POST'])
def add_project():
    data = request.json
    user_id = 1 # Fallback
    
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        try:
            import jwt
            from config import Config
            payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
            if payload.get('sub'):
                user_id = payload.get('sub')
        except Exception:
            pass

    result = create_project(data, user_id)
    if result['success']:
        return jsonify(result), 201
    return jsonify(result), 400

@projects_bp.route('/', methods=['GET'])
def list_projects():
    result = get_projects()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400

@projects_bp.route('/<int:project_id>', methods=['GET'])
def get_project(project_id):
    result = get_project_by_id(project_id)
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 404

@projects_bp.route('/<int:project_id>', methods=['PUT'])
def edit_project(project_id):
    data = request.json
    result = update_project(project_id, data)
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400

@projects_bp.route('/upload-template', methods=['POST'])
def upload_template():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400
        
    try:
        from utils.s3_utils import upload_to_s3, log_s3_upload
        import uuid
        import os
        
        user_id = 1 # Fallback
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
            try:
                import jwt
                from config import Config
                payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
                if payload.get('sub'):
                    user_id = payload.get('sub')
            except Exception:
                pass

        bucket_name = os.getenv("AWS_S3_BUCKET_NAME", "agent-initiative-bucket")
        base_folder = os.getenv("AWS_S3_BASE_FOLDER", "Agents_Doc")
        agent_folder = os.getenv("AWS_S3_AGENT_FOLDER", "Agent_13")
        
        original_name = os.path.basename(file.filename)
        extracted_name = os.path.splitext(original_name)[0]
        ext = os.path.splitext(original_name)[1].lower()
        safe_filename = f"project_template_{uuid.uuid4().hex[:8]}_{original_name}"
        
        s3_base_path = f"{base_folder}/{agent_folder}/{extracted_name}"
        s3_key = f"{s3_base_path}/Projects/{safe_filename}"
        
        success, msg = upload_to_s3(file.stream, bucket_name, s3_key)
        if success:
            log_s3_upload(original_name, ext, s3_key, str(user_id), 'Project Template Upload')
            return jsonify({"success": True, "message": "Template uploaded to S3 successfully", "s3_key": s3_key}), 200
        else:
            return jsonify({"success": False, "message": "Failed to upload to S3"}), 500
    except Exception as e:
        print(f"Error in upload_template: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
