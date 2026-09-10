import json
from db import get_connection

def create_project(data, user_id):
    conn = get_connection()
    if not conn:
        return {"success": False, "message": "Database connection failed"}
    try:
        cursor = conn.cursor()
        name = data.get("name")
        config = json.dumps(data)
        
        sql = "INSERT INTO kt_projects (name, config, created_by) VALUES (%s, %s, %s)"
        cursor.execute(sql, (name, config, user_id))
        project_id = cursor.lastrowid
        conn.commit()
        return {"success": True, "data": {"id": project_id}}
    except Exception as e:
        print(f"Error creating project: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if conn:
            conn.close()

def filter_checked_options(data):
    if not data:
        return data
    import copy
    new_data = copy.deepcopy(data)
    for t in new_data.get('tracks', []):
        filtered_options = {}
        filtered_inputs = {}
        for k, v in t.get('options', {}).items():
            if v:
                filtered_options[k] = True
                if k in t.get('inputs', {}):
                    filtered_inputs[k] = t['inputs'][k]
        
        new_modules = []
        for m in t.get('modules', []):
            m_opts = {}
            m_inps = {}
            for k, v in m.get('options', {}).items():
                if v:
                    m_opts[k] = True
                    if k in m.get('inputs', {}):
                        m_inps[k] = m['inputs'][k]
            m['options'] = m_opts
            m['inputs'] = m_inps
            new_modules.append(m)
            
        t['options'] = filtered_options
        t['inputs'] = filtered_inputs
        t['modules'] = new_modules
        
    return new_data

def update_project(project_id, data):
    conn = get_connection()
    if not conn:
        return {"success": False, "message": "Database connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        config = json.dumps(data)
        
        sql = "UPDATE kt_projects SET config = %s WHERE id = %s"
        cursor.execute(sql, (config, project_id))
        
        # Also update all kt_plans associated with this project
        cursor.execute("SELECT id, project_config FROM kt_plans WHERE project_id = %s", (project_id,))
        plans = cursor.fetchall()
        
        if plans:
            filtered_project_data = filter_checked_options(data)
            import copy
            
            for plan in plans:
                plan_config_str = plan.get('project_config')
                if plan_config_str:
                    try:
                        plan_config = json.loads(plan_config_str) if isinstance(plan_config_str, str) else plan_config_str
                        meta = plan_config.get('_meta')
                        if meta:
                            new_plan_config = copy.deepcopy(filtered_project_data)
                            new_plan_config['_meta'] = meta
                            
                            cursor.execute(
                                "UPDATE kt_plans SET project_config = %s WHERE id = %s",
                                (json.dumps(new_plan_config), plan['id'])
                            )
                    except Exception:
                        pass
                        
        conn.commit()
        return {"success": True, "data": {"id": project_id}}
    except Exception as e:
        print(f"Error updating project: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if conn:
            conn.close()

def get_projects():
    conn = get_connection()
    if not conn:
        return {"success": False, "message": "Database connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        # Fetch projects with count of plans
        sql = """
            SELECT p.id, p.name, p.config, p.created_by, p.created_at,
                   (SELECT COUNT(*) FROM kt_plans pl WHERE pl.project_id = p.id) as plan_count
            FROM kt_projects p
            ORDER BY p.created_at DESC
        """
        cursor.execute(sql)
        projects = cursor.fetchall()
        
        # Parse JSON config safely
        for proj in projects:
            if proj.get('config'):
                if isinstance(proj['config'], str):
                    try:
                        proj['config'] = json.loads(proj['config'])
                    except Exception:
                        pass
                
        return {"success": True, "data": projects}
    except Exception as e:
        print(f"Error fetching projects: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if conn:
            conn.close()

def get_project_by_id(project_id):
    conn = get_connection()
    if not conn:
        return {"success": False, "message": "Database connection failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM kt_projects WHERE id = %s", (project_id,))
        project = cursor.fetchone()
        
        if not project:
            return {"success": False, "message": "Project not found"}
            
        if project.get('config'):
            if isinstance(project['config'], str):
                try:
                    project['config'] = json.loads(project['config'])
                except Exception:
                    pass
            
        # Fetch associated plans instantly
        cursor.execute("SELECT * FROM kt_plans WHERE project_id = %s ORDER BY created_at DESC", (project_id,))
        plans = cursor.fetchall()
        for p in plans:
            if p.get('project_config') and isinstance(p['project_config'], str):
                try:
                    p['project_config'] = json.loads(p['project_config'])
                except Exception:
                    pass
        project['plans'] = plans
        
        return {"success": True, "data": project}
    except Exception as e:
        print(f"Error fetching project: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if conn:
            conn.close()
