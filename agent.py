"""
Akash Deploy Agent - FastAPI Application

A web service that uses Llama 3.3 70B on AkashML to process natural language
deployment commands for Akash Network.
"""

import os
import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

from akash_core import deploy, list_deployments as list_deployments_core, close_deployment

# Module-level httpx client for connection reuse (thread-safe)
_http_client = httpx.Client(timeout=120.0)


# Configuration
AKASHML_API_BASE = "https://api.akashml.com/v1"
AKASHML_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
AKASHML_API_KEY_ENV = "AKASHML_API_KEY"

app = FastAPI(title="Akash Deploy Agent")

# Add CORS middleware for cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# System prompt for the LLM
SYSTEM_PROMPT = """You are Akash Deploy Agent, an AI assistant that deploys and manages applications on Akash Network, a decentralized cloud computing marketplace.

## Your Capabilities

You can help with:
1. **Deploying applications** - Deploy any Docker container (nginx, redis, postgres, custom apps, etc.)
2. **Checking status** - List all active deployments
3. **Stopping deployments** - Close/terminate deployments by their ID

When users ask general questions like "what can you do?", "help", or "introduce yourself", provide a friendly overview of your capabilities.

## Deployment Guide

When deploying, suggest appropriate images and ports:
- Web server → nginx:latest, port 80
- Redis → redis:7, port 6379
- PostgreSQL → postgres:16, port 5432
- Python app → python:3.11, port 8000
- Node.js app → node:20, port 3000
- Jupyter notebook → jupyter/base-notebook:latest, port 8888

Always confirm what you're about to deploy before doing it. After deploying, share the live URL.
If asked to close everything, list deployments first, then close each one.
Keep responses concise and friendly."""

# Tool definitions for the LLM
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "deploy",
            "description": "Deploy a Docker container to Akash Network decentralized cloud. Use this when the user wants to deploy, launch, or run an application, service, or container.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Docker image to deploy (e.g. 'nginx:latest', 'redis:7', 'python:3.11')"
                    },
                    "port": {
                        "type": "integer",
                        "description": "Container port to expose (e.g. 80, 3000, 8080)"
                    },
                    "env": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Environment variables in KEY=VALUE format"
                    },
                    "cpu": {
                        "type": "string",
                        "description": "CPU units (default: '0.5')"
                    },
                    "memory": {
                        "type": "string",
                        "description": "Memory size (default: '512Mi')"
                    },
                    "storage": {
                        "type": "string",
                        "description": "Storage size (default: '512Mi')"
                    }
                },
                "required": ["image", "port"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_deployments",
            "description": "List all active deployments on Akash Network. Use this when the user asks what's running, what's deployed, or wants to check status.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_deployment",
            "description": "Close/terminate a deployment on Akash Network. Use this when the user wants to stop, close, tear down, or delete a deployment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dseq": {
                        "type": "string",
                        "description": "Deployment sequence ID to close"
                    }
                },
                "required": ["dseq"]
            }
        }
    }
]


def _get_akashml_api_key() -> str:
    """Get AkashML API key from environment."""
    api_key = os.environ.get(AKASHML_API_KEY_ENV)
    if not api_key:
        raise ValueError(f"{AKASHML_API_KEY_ENV} environment variable not set")
    return api_key


def _call_llm(messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Call the AkashML LLM API.

    Args:
        messages: List of message dicts with role and content
        tools: Optional list of tool definitions

    Returns:
        Parsed JSON response from the LLM
    """
    start = time.time()
    api_key = _get_akashml_api_key()
    url = f"{AKASHML_API_BASE}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    body = {
        "model": AKASHML_MODEL,
        "messages": messages,
        "max_tokens": 256  # Limit output length for faster responses
    }

    if tools:
        body["tools"] = tools

    try:
        request_start = time.time()
        response = _http_client.post(url, json=body, headers=headers)
        request_time = time.time() - request_start
        response.raise_for_status()
        total_time = time.time() - start
        print(f"[TIMING] _call_llm total: {total_time:.3f}s (HTTP request: {request_time:.3f}s)")
        return response.json()
    except httpx.HTTPStatusError as e:
        # If AkashML rejects max_tokens parameter, retry without it
        if e.response.status_code == 400 and "max_tokens" in body:
            error_text = e.response.text.lower()
            if any(kw in error_text for kw in ["max_tokens", "invalid request", "unrecognized", "unknown parameter"]):
                # Retry without max_tokens
                body_without_max = {k: v for k, v in body.items() if k != "max_tokens"}
                response = _http_client.post(url, json=body_without_max, headers=headers)
                response.raise_for_status()
                return response.json()
        raise HTTPException(status_code=e.response.status_code, detail=f"AkashML API error: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Network error calling AkashML: {e}")


def _execute_tool_call(tool_name: str, arguments: str) -> str:
    """
    Execute a tool call and return the result as a string.

    Args:
        tool_name: Name of the function to call
        arguments: JSON string of arguments

    Returns:
        String result of the function call
    """
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        return f"Error: Invalid JSON in arguments: {arguments}"

    try:
        if tool_name == "deploy":
            result = deploy(
                image=args.get("image"),
                port=args.get("port"),
                env=args.get("env"),
                cpu=args.get("cpu", "0.5"),
                memory=args.get("memory", "512Mi"),
                storage=args.get("storage", "512Mi")
            )
            if result["success"]:
                return json.dumps({
                    "status": "success",
                    "dseq": result["dseq"],
                    "url": result["url"],
                    "provider": result["provider"],
                    "price": result["price"]
                })
            else:
                return json.dumps({"status": "error", "error": result["error"]})

        elif tool_name == "list_deployments":
            result = list_deployments_core()
            if result["success"]:
                return json.dumps({
                    "status": "success",
                    "deployments": result["deployments"]
                })
            else:
                return json.dumps({"status": "error", "error": result["error"]})

        elif tool_name == "close_deployment":
            result = close_deployment(dseq=args.get("dseq"))
            return json.dumps({
                "status": "success" if result["success"] else "error",
                "dseq": result["dseq"],
                "error": result.get("error")
            })

        else:
            return f"Error: Unknown tool '{tool_name}'"

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def _format_tool_result(tool_name: str, result_json: str) -> str:
    """Format a tool result as a human-readable string. No LLM needed."""
    try:
        result = json.loads(result_json)
    except json.JSONDecodeError:
        return result_json

    if result.get("status") == "error":
        return f"❌ Error: {result.get('error', 'Unknown error')}"

    if tool_name == "deploy":
        url = result.get("url", "pending...")
        return (
            f"✅ Deployed successfully!\n"
            f"• Deployment ID: {result.get('dseq')}\n"
            f"• Provider: {result.get('provider', 'unknown')}\n"
            f"• URL: {url}\n"
            f"• Price: {result.get('price', 'unknown')}"
        )

    elif tool_name == "list_deployments":
        deployments = result.get("deployments", [])
        if not deployments:
            return "No active deployments found."
        lines = ["Active deployments:"]
        for d in deployments:
            lines.append(f"• {d.get('dseq')} — {d.get('state', 'unknown')} (created: {d.get('created_at', '?')})")
        return "\n".join(lines)

    elif tool_name == "close_deployment":
        return f"✅ Deployment {result.get('dseq')} closed successfully."

    return result_json


def _is_tools_unsupported_error(error: HTTPException) -> bool:
    """Check if an error indicates tools parameter is not supported."""
    error_text = str(error.detail).lower()
    # Check for indicators that tools aren't supported
    return any(keyword in error_text for keyword in [
        "tools", "parameters", "unsupported", "unrecognized", "invalid request"
    ])


def _process_message_with_tools(message: str) -> str:
    """
    Process a user message through the LLM with tool calling.

    Args:
        message: User's message

    Returns:
        Final response text
    """
    total_start = time.time()
    print(f"[TIMING] _process_message_with_tools START: {message[:50]}")

    # Skip tools for general questions to avoid unnecessary API calls
    # This prevents the LLM from calling list_deployments for "what can you do" type questions
    general_keywords = ["what can you do", "what do you do", "help", "capabilities", "introduce", "tell me about"]
    use_tools = not any(keyword in message.lower() for keyword in general_keywords)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message}
    ]

    # Try with tools first, fall back to no tools if unsupported
    tools_enabled = True
    llm_start = time.time()
    try:
        response = _call_llm(messages, tools=TOOLS if use_tools else None)
    except HTTPException as e:
        if _is_tools_unsupported_error(e):
            tools_enabled = False
            response = _call_llm(messages, tools=None)
        else:
            raise
    llm_time = time.time() - llm_start
    print(f"[TIMING] First LLM call took: {llm_time:.3f}s")

    choice = response.get("choices", [{}])[0]
    message_obj = choice.get("message", {})
    tool_calls = message_obj.get("tool_calls")

    # If no tool calls, return the LLM content directly
    if not tool_calls:
        total_time = time.time() - total_start
        print(f"[TIMING] TOTAL (no tools): {total_time:.3f}s")
        return message_obj.get("content", "No response from LLM")

    # Execute tool calls and format results in Python (no second LLM call)
    tool_start = time.time()
    results = []
    for tool_call in tool_calls:
        function_name = tool_call["function"]["name"]
        function_args = tool_call["function"]["arguments"]

        # Fix: AkashML returns empty string for arguments when tool has no params
        if function_args == "":
            function_args = "{}"

        exec_start = time.time()
        tool_result = _execute_tool_call(function_name, function_args)
        exec_time = time.time() - exec_start
        print(f"[TIMING] Tool {function_name} took: {exec_time:.3f}s")
        results.append(_format_tool_result(function_name, tool_result))

    tool_time = time.time() - tool_start
    total_time = time.time() - total_start
    print(f"[TIMING] All tools took: {tool_time:.3f}s")
    print(f"[TIMING] TOTAL (with tools): {total_time:.3f}s")
    return "\n\n".join(results)


@app.get("/", response_class=HTMLResponse)
async def get_chat_interface():
    """Serve a minimal HTML chat interface."""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Akash Deploy Agent</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: system-ui, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #1a1a2e; color: #eee; }
        h1 { margin-bottom: 10px; }
        #chat-container { border: 1px solid #333; border-radius: 8px; height: 400px; overflow-y: auto; padding: 15px; background: #16213e; margin-bottom: 15px; }
        .message { margin-bottom: 15px; padding: 10px; border-radius: 6px; }
        .user { background: #0f3460; text-align: right; }
        .assistant { background: #533483; }
        .error { background: #e94560; color: white; }
        .progress { color: #888; font-style: italic; background: transparent; border: none; padding: 5px 10px; }
        #input-container { display: flex; gap: 10px; }
        input { flex: 1; padding: 12px; border: 1px solid #333; border-radius: 6px; background: #16213e; color: #eee; }
        button { padding: 12px 24px; border: none; border-radius: 6px; background: #0f3460; color: #eee; cursor: pointer; }
        button:hover { background: #533483; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .system { font-size: 12px; color: #888; margin-top: 5px; }
        #examples { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
        .example-btn { padding: 6px 14px; border: 1px solid #333; border-radius: 16px; background: transparent; color: #aaa; cursor: pointer; font-size: 13px; }
        .example-btn:hover { border-color: #0f3460; color: #eee; }
        .tagline { color: #888; margin-top: 5px; margin-bottom: 20px; font-size: 14px; }
    </style>
</head>
<body>
    <h1>🚀 Akash Deploy Agent</h1>
    <p class="tagline">Deploy any Docker container to decentralized cloud with natural language</p>
    <div id="chat-container"></div>
    <div id="input-container">
        <input type="text" id="message-input" placeholder="e.g., 'deploy nginx on port 80'" autofocus>
        <button onclick="sendMessage()" id="send-btn">Send</button>
    </div>
    <div id="examples">
        <button class="example-btn" onclick="sendExample(this)">🚀 Deploy nginx</button>
        <button class="example-btn" onclick="sendExample(this)">📋 List deployments</button>
        <button class="example-btn" onclick="sendExample(this)">🗄️ Deploy Redis</button>
        <button class="example-btn" onclick="sendExample(this)">📓 Deploy Jupyter notebook</button>
    </div>

    <script>
        const chatContainer = document.getElementById('chat-container');
        const input = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');

        const originalBtnText = sendBtn.textContent;
        let progressTimers = [];
        let progressEls = [];

        function formatResponse(text) {
            // Make URLs clickable
            const urlRegex = /(https?:\\/\\/[^\\s]+)/g;
            text = text.replace(urlRegex, '<a href="$1" target="_blank" style="color: #6ec6ff;">$1</a>');
            // Convert newlines to <br>
            text = text.replace(/\\n/g, '<br>');
            return text;
        }

        function addMessage(content, type) {
            const div = document.createElement('div');
            div.className = `message ${type}`;
            if (type === 'assistant') {
                div.innerHTML = formatResponse(content);
            } else {
                div.textContent = content;
            }
            chatContainer.appendChild(div);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function showProgress() {
            const progressMessages = [
                { text: "🧠 Thinking...", delay: 0 },
                { text: "📡 Talking to Akash marketplace...", delay: 3000 },
                { text: "⏳ Waiting for provider bids...", delay: 8000 },
                { text: "🔄 Processing...", delay: 15000 },
            ];

            for (const pm of progressMessages) {
                const timer = setTimeout(() => {
                    const el = document.createElement('div');
                    el.className = 'message progress';
                    el.textContent = pm.text;
                    chatContainer.appendChild(el);
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                    progressEls.push(el);
                }, pm.delay);
                progressTimers.push(timer);
            }
        }

        function clearProgress() {
            progressTimers.forEach(t => clearTimeout(t));
            progressEls.forEach(el => el.remove());
            progressTimers = [];
            progressEls = [];
        }

        async function sendMessage() {
            const message = input.value.trim();
            if (!message) return;

            addMessage(message, 'user');
            input.value = '';
            sendBtn.disabled = true;
            sendBtn.textContent = '⏳';

            showProgress();

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message })
                });

                const data = await response.json();
                clearProgress();
                if (data.response) {
                    addMessage(data.response, 'assistant');
                } else if (data.error) {
                    addMessage('Error: ' + data.error, 'error');
                }
            } catch (error) {
                clearProgress();
                addMessage('Error: ' + error.message, 'error');
            } finally {
                sendBtn.disabled = false;
                sendBtn.textContent = originalBtnText;
                input.focus();
            }
        }

        function sendExample(btn) {
            input.value = btn.textContent.replace(/^[^\\w]+/, '').trim();
            sendMessage();
        }

        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });

        // Add welcome message
        addMessage('Hello! I can help you deploy applications to Akash Network. Try asking me to "deploy nginx on port 80" or "list all deployments".', 'assistant');
    </script>
</body>
</html>
"""


@app.post("/chat")
async def chat(request: Request) -> dict[str, str]:
    """
    Process a natural language message and return the agent's response.

    Body: {"message": "deploy nginx on port 80"}

    Returns: {"response": "..."} or {"error": "..."}
    """
    try:
        data = await request.json()
        message = data.get("message", "")

        if not message:
            raise HTTPException(status_code=400, detail="Message is required")

        response_text = _process_message_with_tools(message)

        return {"response": response_text}

    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.get("/status")
async def get_status():
    """
    Get the status of all active deployments.

    Returns: JSON with deployment status
    """
    try:
        result = list_deployments_core()
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
