from flask import Blueprint, request, jsonify
from services.project_service import create_project, get_projects, get_project_by_id, update_project

projects_bp = Blueprint('projects_bp', __name__)

@projects_bp.route('/', methods=['POST'])
def add_project():
    data = request.json
    user_id = 1 # Hardcoded for now as in planning_controller
    result = create_project(data, user_id)
    if result['success']:
        return jsonify(result), 201
    return jsonify(result), 400

@projects_bp.route('/', methods=['GET'])
def list_projects():
    result = get_projects()
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400

@projects_bp.route('/<int:project_id>', methods=['GET'])
def get_project(project_id):
    result = get_project_by_id(project_id)
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 404

@projects_bp.route('/<int:project_id>', methods=['PUT'])
def edit_project(project_id):
    data = request.json
    result = update_project(project_id, data)
    if result['success']:
        return jsonify(result), 200
    return jsonify(result), 400
