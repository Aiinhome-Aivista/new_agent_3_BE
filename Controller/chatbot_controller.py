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
        
        # Fetch some recent context (lightweight RAG)
        # In a real app we'd use vector search or at least filter by keyword.
        # Here we just fetch the latest plan and active risks to ground it.
        plan_query = "SELECT application_name, scope_description FROM kt_plans ORDER BY created_at DESC LIMIT 1"
        plan = execute_query(plan_query)
        context_str = f"Context (Latest Plan): {plan[0] if plan else 'None'}."
        
        if plan_id:
            chunks = query_knowledge(question, plan_id)
            from guardrails import retrieval_rail
            retrieval_passed, _ = retrieval_rail(chunks, endpoint="/api/chat/ask")
            if chunks and retrieval_passed:
                retrieved_context = "\n".join([chunk["text"] for chunk in chunks])
                context_str = f"Retrieved Context:\n{retrieved_context}\n\n" + context_str
        
        prompt = f"""
        You are a helpful AI assistant for the Virtual KT Manager system.
        Answer the user's question based on the following context.
        If the answer is not in the context, answer generally but acknowledge you are an AI assistant.
        
        {context_str}
        
        User Question: {question}
        """
        
        from guardrails import dialog_rail
        dialog_passed, dialog_reason = dialog_rail(question, "/api/chat/ask")
        if not dialog_passed:
            answer = "I'm sorry, I am a specialized KT Manager assistant and can only answer questions related to KT plans, schedules, risks, or assessments."
        else:
            answer = call_llm(prompt)
        
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
