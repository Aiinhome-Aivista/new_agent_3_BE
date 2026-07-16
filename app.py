import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS
from config import Config

# Import Blueprints
from Controller.stakeholder_controller import stakeholder_bp
from Controller.planning_controller import planning_bp
from Controller.scheduling_controller import scheduling_bp
from Controller.tracking_controller import tracking_bp
from Controller.risk_controller import risk_bp
from Controller.assessment_controller import assessment_bp
from Controller.reporting_controller import reporting_bp
from Controller.chatbot_controller import chatbot_bp
from Controller.auth_controller import auth_bp
from Controller.knowledge_controller import knowledge_bp
from Controller.guardrails_controller import guardrails_bp

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.instrumentation.flask import FlaskInstrumentor

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Run Database Seeder
    from db_seeder import seed_users
    seed_users()

    # Configure OpenTelemetry (Disabled to avoid connection refused errors)
    # resource = Resource.create({SERVICE_NAME: "kt-manager-backend"})
    # provider = TracerProvider(resource=resource)
    # exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
    # provider.add_span_processor(BatchSpanProcessor(exporter))
    # trace.set_tracer_provider(provider)
    
    # FlaskInstrumentor().instrument_app(app)
    
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Enable CORS
    CORS(app, resources={r"/api/*": {"origins": ["http://localhost:5173", "http://localhost:5174"]}})
    
    # Register Blueprints
    app.register_blueprint(stakeholder_bp, url_prefix="/api/stakeholders")
    app.register_blueprint(planning_bp, url_prefix="/api/plans")
    app.register_blueprint(scheduling_bp, url_prefix="/api/schedule")
    app.register_blueprint(tracking_bp, url_prefix="/api/tracking")
    app.register_blueprint(risk_bp, url_prefix="/api/risks")
    app.register_blueprint(assessment_bp, url_prefix="/api/assessments")
    app.register_blueprint(reporting_bp, url_prefix="/api/reports")
    app.register_blueprint(chatbot_bp, url_prefix="/api/chat")
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(knowledge_bp, url_prefix="/api/knowledge")
    app.register_blueprint(guardrails_bp, url_prefix="/api/guardrails")
    
    @app.route("/api/health")
    def health():
        return jsonify({"success": True, "message": "KT Manager API is running"})

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"success": False, "message": "Endpoint not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"success": False, "message": "Internal server error"}), 500
        
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=Config.FLASK_DEBUG, host="0.0.0.0", port=5000)
