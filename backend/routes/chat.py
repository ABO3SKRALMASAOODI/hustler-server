from flask import Blueprint, request, jsonify, current_app
import jwt
import openai
from functools import wraps
import os
from jwt import ExpiredSignatureError, InvalidTokenError
import psycopg2
from psycopg2.extras import RealDictCursor

chat_bp = Blueprint('chat', __name__)
print("✅ chat.py with GPT-4 is active")

openai.api_key = os.getenv("OPENAI_API_KEY")

# ----- Database Access -----
def get_db():
    return psycopg2.connect(current_app.config['DATABASE_URL'], cursor_factory=RealDictCursor)

# ----- JWT Token Checker -----
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            parts = request.headers['Authorization'].split(" ")
            if len(parts) == 2 and parts[0] == "Bearer":
                token = parts[1]

        if not token:
            return jsonify({'error': 'Token missing'}), 401

        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            user_id = data['sub']
        except ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 403

        return f(user_id, *args, **kwargs)
    return decorated

# ----- Subscription Check -----
def is_user_subscribed(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_subscribed FROM users WHERE id = %s", (user_id,))
    row = cursor.fetchone()
    if row and row["is_subscribed"] == 1:
        return True
    return False

# Decorator for routes that require subscription
def subscription_required(f):
    @wraps(f)
    def decorated(user_id, *args, **kwargs):
        if not is_user_subscribed(user_id):
            return jsonify({'error': 'Subscription required'}), 402
        return f(user_id, *args, **kwargs)
    return decorated

# ----- Basic Chat (No Session) -----
@chat_bp.route('/', methods=['POST'])
@token_required
@subscription_required
def chat(user_id):
    data = request.get_json()
    prompt = data.get('prompt')

    if not prompt:
        return jsonify({'error': 'Prompt is required'}), 400

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a business mentor for entrepreneurs."},
                {"role": "user", "content": prompt}
            ]
        )
        reply = response['choices'][0]['message']['content']
        return jsonify({'reply': reply}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ----- Start New Chat Session -----
@chat_bp.route('/start-session', methods=['POST'])
@token_required
@subscription_required
def start_session(user_id):
    data = request.get_json()
    title = data.get("title", "Untitled Session")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_sessions (user_id, title) VALUES (%s, %s) RETURNING id",
        (user_id, title)
    )
    session_id = cursor.fetchone()['id']
    conn.commit()

    return jsonify({"session_id": session_id}), 201

@chat_bp.route('/send-message', methods=['POST'])
@token_required
@subscription_required
def send_message(user_id):
    data = request.get_json()
    session_id = data.get("session_id")
    prompt = data.get("prompt")

    if not session_id or not prompt:
        return jsonify({'error': 'Missing session_id or prompt'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO chat_messages (session_id, role, content)
        VALUES (%s, %s, %s)
    ''', (session_id, "user", prompt))

    cursor.execute("SELECT role, content FROM chat_messages WHERE session_id = %s ORDER BY created_at ASC", (session_id,))
    all_messages = cursor.fetchall()

    cursor.execute("SELECT title FROM chat_sessions WHERE id = %s", (session_id,))
    session = cursor.fetchone()
    title = session["title"] if session else "Untitled Session"

    print(f"[DEBUG] Session {session_id} title: '{title}' | Message count: {len(all_messages)}")

    if len(all_messages) >= 5 and ("untitled" in title.lower()):
        print(f"[DEBUG] Title rename triggered for session {session_id}. Current title: {title}")
        summary_prompt = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in all_messages[:5]])
        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": f"Summarize the following chat as a short session title (max 6 words):\n\n{summary_prompt}\n\nTitle:"}],
                max_tokens=20,
                temperature=0.3,
            )
            new_title = response.choices[0].message["content"].strip().replace("Title:", "").strip()

            if not new_title or "untitled" in new_title.lower():
                new_title = "Business Chat"

            cursor.execute("UPDATE chat_sessions SET title = %s WHERE id = %s", (new_title, session_id))
            conn.commit()
            print(f"[DEBUG] Session {session_id} title updated to: {new_title}")

        except Exception as e:
            print("Error generating title:", str(e))

    try:
        reply = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a business mentor for entrepreneurs."},
                *[
                    {"role": m["role"], "content": m["content"]}
                    for m in all_messages
                ],
                {"role": "user", "content": prompt}
            ]
        )["choices"][0]["message"]["content"]

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    cursor.execute('''
        INSERT INTO chat_messages (session_id, role, content)
        VALUES (%s, %s, %s)
    ''', (session_id, "assistant", reply))

    conn.commit()
    return jsonify({'reply': reply}), 200

# ----- List All Sessions -----
@chat_bp.route('/sessions', methods=['GET'])
@token_required
@subscription_required
def list_sessions(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, title, created_at FROM chat_sessions WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,)
    )
    sessions = [dict(row) for row in cursor.fetchall()]
    return jsonify({"sessions": sessions})

# ----- Get Messages in a Session -----
@chat_bp.route('/messages/<int:session_id>', methods=['GET'])
@token_required
@subscription_required
def get_session_messages(user_id, session_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM chat_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
    session = cursor.fetchone()
    if not session:
        return jsonify({"error": "Session not found"}), 404

    cursor.execute(
        "SELECT role, content, created_at FROM chat_messages WHERE session_id = %s ORDER BY created_at ASC",
        (session_id,)
    )
    messages = [dict(row) for row in cursor.fetchall()]
    return jsonify({"messages": messages})