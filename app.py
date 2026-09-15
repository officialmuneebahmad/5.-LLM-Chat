import os
import requests
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

load_dotenv()
SECRET_API_KEY = os.environ.get("SECRET_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

app = Flask(__name__, template_folder='.', static_folder='.')

@app.route('/')
def index():
    return render_template('index.html', 
                           SUPABASE_URL=os.environ.get("SUPABASE_URL"), 
                           SUPABASE_ANON_KEY=os.environ.get("SUPABASE_ANON_KEY"))

@app.route('/logo.jpg')
def logo():
    from flask import send_from_directory
    return send_from_directory('.', 'logo.jpg')

@app.route('/favicon.png')
def favicon():
    from flask import send_from_directory
    return send_from_directory('.', 'favicon.png')

@app.route('/toc')
def toc():
    return render_template('toc.html')

@app.route('/api/delete-account', methods=['POST'])
def delete_account():
    """Deletes a user from Supabase Auth using the service role key (admin only)."""
    if not SUPABASE_SERVICE_ROLE_KEY:
        return jsonify({"error": "Server not configured for account deletion."}), 500

    data = request.json
    user_id = data.get('user_id') if data else None

    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400

    # Verify the request is from the actual logged-in user by checking their JWT
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({"error": "Unauthorized"}), 401
    user_jwt = auth_header.split(' ', 1)[1]

    # Verify user identity using their JWT against Supabase
    verify_resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "apikey": os.environ.get("SUPABASE_ANON_KEY"),
            "Authorization": f"Bearer {user_jwt}"
        }
    )
    if not verify_resp.ok:
        return jsonify({"error": "Could not verify user identity."}), 401

    verified_user = verify_resp.json()
    if verified_user.get('id') != user_id:
        return jsonify({"error": "User ID mismatch."}), 403

    # Step 1: Delete user's chats (cascades referencing data)
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/chats?user_id=eq.{user_id}",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json"
        }
    )

    # Step 2: Delete user's profile row
    requests.delete(
        f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json"
        }
    )

    # Step 3: Now delete the auth user itself
    delete_resp = requests.delete(
        f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
        }
    )

    if delete_resp.status_code in (200, 204):
        return jsonify({"success": True}), 200
    else:
        try:
            err = delete_resp.json()
        except Exception:
            err = delete_resp.text
        return jsonify({"error": str(err)}), delete_resp.status_code

@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def proxy_chat():
    if request.method == 'OPTIONS':
        return '', 204
        
    data = request.json
    if not data:
        return jsonify({"error": {"message": "Invalid request body: JSON required"}}), 400
        
    url = data.get('url')
    headers = data.get('headers', {})
    body = data.get('body')
    
    if not url:
        return jsonify({"error": {"message": "Missing 'url' parameter"}}), 400

    if SECRET_API_KEY:
        if SECRET_API_KEY.startswith("sk-or-v1"):
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers['Authorization'] = f'Bearer {SECRET_API_KEY}'
            
            # OpenRouter requires specific model prefixes
            model = body.get('model', '')
            if model == 'gpt-4o-mini':
                body['model'] = 'openai/gpt-4o-mini'
            elif model == 'gemini-1.5-flash':
                body['model'] = 'google/gemini-1.5-flash'
            elif model == 'llama-3.3-70b-versatile':
                body['model'] = 'meta-llama/llama-3.3-70b-instruct'
        else:
            if "Authorization" in headers or "openrouter" in url or "openai" in url or "mistral" in url or "deepseek" in url or "groq" in url:
                headers['Authorization'] = f'Bearer {SECRET_API_KEY}'
            elif "x-api-key" in headers or "anthropic" in url:
                headers['x-api-key'] = SECRET_API_KEY

        
    search_type = data.get('search_type')
    TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

    if search_type and TAVILY_API_KEY and 'messages' in body:
        messages = body['messages']
        last_message = next((m['content'] for m in reversed(messages) if m.get('role') == 'user'), None)
        
        if last_message:
            # If multimodal array format, extract the text part
            if isinstance(last_message, list):
                last_message = next((p['text'] for p in last_message if p.get('type') == 'text'), '')
                
            if isinstance(last_message, str) and last_message.strip():
                tavily_url = "https://api.tavily.com/search"
                search_depth = "advanced" if search_type in ["deep", "dark"] else "basic"
                
                payload = {
                    "api_key": TAVILY_API_KEY,
                    "query": last_message,
                    "search_depth": search_depth,
                    "include_answer": True
                }
                
                try:
                    search_res = requests.post(tavily_url, json=payload, timeout=10)
                    if search_res.ok:
                        search_data = search_res.json()
                        answer = search_data.get('answer', '')
                        results = search_data.get('results', [])
                        
                        context = f"[SYSTEM INJECT: User enabled {search_type.upper()} web search. Use the following real-time data to answer.]\n\n"
                        if answer:
                            context += f"Tavily Summary: {answer}\n\n"
                        
                        context += "Sources:\n"
                        for r in results[:5]: # Top 5 results
                            context += f"- [{r.get('title', 'Link')}]({r.get('url')}): {r.get('content')}\n"
                            
                        # Inject as system prompt
                        if messages and messages[0].get('role') == 'system':
                            messages[0]['content'] += f"\n\n{context}"
                        else:
                            messages.insert(0, {'role': 'system', 'content': context})
                        
                        body['messages'] = messages
                except Exception as e:
                    print(f"Search API Error: {e}")

    try:
        # Forward the request to the target LLM API
        # Set a reasonable timeout (e.g. 60 seconds) so it doesn't hang
        resp = requests.post(url, headers=headers, json=body, timeout=60)
        
        status_code = resp.status_code
        
        # If response is JSON, return it as JSON, else as text
        try:
            resp_data = resp.json()
            return jsonify(resp_data), status_code
        except ValueError:
            return resp.text, status_code, {'Content-Type': 'application/json'}
            
    except requests.exceptions.Timeout:
        return jsonify({"error": {"message": "Request timed out after 60 seconds."}}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": {"message": f"Proxy request failed: {str(e)}"}}), 500

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'POST,OPTIONS,GET'
    return response

if __name__ == '__main__':
    # Run server on port 8000
    app.run(host='127.0.0.1', port=8000, debug=True)
