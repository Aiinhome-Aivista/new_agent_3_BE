from flask import Blueprint, jsonify
from db import execute_query

guardrails_bp = Blueprint('guardrails_bp', __name__)

@guardrails_bp.route('/', methods=['GET'])
def get_guardrail_logs():
    try:
        query = "SELECT * FROM guardrail_logs ORDER BY created_at DESC"
        logs = execute_query(query)
        return jsonify({"success": True, "data": logs}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
