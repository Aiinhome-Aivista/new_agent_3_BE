from flask import Blueprint, request, jsonify
from services.holiday_service import extract_holiday_info_from_doc_service
import mysql.connector
import os

holiday_bp = Blueprint('holiday_bp', __name__)

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "password"),
        database=os.getenv("DB_NAME", "virtual_kt_manager")
    )

@holiday_bp.route('/upload', methods=['POST'])
def upload_holiday_list():
    try:
        uploaded_files = request.files.getlist('files')
        if not uploaded_files:
            uploaded_files = request.files.getlist('file')

        uploaded_files = [f for f in uploaded_files if f and f.filename != '']
        if not uploaded_files:
            return jsonify({"success": False, "message": "No file uploaded"}), 400
            
        extracted_info = extract_holiday_info_from_doc_service(uploaded_files)
        return jsonify({"success": True, "data": extracted_info}), 200
        
    except Exception as e:
        print(f"Error extracting holiday list: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@holiday_bp.route('/insert', methods=['POST'])
def insert_holidays():
    try:
        data = request.get_json()
        holidays = data.get('holidays', [])
        
        if not holidays:
            return jsonify({"success": False, "message": "No holiday data provided"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        insert_query = """
            INSERT INTO holidays (holiday_date, holiday_name, holiday_year)
            VALUES (%s, %s, %s)
        """
        
        values = []
        for h in holidays:
            values.append((h['date'], h['name'], h['year']))
            
        cursor.executemany(insert_query, values)
        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({"success": True, "message": "Holidays inserted successfully"}), 201

    except Exception as e:
        print(f"Error inserting holidays: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@holiday_bp.route('/', methods=['GET'])
def get_holidays():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM holidays ORDER BY holiday_date ASC")
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return jsonify({"success": True, "data": results}), 200
    except Exception as e:
        print(f"Error fetching holidays: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@holiday_bp.route('/<int:id>', methods=['PUT'])
def update_holiday(id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        update_query = """
            UPDATE holidays 
            SET holiday_date = %s, holiday_name = %s, holiday_year = %s
            WHERE id = %s
        """
        cursor.execute(update_query, (data.get('date'), data.get('name'), data.get('year'), id))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({"success": True, "message": "Holiday updated successfully"}), 200
    except Exception as e:
        print(f"Error updating holiday: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@holiday_bp.route('/<int:id>', methods=['DELETE'])
def delete_holiday(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM holidays WHERE id = %s", (id,))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({"success": True, "message": "Holiday deleted successfully"}), 200
    except Exception as e:
        print(f"Error deleting holiday: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
