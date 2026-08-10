from db import get_connection

conn = get_connection()
if conn:
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT id, application_name, project_config FROM kt_plans')
    plans = cursor.fetchall()
    for p in plans:
        print("Plan ID:", p['id'], "Name:", p['application_name'], "Config Meta:", p['project_config'])
    cursor.close()
    conn.close()
