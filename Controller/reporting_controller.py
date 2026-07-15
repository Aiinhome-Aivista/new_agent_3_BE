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
os.makedirs(REPORTS_DIR, exist_ok=True)

def generate_report_doc(title, content, filename):
    if not Document:
        raise Exception("python-docx is not installed")
    doc = Document()
    doc.add_heading(title, 0)
    doc.add_paragraph(content)
    filepath = os.path.join(REPORTS_DIR, filename)
    doc.save(filepath)
    return filepath

@reporting_bp.route('/weekly', methods=['POST'])
def generate_weekly():
    data = request.json
    if 'plan_id' not in data:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    plan_id = data['plan_id']
    try:
        # Gather context for LLM
        comp_query = "SELECT AVG(completion_percent) as avg_comp FROM completion_tracking WHERE plan_id = %s"
        comp_res = execute_query(comp_query, (plan_id,))
        avg_comp = float(comp_res[0]['avg_comp']) if comp_res and comp_res[0]['avg_comp'] else 0
        
        risk_query = "SELECT description, severity FROM risks WHERE plan_id = %s AND status != 'resolved'"
        risks = execute_query(risk_query, (plan_id,))
        
        prompt = f"""
        Write a concise narrative summary for a Weekly KT Status Report.
        Overall Completion: {avg_comp}%
        Open Risks: {risks}
        
        Structure the summary with: Progress, Risks & Issues, and Next Steps.
        """
        
        summary = call_llm(prompt)
        
        filename = f"Weekly_Report_{plan_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
        filepath = generate_report_doc(f"Weekly KT Report (Plan {plan_id})", summary, filename)
        
        # Save to DB
        query = "INSERT INTO reports (plan_id, report_type, file_path) VALUES (%s, %s, %s)"
        report_id = execute_write(query, (plan_id, 'weekly', filename))
        
        return jsonify({"success": True, "message": "Weekly report generated", "data": {"id": report_id, "filename": filename}}), 201
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@reporting_bp.route('/final', methods=['POST'])
def generate_final():
    data = request.json
    if 'plan_id' not in data:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    plan_id = data['plan_id']
    try:
        # Gather extensive context for final report
        plan_query = "SELECT application_name FROM kt_plans WHERE id = %s"
        app_name = execute_query(plan_query, (plan_id,))[0]['application_name']
        
        prompt = f"Write a comprehensive Final KT Assessment Report for '{app_name}'. Include an executive summary, assessment of readiness, and sign-off section."
        
        content = call_llm(prompt)
        
        filename = f"Final_Report_{plan_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx"
        filepath = generate_report_doc(f"Final KT Report - {app_name}", content, filename)
        
        query = "INSERT INTO reports (plan_id, report_type, file_path) VALUES (%s, %s, %s)"
        report_id = execute_write(query, (plan_id, 'final', filename))
        
        return jsonify({"success": True, "message": "Final report generated", "data": {"id": report_id, "filename": filename}}), 201
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
