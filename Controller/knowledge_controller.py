from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from rag_service import add_document
import uuid
import os

knowledge_bp = Blueprint('knowledge_bp', __name__)

@knowledge_bp.route('/upload', methods=['POST'])
def upload_document():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file part"}), 400
        
    file = request.files['file']
    plan_id = request.form.get('plan_id')
    kt_day = request.form.get('kt_day')
    
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400
        
    if not plan_id:
        return jsonify({"success": False, "message": "Missing plan_id"}), 400
        
    try:
        plan_id = int(plan_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid plan_id"}), 400
        
    try:
        filename = file.filename
        
        if filename == 'CONFLUENCE_SYNC.txt':
            from connectors import ConfluenceConnector
            confluence = ConfluenceConnector()
            kb_chunks = confluence.fetch_kb_from_confluence()
            
            text = "\n".join(kb_chunks)
            filename = 'confluence_auto_sync'
            ext = '.txt'
        else:
            ext = os.path.splitext(filename)[1].lower()
            text = ""
            
            if ext == '.pdf':
                import pypdf
                pdf_reader = pypdf.PdfReader(file)
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"
            elif ext in ['.docx', '.doc', '.docs']:
                import docx
                doc = docx.Document(file)
                for para in doc.paragraphs:
                    text += para.text + "\n"
            elif ext in ['.ppt', '.pptx']:
                from pptx import Presentation
                prs = Presentation(file)
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text"):
                            text += shape.text + "\n"
            elif ext == '.txt':
                text = file.read().decode('utf-8', errors='ignore')
            else:
                return jsonify({"success": False, "message": "Unsupported file type"}), 400
            
        doc_id = str(uuid.uuid4())
        metadata = {"plan_id": plan_id, "filename": filename, "kt_day": kt_day}
        
        chunk_count = add_document(doc_id, text, metadata)
        
        query = """
            INSERT INTO knowledge_documents (plan_id, kt_day, filename, chunk_count)
            VALUES (%s, %s, %s, %s)
        """
        doc_db_id = execute_write(query, (plan_id, kt_day, filename, chunk_count))
        
        return jsonify({
            "success": True, 
            "data": {
                "id": doc_db_id,
                "plan_id": plan_id,
                "kt_day": kt_day,
                "filename": filename,
                "chunk_count": chunk_count
            },
            "message": "Document processed and added to knowledge base"
        }), 201
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@knowledge_bp.route('/plan/<int:plan_id>', methods=['GET'])
def get_plan_documents(plan_id):
    try:
        query = "SELECT * FROM knowledge_documents WHERE plan_id = %s ORDER BY uploaded_at DESC"
        docs = execute_query(query, (plan_id,))
        return jsonify({"success": True, "data": docs}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
