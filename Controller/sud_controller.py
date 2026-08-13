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

    sud_folder = os.path.join(os.getcwd(), 'sud_documents')
    os.makedirs(sud_folder, exist_ok=True)

    results = []
    try:
        for file in files:
            if file.filename == '':
                continue
                
            original_name = os.path.basename(file.filename)
            safe_filename = f"plan_{plan_id}_{uuid.uuid4().hex[:8]}_{original_name}"
            dest_path = os.path.join(sud_folder, safe_filename)
            file.save(dest_path)
            
            rel_file_path = f"sud_documents/{safe_filename}"
            
            query = """
                INSERT INTO sud_documents (project_id, plan_id, stakeholder_id, file_path)
                VALUES (%s, %s, %s, %s)
            """
            doc_db_id = execute_write(query, (project_id, plan_id, stakeholder_id, rel_file_path))
            
            results.append({
                "id": doc_db_id,
                "plan_id": plan_id,
                "project_id": project_id,
                "stakeholder_id": stakeholder_id,
                "file_path": rel_file_path,
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
        query = "SELECT * FROM sud_documents WHERE plan_id = %s ORDER BY uploaded_at DESC"
        docs = execute_query(query, (plan_id,))
        formatted = []
        for d in docs:
            fp = d.get('file_path') or ''
            raw_filename = os.path.basename(fp)
            parts = raw_filename.split('_')
            if len(parts) >= 4 and parts[0] == 'plan':
                filename = "_".join(parts[3:])
            else:
                filename = raw_filename
            
            d_copy = dict(d)
            d_copy['filename'] = filename
            d_copy['kt_day'] = 'SUD Document'
            formatted.append(d_copy)
            
        return jsonify({"success": True, "data": formatted}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
