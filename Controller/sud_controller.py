from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
import uuid
import os

sud_bp = Blueprint('sud_bp', __name__)

@sud_bp.route('/upload', methods=['POST'])
def upload_sud_document():
    files = request.files.getlist('files')
    if not files and 'file' in request.files:
        files = [request.files['file']]
        
    if not files:
        return jsonify({"success": False, "message": "No file part"}), 400
        
    plan_id = request.form.get('plan_id')
    if not plan_id:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    try:
        plan_id = int(plan_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid plan_id"}), 400

    # Enforce KA Phase 100% completion rule for SUD document upload
    try:
        from services.tracking_service import get_plan_summary_service
        summary = get_plan_summary_service(plan_id)
        ka_comp = summary.get('avg_completion_percent', 0)
        if ka_comp < 100:
            return jsonify({
                "success": False,
                "message": f"SUD Document Upload is locked. Knowledge Acquisition (KA) Phase must be 100% completed first (Current KA progress: {ka_comp}%)."
            }), 400
    except Exception:
        pass
        
    stakeholder_id = 0
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        try:
            import jwt
            from config import Config
            payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
            user_email = payload.get('email')
            user_id = payload.get('sub')
            
            if user_id:
                stakeholder_id = int(user_id)
            elif user_email:
                users = execute_query("SELECT id FROM users WHERE email = %s", (user_email,))
                if users:
                    stakeholder_id = users[0]['id']
        except Exception:
            pass

    project_id = request.form.get('project_id')
    if not project_id:
        try:
            plan_rows = execute_query("SELECT id, project_id, project_config FROM kt_plans WHERE id = %s", (plan_id,))
            if plan_rows:
                p_row = plan_rows[0]
                if p_row.get('project_id') is not None:
                    project_id = p_row['project_id']
                elif p_row.get('project_config'):
                    import json
                    cfg = p_row['project_config']
                    if isinstance(cfg, str):
                        cfg = json.loads(cfg)
                    if isinstance(cfg, dict):
                        project_id = cfg.get('project_id')
        except Exception:
            pass

    if project_id is not None:
        try:
            project_id = int(project_id)
        except (ValueError, TypeError):
            pass

    results = []
    try:
        from utils.s3_utils import upload_to_s3, log_s3_upload
        bucket_name = os.getenv("AWS_S3_BUCKET_NAME", "agent-initiative-bucket")
        base_folder = os.getenv("AWS_S3_BASE_FOLDER", "Agents_Doc")
        agent_folder = os.getenv("AWS_S3_AGENT_FOLDER", "Agent_13")

        for file in files:
            if file.filename == '':
                continue
                
            original_name = os.path.basename(file.filename)
            safe_filename = f"plan_{plan_id}_{uuid.uuid4().hex[:8]}_{original_name}"
            
            extracted_name = os.path.splitext(original_name)[0]
            ext = os.path.splitext(original_name)[1].lower()
            s3_base_path = f"{base_folder}/{agent_folder}/{extracted_name}"
            s3_key = f"{s3_base_path}/SUD/{safe_filename}"
            
            success, msg = upload_to_s3(file.stream, bucket_name, s3_key)
            if not success:
                return jsonify({"success": False, "message": f"S3 upload failed: {msg}"}), 500
            
            log_s3_upload(original_name, ext, s3_key, str(stakeholder_id), 'SUD')
            
            query = """
                INSERT INTO sud_documents (project_id, plan_id, stakeholder_id, file_path)
                VALUES (%s, %s, %s, %s)
            """
            doc_db_id = execute_write(query, (project_id, plan_id, stakeholder_id, s3_key))
            
            results.append({
                "id": doc_db_id,
                "plan_id": plan_id,
                "project_id": project_id,
                "stakeholder_id": stakeholder_id,
                "file_path": s3_key,
                "filename": original_name
            })
            
        return jsonify({
            "success": True, 
            "data": results,
            "message": f"Successfully uploaded {len(results)} SUD document(s)."
        }), 201
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@sud_bp.route('/plan/<int:plan_id>', methods=['GET'])
def get_sud_documents(plan_id):
    try:
        user_role = None
        stakeholder_id = None
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
                    stakeholder_id = int(user_id)
                elif user_email:
                    users = execute_query("SELECT id FROM users WHERE email = %s", (user_email,))
                    if users:
                        stakeholder_id = users[0]['id']
            except Exception:
                pass

        if user_role and ('incoming' in user_role.lower() or 'receiver' in user_role.lower()) and stakeholder_id:
            query = "SELECT * FROM sud_documents WHERE plan_id = %s AND stakeholder_id = %s ORDER BY uploaded_at DESC"
            docs = execute_query(query, (plan_id, stakeholder_id))
        else:
            query = "SELECT * FROM sud_documents WHERE plan_id = %s ORDER BY uploaded_at DESC"
            docs = execute_query(query, (plan_id,))

        formatted = []
        from utils.s3_utils import generate_presigned_url
        bucket_name = os.getenv("AWS_S3_BUCKET_NAME", "agent-initiative-bucket")
        base_folder = os.getenv("AWS_S3_BASE_FOLDER", "Agents_Doc")

        for d in docs:
            fp = d.get('file_path') or ''
            raw_filename = os.path.basename(fp)
            parts = raw_filename.split('_')
            if len(parts) >= 4 and parts[0] == 'plan':
                filename = "_".join(parts[3:])
            else:
                filename = raw_filename
            
            download_url = fp
            if fp.startswith(base_folder):
                presigned = generate_presigned_url(bucket_name, fp)
                if presigned:
                    download_url = presigned
            elif fp.startswith("sud_documents/"):
                download_url = f"/static/{fp}" # Just a fallback if local files were meant to be accessible
            
            d_copy = dict(d)
            d_copy['filename'] = filename
            d_copy['file_path'] = download_url
            d_copy['kt_day'] = 'SUD Document'
            formatted.append(d_copy)
            
        return jsonify({"success": True, "data": formatted}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
