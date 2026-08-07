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
        if conn:
            cursor.close()
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
        
        # Parse JSON config
        for proj in projects:
            if proj.get('config'):
                proj['config'] = json.loads(proj['config'])
                
        return {"success": True, "data": projects}
    except Exception as e:
        print(f"Error fetching projects: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if conn:
            cursor.close()
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
            project['config'] = json.loads(project['config'])
            
        # Fetch associated plans
        cursor.execute("SELECT id, application_name, plan_type, status, created_at FROM kt_plans WHERE project_id = %s ORDER BY created_at DESC", (project_id,))
        plans = cursor.fetchall()
        project['plans'] = plans
        
        return {"success": True, "data": project}
    except Exception as e:
        print(f"Error fetching project: {e}")
        return {"success": False, "message": str(e)}
    finally:
        if conn:
            cursor.close()
            conn.close()
