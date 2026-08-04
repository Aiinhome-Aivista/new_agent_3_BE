from flask import Blueprint, request, jsonify
from db import execute_query, execute_write
from llm_service import call_llm, load_prompt

chatbot_bp = Blueprint('chatbot_bp', __name__)

@chatbot_bp.route('/ask', methods=['POST'])
def ask_chatbot():
    data = request.json
    required = ['session_id', 'question', 'user_id', 'context_id']
    if not all(field in data for field in required):
        return jsonify({"success": False, "message": "Missing required fields"}), 400
        
    session_id = data['session_id']
    question = data['question']
    user_id = data['user_id']
    context_id = data['context_id']
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
        
        prompt = load_prompt("chatbot_qa.txt", context_str=context_str, question=question)
        
        dialog_passed, dialog_reason = dialog_rail(question, "/api/chat/ask", has_context=bool(plan_id))
        if not dialog_passed:
            answer = "I'm sorry, I am a specialized KT Manager assistant and can only answer questions related to KT plans, schedules, risks, or assessments."
        else:
            answer = call_llm(prompt)
            
            output_passed, output_reason = output_rail(answer, "/api/chat/ask")
            if not output_passed:
                answer = "I'm sorry, my generated response was blocked by security policies (potential PII leakage)."
        
        # Save to chat history
        query = "INSERT INTO chat_history (session_id, question, answer, user_id, context_id) VALUES (%s, %s, %s, %s, %s)"
        execute_write(query, (session_id, question, answer, user_id, context_id))
        
        return jsonify({"success": True, "data": {"answer": answer}}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@chatbot_bp.route('/history', methods=['GET'])
def get_history():
    user_id = request.args.get('user_id')
    context_id = request.args.get('context_id')
    
    if not user_id or not context_id:
        return jsonify({"success": False, "message": "Missing user_id or context_id"}), 400

    try:
        query = "SELECT * FROM chat_history WHERE user_id = %s AND context_id = %s ORDER BY created_at ASC"
        history = execute_query(query, (user_id, context_id))
        return jsonify({"success": True, "data": history}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
