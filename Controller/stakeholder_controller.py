from flask import Blueprint, request, jsonify
from db import execute_query, execute_write

stakeholder_bp = Blueprint('stakeholder_bp', __name__)

@stakeholder_bp.route('/', methods=['POST'])
def add_stakeholder():
    data = request.json
    required_fields = ['name', 'email', 'role']
    from guardrails import input_rail
    passed, reason = input_rail(data, required_fields, "/api/stakeholders/")
    if not passed:
        return jsonify({"success": False, "message": reason}), 400
    
    try:
        query = "INSERT INTO stakeholders (name, email, role) VALUES (%s, %s, %s)"
        params = (data['name'], data['email'], data['role'])
        stakeholder_id = execute_write(query, params)
        return jsonify({"success": True, "data": {"id": stakeholder_id}, "message": "Stakeholder created successfully"}), 201
    except Exception as e:
        error_msg = str(e)
        if "1062" in error_msg and "Duplicate entry" in error_msg:
            return jsonify({"success": False, "message": "Email already exists"}), 400
        return jsonify({"success": False, "message": error_msg}), 500

@stakeholder_bp.route('/', methods=['GET'])
def get_all_stakeholders():
    try:
        query = "SELECT * FROM stakeholders"
        stakeholders = execute_query(query)
        return jsonify({"success": True, "data": stakeholders}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@stakeholder_bp.route('/<int:id>', methods=['GET'])
def get_stakeholder(id):
    try:
        query = "SELECT * FROM stakeholders WHERE id = %s"
        stakeholders = execute_query(query, (id,))
        if not stakeholders:
            return jsonify({"success": False, "message": "Stakeholder not found"}), 404
        return jsonify({"success": True, "data": stakeholders[0]}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@stakeholder_bp.route('/<int:id>', methods=['PUT'])
def update_stakeholder(id):
    data = request.json
    try:
        # Build dynamic query based on provided fields
        fields = []
        params = []
        for key in ['name', 'email', 'role']:
            if key in data:
                fields.append(f"{key} = %s")
                params.append(data[key])
                
        if not fields:
            return jsonify({"success": False, "message": "No fields to update"}), 400
            
        query = f"UPDATE stakeholders SET {', '.join(fields)} WHERE id = %s"
        params.append(id)
        execute_write(query, tuple(params))
        
        return jsonify({"success": True, "message": "Stakeholder updated successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@stakeholder_bp.route('/<int:id>', methods=['DELETE'])
def delete_stakeholder(id):
    try:
        query = "DELETE FROM stakeholders WHERE id = %s"
        execute_write(query, (id,))
        return jsonify({"success": True, "message": "Stakeholder deleted successfully"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
