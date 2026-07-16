from flask import Blueprint, request, jsonify, send_file
from db import execute_query, execute_write
from llm_service import call_llm
import os
from datetime import datetime
try:
    from docx import Document
except ImportError:
    Document = None

reporting_bp = Blueprint('reporting_bp', __name__)

REPORTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports_output')

@reporting_bp.route('/weekly', methods=['POST'])
def generate_weekly():
    data = request.json
    if 'plan_id' not in data:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    plan_id = data['plan_id']
    try:
        from services.reporting_service import generate_weekly_service
        result_data = generate_weekly_service(plan_id)
        return jsonify({"success": True, "message": "Weekly report generated", "data": result_data}), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@reporting_bp.route('/final', methods=['POST'])
def generate_final():
    data = request.json
    if 'plan_id' not in data:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    plan_id = data['plan_id']
    try:
        from services.reporting_service import generate_final_service
        result_data = generate_final_service(plan_id)
        return jsonify({"success": True, "message": "Final report generated", "data": result_data}), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@reporting_bp.route('/', methods=['GET'])
def get_reports():
    try:
        query = "SELECT * FROM reports ORDER BY generated_at DESC"
        reports = execute_query(query)
        return jsonify({"success": True, "data": reports}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@reporting_bp.route('/download/<int:id>', methods=['GET'])
def download_report(id):
    try:
        query = "SELECT file_path FROM reports WHERE id = %s"
        report = execute_query(query, (id,))
        if not report:
            return jsonify({"success": False, "message": "Report not found"}), 404
            
        filename = report[0]['file_path']
        filepath = os.path.join(REPORTS_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({"success": False, "message": "File not found on disk"}), 404
            
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
