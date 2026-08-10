import json
from db import get_connection

def fix():
    conn = get_connection()
    if not conn:
        print("No DB connection")
        return
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT id, config FROM kt_projects')
    projects = cursor.fetchall()
    
    for p in projects:
        if p['config']:
            try:
                conf = json.loads(p['config'])
                if 'config' in conf and 'tracks' in conf['config']:
                    print(f"Fixing project {p['id']}")
                    fixed_conf = conf['config']
                    cursor.execute('UPDATE kt_projects SET config = %s WHERE id = %s', (json.dumps(fixed_conf), p['id']))
            except Exception as e:
                print("Error on project", p['id'], e)
    
    conn.commit()
    cursor.close()
    conn.close()
    print("Done")

if __name__ == '__main__':
    fix()
