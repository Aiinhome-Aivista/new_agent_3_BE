from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from rag_service import add_document
import uuid
import os
import yt_dlp
import speech_recognition as sr
import imageio_ffmpeg
import subprocess

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

def transcribe_audio_from_url(video_url):
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'noplaylist': True
    }
    
    uid = str(uuid.uuid4())
    temp_folder = os.path.join(os.getcwd(), 'temp_audio_files')
    os.makedirs(temp_folder, exist_ok=True)
    
    ydl_opts['outtmpl'] = os.path.join(temp_folder, f'temp_audio_{uid}.%(ext)s')
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            ext = info.get('ext', 'm4a')
    except Exception as e:
        return f"Failed to extract audio from URL. Please ensure it is publicly accessible. Error: {e}"
        
    temp_orig = os.path.join(temp_folder, f"temp_audio_{uid}.{ext}")
    temp_wav = os.path.join(temp_folder, f"temp_audio_{uid}.wav")
    
    try:
        subprocess.run([ffmpeg_path, "-y", "-i", temp_orig, "-ac", "1", "-ar", "16000", temp_wav], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        if os.path.exists(temp_orig): os.remove(temp_orig)
        return f"Conversion failed: {e}"
        
    r = sr.Recognizer()
    full_text = ""
    try:
        with sr.AudioFile(temp_wav) as source:
            while True:
                audio_data = r.record(source, duration=55) # 55 seconds chunk
                if not audio_data.frame_data:
                    break
                try:
                    text = r.recognize_google(audio_data)
                    full_text += text + " "
                except sr.UnknownValueError:
                    pass
                except sr.RequestError as e:
                    full_text += f"[API Error: {e}] "
    except Exception as e:
        full_text = f"Transcription failed: {e}"
        
    if os.path.exists(temp_orig): os.remove(temp_orig)
    if os.path.exists(temp_wav): os.remove(temp_wav)
    
    return full_text.strip()

@knowledge_bp.route('/extract-transcript', methods=['POST'])
def extract_transcript():
    data = request.json
    if not data or not data.get('url'):
        return jsonify({"success": False, "message": "Missing video URL"}), 400
        
    url = data['url']
    
    try:
        transcript_text = transcribe_audio_from_url(url)
        if not transcript_text:
            transcript_text = "Could not transcribe audio (no speech detected)."
            
        return jsonify({
            "success": True, 
            "data": {
                "transcript": transcript_text
            },
            "message": "Transcript extracted successfully"
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": f"Transcription error: {str(e)}"}), 500

@knowledge_bp.route('/upload-transcript', methods=['POST'])
def upload_transcript():
    data = request.json
    if not data:
        return jsonify({"success": False, "message": "Missing JSON body"}), 400
        
    plan_id = data.get('plan_id')
    kt_day = data.get('kt_day')
    text = data.get('text')
    url = data.get('url', 'Unknown URL')
    
    if not plan_id or not text:
        return jsonify({"success": False, "message": "Missing plan_id or text"}), 400
        
    try:
        plan_id = int(plan_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid plan_id"}), 400
        
    try:
        filename = f"Transcript_Day_{kt_day}.txt" if kt_day else f"Transcript_URL.txt"
        
        doc_id = str(uuid.uuid4())
        metadata = {"plan_id": plan_id, "filename": filename, "kt_day": kt_day, "source_url": url}
        
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
            "message": "Transcript processed and added to knowledge base"
        }), 201
        
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

