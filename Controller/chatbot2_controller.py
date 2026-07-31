from flask import Blueprint, request, jsonify
from llm_service import call_llm, load_prompt
from db import execute_query, execute_write
import time

chatbot2_bp = Blueprint("chatbot2_bp", __name__)

@chatbot2_bp.route("/ask", methods=["POST"])
def ask_chatbot2():

    print("Chatbot2 API called")

    data = request.json
    print(data)

    required = ["session_id", "question"]

    if not all(field in data for field in required):
        return jsonify({
            "success": False,
            "message": "Missing required fields"
        }), 400

    session_id = data["session_id"]
    question = data["question"]
    sql = None
    try:
        overall_start = time.time()
        from rag_service import query_knowledge
        from guardrails import input_rail, dialog_rail2, retrieval_rail, output_rail

        input_passed, input_reason = input_rail(
            data,
            required,
            "/api/chatbot2/ask"
        )

        if not input_passed:
            return jsonify({
                "success": False,
                "message": input_reason
            }), 400

        dialog_start = time.time()
        dialog_passed, _ = dialog_rail2(
            question,
            "/api/chatbot2/ask",
            has_context=True
        )
        print("Dialog Rail Time:", time.time() - dialog_start)

        if not dialog_passed:
            answer = "I can only answer questions related to the Virtual KT Manager project."

        else:

            database_keywords = [
                "meeting", "meetings",
                "plan", "plans",
                "attendance",
                "risk", "risks",
                "stakeholder", "stakeholders",
                "topic", "topics",
                "organizer",
                "application",
                "approved", "pending",
                "schedule", "scheduled"
            ]

            if any(keyword in question.lower() for keyword in database_keywords):
                chunks = []
                rag_context = ""
                print("RAG Skipped")
            else:
                rag_start = time.time()
                chunks = query_knowledge(question)
                print("RAG Time:", time.time() - rag_start)

                rag_context = ""

                retrieval_passed, _ = retrieval_rail(
                    chunks,
                    threshold=1.5,
                    endpoint="/api/chatbot2/ask"
                )

                if chunks and retrieval_passed:
                    rag_context = "\n".join(
                        chunk["text"] for chunk in chunks
                    )

            # Step 1: SQL generate
            sql_prompt = load_prompt("chatbot2_sql_generation.txt", question=question)

            sql_start = time.time()
            sql = call_llm(sql_prompt)
            print("SQL Generation Time:", time.time() - sql_start)

            print("Generated SQL:")
            print(repr(sql))
            sql = sql.replace("```sql", "")
            sql = sql.replace("```", "")
            sql = sql.strip()

            if sql.upper().startswith("SQL"):
                sql = sql[3:].strip()

            if not sql.upper().startswith("SELECT"):
                return jsonify({
                    "success": False,
                    "message": "Only SELECT queries are allowed."
                }), 400  
            if "NO_SQL_POSSIBLE" in sql.upper():
                return jsonify({
                    "success": False,
                    "message": "The requested information is not available in the current database schema."
                }), 400
           
            db_start = time.time()
            rows = execute_query(sql)
            if rows:
                db_result = "\n".join(
                    ", ".join(f"{k}: {v}" for k, v in row.items())
                    for row in rows
                )
            else:
                db_result = "No records found."            

            print("Database Time:", time.time() - db_start)
            print("Database Rows:")
            print(rows)

            answer_prompt = load_prompt("chatbot2_answer_generation.txt", rag_context=rag_context, db_result=db_result, question=question)

            answer_start = time.time()
            answer = call_llm(answer_prompt) 
            print("Answer Generation Time:", time.time() - answer_start)
            print("Final Answer:")
            print(answer)
            output_passed, _ = output_rail(answer, "/api/chatbot2/ask")

            if not output_passed:
                answer = "Response blocked."

        execute_write(
            "INSERT INTO chat_history(session_id,question,answer) VALUES(%s,%s,%s)",
            (session_id, question, answer)
        )

        print("Total Response Time:", time.time() - overall_start)
        return jsonify({
            "success": True,
            "data": {
                "answer": answer
            }
        }), 200
    except Exception as e:
        import traceback
        print("Generated SQL:", sql)
        traceback.print_exc()

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500