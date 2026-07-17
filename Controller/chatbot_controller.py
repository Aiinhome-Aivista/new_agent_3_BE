from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm

chatbot_bp = Blueprint('chatbot_bp', __name__)

@chatbot_bp.route('/ask', methods=['POST'])
def ask_chatbot():
    data = request.json
    required = ['session_id', 'question']
    if not all(field in data for field in required):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    session_id = data['session_id']
    question = data['question']
    plan_id = data.get('plan_id')
    
    try:
        from rag_service import query_knowledge
        from guardrails import input_rail, dialog_rail, retrieval_rail, output_rail
        
        input_passed, input_reason = input_rail(data, required, "/api/chat/ask")
        if not input_passed:
            return jsonify({"success": False, "message": input_reason}), 400
        
        if plan_id:
            plan_query = "SELECT application_name, scope_description FROM kt_plans WHERE id = %s"
            plan = execute_query(plan_query, (plan_id,))
            context_str = f"Context (Selected Plan): {plan[0] if plan else 'None'}."
            
            chunks = query_knowledge(question, plan_id)
            retrieval_passed, _ = retrieval_rail(chunks, threshold=1.5, endpoint="/api/chat/ask")
            if chunks and retrieval_passed:
                retrieved_context = "\n".join([chunk["text"] for chunk in chunks])
                context_str = f"Uploaded Knowledge Base Context:\n{retrieved_context}\n\n" + context_str
        else:
            plan_query = "SELECT application_name, scope_description FROM kt_plans ORDER BY created_at DESC LIMIT 1"
            plan = execute_query(plan_query)
            context_str = f"Context (Latest Plan): {plan[0] if plan else 'None'}."
        
        prompt = f"""
        You are a helpful AI assistant for the Virtual KT Manager system.
        You are authorized to answer questions about KT plans, status, risks, and importantly, technical concepts, processes, or training content from the uploaded documents.
        
        Treat the 'Uploaded Knowledge Base Context' below as an authoritative source. 
        You must ONLY answer the user's question using the provided Uploaded Knowledge Base Context and the selected plan context. Do not reference other plans or applications if the context contains data for a specific plan.
        If the answer is not in the context, answer generally using your own knowledge but acknowledge that the information wasn't explicitly found in the uploaded KT documents.
        
        {context_str}
        
        User Question: {question}
        """
        
        dialog_passed, dialog_reason = dialog_rail(question, "/api/chat/ask", has_context=bool(plan_id))
        if not dialog_passed:
            answer = "I'm sorry, I am a specialized KT Manager assistant and can only answer questions related to KT plans, schedules, risks, or assessments."
        else:
            answer = call_llm(prompt)
            
            output_passed, output_reason = output_rail(answer, "/api/chat/ask")
            if not output_passed:
                answer = "I'm sorry, my generated response was blocked by security policies (potential PII leakage)."
        
        # Save to chat history
        query = "INSERT INTO chat_history (session_id, question, answer) VALUES (%s, %s, %s)"
        execute_write(query, (session_id, question, answer))
        
        return jsonify({"success": True, "data": {"answer": answer}}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@chatbot_bp.route('/history/<session_id>', methods=['GET'])
def get_history(session_id):
    try:
        query = "SELECT * FROM chat_history WHERE session_id = %s ORDER BY created_at ASC"
        history = execute_query(query, (session_id,))
        return jsonify({"success": True, "data": history}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
