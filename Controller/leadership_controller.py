from flask import Blueprint, jsonify
from services.leadership_service import get_manager_wise_summary, get_manager_wise_risk_summary

leadership_bp = Blueprint('leadership_bp', __name__)

@leadership_bp.route('/completion-summary', methods=['GET'])
def get_completion_summary():
    try:
        summary = get_manager_wise_summary()
        return jsonify({"success": True, "data": summary}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@leadership_bp.route('/risk-summary', methods=['GET'])
def get_risk_summary():
    try:
        summary = get_manager_wise_risk_summary()
        return jsonify({"success": True, "data": summary}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
