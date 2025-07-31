from flask import Flask, request, jsonify
from flask_cors import CORS
from ravendb import DocumentStore
import logging
import re
import uuid 
from openai import OpenAI

app = Flask(__name__)
CORS(app)

# --- Logging Configuration ---
# Configure the Flask app's logger directly to avoid duplicate logs.
# This setup ensures logs are clean and only show what's necessary.
log_format = '%(asctime)s - %(message)s'
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(log_format, "%H:%M:%S"))

# Clear existing handlers and add our custom one
app.logger.handlers.clear()
app.logger.addHandler(handler)
app.logger.propagate = False 

if not app.debug:
    app.logger.setLevel(logging.INFO)
else:
    # Debug mode will show more verbose output
    app.logger.setLevel(logging.DEBUG)


# --- OpenAI Configuration ---
# IMPORTANT: Replace "YOUR_OPENAI_API_KEY" with an actual OpenAI API key.
# For production, consider using environment variables or a secure secret manager.
OPENAI_API_KEY = "" # Your OpenAI API Key goes here
OPENAI_MODEL = "gpt-4o-mini"
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# --- RavenDB Configuration ---
RAVENDB_URLS = ["http://localhost:8080"]
RAVENDB_DATABASE_NAME = "RAG_Chatbot"
RAVENDB_COLLECTION_NAME = "Context"
RAVENDB_SEARCH_FIELD = "Content"

store = DocumentStore(urls=RAVENDB_URLS, database=RAVENDB_DATABASE_NAME)
try:
    store.initialize()
    app.logger.info("Successfully connected to RavenDB.")
except Exception as e:
    app.logger.error(f"FATAL: Could not connect to RavenDB: {e}", exc_info=True)
    store = None

# --- Configuration Constants ---
MAX_MESSAGES_BEFORE_SUMMARY = 7   # Number of messages before summarization
ILLEGAL_PROMPT_THRESHOLD = 3      # Number of max consecutive illegal prompts before lockout
K_RETRIEVAL_CHUNKS = 5            # Number of chunks to retrieve from RavenDB


# --- System-Level Engineered Instructions for LLM ---
SYSTEM_INSTRUCTION = {
  "role": "system",
  "content": """You are Grip, an expert IT assistant designed to support users of RavenDB. You operate as a Retrieval-Augmented Generation (RAG) system and rely primarily on retrieved documentation chunks provided in the current session.

Your core directives:
1. Use the provided context chunks as your main source of truth. When answering, rely heavily on these chunks.
2. If you find relevant and accurate knowledge based on your internal training about RavenDB, you may cautiously supplement your answer—but only when it aligns with or clearly extends the retrieved content. Prioritize the retrieved content.
3. If the retrieved chunks are insufficient to confidently answer the question, explicitly state that you require more context rather than guessing or speculating. But never refer to chunks, only to context.
4. Never fabricate facts. Do not invent features, capabilities, or behaviors of RavenDB.
5. Do not expose your retrieval mechanism, internal architecture, or the existence of context chunks to the user. Ever. Don't mention this at all.
6. Always prioritize security, precision, and clarity. Use formal, professional language. Be somewhat concise but complete in your answers.
7. If prior messages exist in the conversation, consider them when forming your answer. They will be provided.
8. Do not impersonate a human. Do not use expressions that imply emotions or consciousness.
9. If there are examples in the provided context chunks, use them and show them to the user.
10. If user requests help regarding practical usage of RavenDB sofware, provide a very hands on, practical steps needed for his usecase.
11. You are allowed to explain you'r name, it's the only none RavenDB related topic you're allowed to adress. You are named after the famous talking raven "Grip" because you are a chatbot of RavenDB who's logo is a Raven. you can use you'r general knowlage about grip and RavenDB for this question, and only this question. this is the only exception to ignoring the context chunks.
12. Under no circumstances should you generate, use, or refer to any web links or URLs.

Your purpose is to provide reliable, context-grounded assistance about RavenDB usage, troubleshooting, configuration, and best practices. When context is lacking, suggest actionable next steps or clarify what further input is needed. Never lie to the user or make things up."""
}

# --- Session Management ---
chat_sessions = {}

def check_message_legality(user_message):
    """Checks if a user message is related to RavenDB using OpenAI."""
    legality_prompt = f"""You are an expert RavenDB assistant named "Grip". You are being asked to validate whether a user's query is related to RavenDB or its general usage context.

Rules:
- If the question is general enough that it can be assumed to be about a document database (e.g., "How do I index data?"), return TRUE.
- If the question directly mentions other technologies unrelated to RavenDB (e.g., MongoDB, MySQL, Oracle), return FALSE.
- If the question compares RavenDB to another technology, return TRUE.
- One eception to the rule, if a user asks you about your name, as an entity, it's legal.
- Do NOT explain your decision. Return only one word: "true" or "false".

Now evaluate the following user prompt:
"{user_message}"
"""

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": legality_prompt}],
            max_tokens=10
        )
        result = response.choices[0].message.content.strip().lower()
        is_legal = result == "true"
        app.logger.info(f"Query Legality Check: '{'LEGAL' if is_legal else 'ILLEGAL'}'.")
        return is_legal
    except Exception as e:
        app.logger.error(f"Failed to check message legality: {e}")
        return True  # Default to legal if check fails

def enhance_query_for_search(user_message):
    """Enhances user message for better semantic search."""
    enhancement_prompt = f"""Enhance this user message to improve semantic similarity search over embedded documentation about RavenDB. Do not change the meaning, only improve searchability:
"{user_message}"
"""

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": enhancement_prompt}],
            max_tokens=100
        )
        enhanced_query = response.choices[0].message.content.strip()
        app.logger.debug(f"Enhanced query for search: '{enhanced_query}' (Original: '{user_message}')")
        return enhanced_query
    except Exception as e:
        app.logger.error(f"Failed to enhance query: {e}")
        return user_message  # Return original if enhancement fails

def generate_session_title(first_user_message):
    """Generates a concise title for the chat session using OpenAI."""
    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "user", "content": f"Generate a concise 3–5 word title for a chat session that begins with this message:\n\"{first_user_message}\""}
            ],
            max_tokens=20
        )
        title = response.choices[0].message.content.strip()
        title = re.sub(r'^["\']|["\']$', '', title).strip()
        app.logger.debug(f"Generated session title: '{title}'")
        return title if title else "New Chat"
    except Exception as e:
        app.logger.error(f"Failed to generate session title: {e}")
        return "New Chat"

def generate_conversation_summary(conversation_history):
    """Generates a summary of conversation history for memory efficiency."""
    if not conversation_history:
        return ""
    
    conversation_text = ""
    for msg in conversation_history:
        role = "User" if msg['role'] == 'user' else "Assistant"
        conversation_text += f"{role}: {msg['content']}\n"
    
    summary_prompt = f"""Summarize the following conversation history into a compact form that preserves all technical intent and context. Output should be suitable for re-use in a future system prompt.

{conversation_text}
"""

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            max_tokens=500
        )
        summary = response.choices[0].message.content.strip()
        app.logger.debug(f"Generated conversation summary. Length: {len(summary)} characters.")
        return summary
    except Exception as e:
        app.logger.error(f"Failed to generate conversation summary: {e}")
        return ""

# --- Endpoint to Get All Sessions for Sidebar ---
@app.route('/get_sessions', methods=['GET'])
def get_sessions():
    session_previews = []
    for session_id, data in chat_sessions.items():
        session_previews.append({
            "id": session_id,
            "preview": data.get('title', 'New Chat'),
            "is_locked": data.get('is_locked', False)
        })
    app.logger.debug(f"Providing list of {len(session_previews)} sessions for sidebar.")
    return jsonify(session_previews)

# --- Endpoint to Get a Specific Session's History ---
@app.route('/get_session_history/<session_id>', methods=['GET'])
def get_session_history(session_id):
    session_data = chat_sessions.get(session_id)
    if session_data:
        app.logger.debug(f"Fetching history for session: {session_id}")
        return jsonify({
            "history": session_data['history'],
            "is_locked": session_data.get('is_locked', False)
        })
    return jsonify({"error": "Session not found"}), 404

# --- New Chat Endpoint ---
@app.route('/new_chat', methods=['POST'])
def new_chat_session():
    session_id = str(uuid.uuid4())
    chat_sessions[session_id] = {
        "history": [],
        "title": "New Chat",
        "illegal_count": 0,
        "is_locked": False,
        "conversation_summary": ""
    }
    app.logger.info(f"New chat session created: {session_id}")
    return jsonify({"session_id": session_id}), 201

# --- Delete Session Endpoint ---
@app.route('/delete_session/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    if session_id in chat_sessions:
        del chat_sessions[session_id]
        app.logger.info(f"Deleted session: {session_id}")
        return jsonify({"success": True}), 200
    return jsonify({"error": "Session not found"}), 404

# --- Clear All Sessions Endpoint ---
@app.route('/clear_all_sessions', methods=['POST'])
def clear_all_sessions():
    global chat_sessions
    count = len(chat_sessions)
    chat_sessions = {}
    app.logger.info(f"Cleared all {count} chat sessions.")
    return jsonify({"success": True}), 200

@app.route('/chat', methods=['POST'])
def chat_with_rag_and_ravendb():
    data = request.get_json()
    user_message = data.get('message')
    session_id = data.get('session_id')

    if not all([user_message, session_id]):
        return jsonify({"error": "Message and session_id are required"}), 400

    if session_id not in chat_sessions:
        return jsonify({"error": "Invalid session_id"}), 404

    current_session = chat_sessions[session_id]
    
    if current_session.get('is_locked', False):
        app.logger.warning(f"Denied request for locked session {session_id}.")
        return jsonify({"error": "Session is locked due to repeated invalid queries"}), 423

    conversation_history = current_session['history']
    
    is_legal = check_message_legality(user_message)
    
    if not is_legal:
        current_session['illegal_count'] = current_session.get('illegal_count', 0) + 1
        app.logger.warning(f"Illegal message detected in session {session_id}. Count: {current_session['illegal_count']}/{ILLEGAL_PROMPT_THRESHOLD}.")
        
        if current_session['illegal_count'] >= ILLEGAL_PROMPT_THRESHOLD:
            current_session['is_locked'] = True
            app.logger.error(f"SESSION LOCKED: Session {session_id} locked due to too many illegal prompts.")
            return jsonify({"error": "Session locked due to repeated off-topic queries. Please start a new chat."}), 423
        
        error_response = "I'm designed to help with RavenDB-related questions. Please ask something related to RavenDB, document databases, or database management."
        return jsonify({"reply": error_response}), 400
    
    if current_session['illegal_count'] > 0:
        app.logger.info(f"Legal message received. Resetting illegal count for session {session_id}.")
        current_session['illegal_count'] = 0
    
    if not conversation_history and current_session['title'] == "New Chat":
        current_session['title'] = generate_session_title(user_message)
        app.logger.info(f"Session {session_id} titled: '{current_session['title']}'")

    enhanced_query = enhance_query_for_search(user_message)

    retrieved_context_texts = []
    
    if store:
        with store.open_session() as session:
            try:
                rql_query = f"""
                from {RAVENDB_COLLECTION_NAME} as a
                where vector.search(embedding.text(a.{RAVENDB_SEARCH_FIELD}), $userInputQuery, 0.8)
                select {{
                    Id: id(a),
                    Content: a.{RAVENDB_SEARCH_FIELD},
                    Title: a.Title
                }}
                limit {K_RETRIEVAL_CHUNKS}
                """
                results = list(session.advanced.raw_query(rql_query, dict).add_parameter("userInputQuery", enhanced_query))

                if results:
                    chunk_titles = [f"'{chunk.get('Title', 'N/A')}'" for chunk in results]
                    app.logger.info(f"Retrieved {len(results)} chunks. Titles: {', '.join(chunk_titles)}")
                    
                    for chunk in results:
                        content = chunk.get("Content", "")
                        if content:
                            retrieved_context_texts.append(f"Chunk (Title: {chunk.get('Title', 'N/A')}): {content}")
                else:
                    app.logger.info("No relevant context chunks retrieved from RavenDB.")

            except Exception as e:
                app.logger.error(f"RavenDB query failed: {e}", exc_info=True)
    
    messages_for_openai_api = [SYSTEM_INSTRUCTION]
    
    if current_session.get('conversation_summary'):
        summary_message = {
            "role": "system", 
            "content": f"Previous conversation summary: {current_session['conversation_summary']}"
        }
        messages_for_openai_api.append(summary_message)
    
    if len(conversation_history) <= MAX_MESSAGES_BEFORE_SUMMARY:
        messages_for_openai_api.extend(conversation_history)
    else:
        if not current_session.get('conversation_summary'):
            app.logger.info("Conversation history is long, generating a summary.")
            current_session['conversation_summary'] = generate_conversation_summary(conversation_history)
            summary_message = {
                "role": "system", 
                "content": f"Previous conversation summary: {current_session['conversation_summary']}"
            }
            messages_for_openai_api.append(summary_message)
        
        recent_messages = conversation_history[-(MAX_MESSAGES_BEFORE_SUMMARY//2):]
        messages_for_openai_api.extend(recent_messages)

    context_for_llm = "\n\n".join(retrieved_context_texts)
    if context_for_llm:
        context_for_llm = f"Relevant retrieved context chunks:\n---\n{context_for_llm}\n---"
    else:
        context_for_llm = "No specific relevant context was found in the knowledge base."

    user_turn_message_parts = [{"type": "text", "text": f"{user_message}\n\n{context_for_llm}"}]
    
    messages_for_openai_api.append({"role": "user", "content": user_turn_message_parts})

    try:
        app.logger.debug(f"Sending request to OpenAI with {len(messages_for_openai_api)} messages.")
        openai_response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages_for_openai_api,
            stream=False,
            temperature=0.7
        )
        
        bot_reply = openai_response.choices[0].message.content.strip() or "I apologize, I couldn't formulate a response."

        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": bot_reply})
        
        response_payload = {"reply": bot_reply}
             
        return jsonify(response_payload)

    except Exception as e:
        app.logger.error(f"OpenAI API request failed: {e}", exc_info=True)
        return jsonify({"error": f"Failed to communicate with the language model: {e}"}), 503


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)