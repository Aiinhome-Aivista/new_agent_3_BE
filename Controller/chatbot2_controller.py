from flask import Blueprint, request, jsonify
from llm_service import call_llm
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
            sql_prompt = f"""
            You are an expert MySQL SQL generator.

            Database Schema:

            kt_plans(id, application_name, plan_type, status, created_at)
            meetings(id, plan_id, title, scheduled_at, organizer_id, status, description, meeting_link)
            attendance(id, meeting_id, stakeholder_id, attended, notes)
            stakeholders(id, name, email, role)
            risks(id, plan_id, description, severity, status, jira_ticket_ref)

            Relationships:
            - meetings.plan_id = kt_plans.id
            - attendance.meeting_id = meetings.id
            - attendance.stakeholder_id = stakeholders.id
            - risks.plan_id = kt_plans.id
            
            Rules:
            1. Generate ONLY one valid MySQL SELECT query.
            2. Use ONLY the tables, columns and relationships provided.
            3. Return ONLY the SQL query.
            4. Do NOT include explanations, comments, markdown or ```sql.
            5. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE or TRUNCATE statements.
            6. Never invent tables, columns, IDs or values.
            7. Do not assume application names, meeting titles or statuses.
            8. If the user does not specify a filter, do not add unnecessary WHERE conditions.
            9. Use JOIN whenever information exists in related tables.
            10. Use COUNT() for "how many" questions.
            11. Use ORDER BY and LIMIT for latest, last, recent, highest, lowest and next.
            12. For "today", use DATE(scheduled_at)=CURDATE().
            13. For "next meeting", use scheduled_at > NOW() ORDER BY scheduled_at ASC LIMIT 1.
            14. For "last meeting", use ORDER BY scheduled_at DESC LIMIT 1.
            15. Always use the simplest correct MySQL query.
            16. If the question cannot be answered using the given schema, return exactly:
            SELECT 'NO_SQL_POSSIBLE';

            Question:
            {question}
            """

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

            answer_prompt = f"""
            You are a Virtual KT Manager assistant.

            Knowledge Context:
            {rag_context}

            Database Result:
            {db_result}

            User Question:
            {question}

            Instructions:
            - Prefer the database result whenever it contains the answer.
            - The database result is the source of truth.
            - If the database result contains values such as counts, dates, names or links, convert them into a natural English answer.
            - Never ignore a non-empty database result.
            - Use the knowledge context only if the database result is empty or does not answer the question.
            - Do not combine unrelated information.
            - Do not make assumptions.
            - Use clear and professional English.
            - If one record exists, answer in one sentence.
            - If multiple records exist, return a bullet list.
            - If the result contains counts, dates, names, links, statuses, or other values, explain them naturally.
            - If neither the database result nor the knowledge context contains the answer, reply exactly:
            "I couldn't find this information."
            Answer:
            """

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