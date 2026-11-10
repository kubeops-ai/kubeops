"""FastAPI application and small browser UI for KubeOps questions."""

import logging
import os
from uuid import uuid4
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from langchain_openai import ChatOpenAI
from langgraph.types import Command
from supervisor_agent import run_supervisor_agent
from agent_workflow import HumanApprovalRequired, delete_chat_history
from chat_store import (
    add_message,
    create_chat,
    delete_chat,
    get_messages,
    list_chats,
    rename_chat,
    setup_chat_table,
    touch_chat,
)


load_dotenv(Path(__file__).with_name(".env"))
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kubeops.web")

app = FastAPI(title="KubeOps Assistant")
DEFAULT_USER_ID = "local-user"
pending_approvals = {}


@app.on_event("startup")
def startup():
    """Create PostgreSQL chat metadata tables when the API starts."""
    setup_chat_table()


class QuestionRequest(BaseModel):
    """Question received from the browser."""

    question: str
    chat_id: str
    user_id: str = DEFAULT_USER_ID


class AnswerResponse(BaseModel):
    """Answer returned to the browser."""

    answer: str
    approval_required: bool = False
    approval_id: str | None = None
    approval_message: str | None = None


class ChatRequest(BaseModel):
    """Chat identity sent by the browser."""

    user_id: str = DEFAULT_USER_ID


class RenameChatRequest(BaseModel):
    """New display name for a chat."""

    name: str


@app.get("/", response_class=HTMLResponse)
def home_page():
    """Display the simple question form."""
    return """
<!doctype html>
<html>
<head>
  <title>KubeOps Assistant</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: Arial, sans-serif; margin: 0; background: #fff; color: #202123; }
    .app { display: flex; height: 100vh; }
    aside { width: 260px; background: #202123; color: white; padding: 14px; display: flex; flex-direction: column; }
    .brand { font-size: 18px; font-weight: bold; padding: 10px 8px 18px; }
    .new-chat, .delete-chat { width: 100%; border: 1px solid #565869; background: transparent; color: white; padding: 10px; border-radius: 6px; cursor: pointer; text-align: left; }
    .delete-chat { margin-top: 8px; color: #ffb4b4; }
    #chatList { margin-top: 14px; overflow-y: auto; }
    .chat-row { display: flex; align-items: center; gap: 6px; margin: 4px 0; }
    .chat-item { flex: 1; border: 0; background: transparent; color: #ddd; padding: 10px; border-radius: 6px; cursor: pointer; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .chat-item:hover, .chat-item.selected { background: #343541; color: white; }
    main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    header { border-bottom: 1px solid #e5e5e5; padding: 18px 6%; font-size: 18px; font-weight: bold; }
    #chat { flex: 1; overflow-y: auto; padding: 28px 12%; }
    .message { white-space: pre-wrap; padding: 16px; margin: 14px 0; border-radius: 8px; line-height: 1.5; }
    .user { background: #f7f7f8; margin-left: 12%; }
    .assistant { background: #fff; border: 1px solid #eee; margin-right: 12%; }
    .composer { padding: 16px 12%; border-top: 1px solid #e5e5e5; }
    textarea { width: 100%; min-height: 54px; max-height: 180px; padding: 14px; font-size: 15px; resize: vertical; border: 1px solid #c5c5c5; border-radius: 8px; }
    .send-row { display: flex; align-items: center; gap: 12px; }
    #askButton { margin-top: 8px; background: #19c37d; color: white; border: 0; padding: 10px 18px; border-radius: 6px; cursor: pointer; }
    #askButton:disabled { opacity: .6; cursor: wait; }
    #status { color: #666; margin-top: 8px; display: inline-block; }
    @media (max-width: 700px) { aside { width: 190px; } #chat, .composer { padding-left: 5%; padding-right: 5%; } }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand">KubeOps AI</div>
      <button class="new-chat" onclick="newChat()">＋ New chat</button>
      <div id="chatList"></div>
      <button class="delete-chat" onclick="renameChat()">Rename selected chat</button>
      <button class="delete-chat" onclick="deleteChat()">Delete selected chat</button>
    </aside>
    <main>
      <header>Kubernetes Operations Assistant</header>
      <div id="chat"><div class="message assistant">Hello. How can I help with your Kubernetes cluster?</div></div>
      <div class="composer">
        <textarea id="question" placeholder="Message KubeOps AI..."></textarea>
        <div class="send-row"><button id="askButton" onclick="askQuestion()">Send</button><span id="status"></span></div>
      </div>
    </main>
  </div>
  <script>
    const chat = document.getElementById('chat');
    const chatList = document.getElementById('chatList');
    const questionBox = document.getElementById('question');
    const askButton = document.getElementById('askButton');
    const statusText = document.getElementById('status');

    let chats = {};
    let activeChat = null;

    async function loadChats() {
      const response = await fetch('/chats');
      const data = await response.json();
      chats = Object.fromEntries(data.map(item => [item.chat_id, {name: item.name, messages: []}]));
      activeChat = Object.keys(chats)[0] || null;
      if (!activeChat) await newChat(); else { renderChatList(); await loadMessages(); }
    }

    async function newChat() {
      const response = await fetch('/chats', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
      const item = await response.json();
      activeChat = item.chat_id; chats[activeChat] = {name: item.name, messages: []};
      renderChatList(); await loadMessages();
    }

    function renderChatList() {
      chatList.innerHTML = '';
      Object.entries(chats).forEach(([id, item]) => {
        const row = document.createElement('div'); row.className = 'chat-row';
        const button = document.createElement('button');
        button.className = 'chat-item' + (id === activeChat ? ' selected' : '');
        button.textContent = item.name;
        button.onclick = () => switchChat(id);
        row.appendChild(button); chatList.appendChild(row);
      });
    }

    function renderMessages() {
      chat.innerHTML = '';
      (chats[activeChat].messages.length ? chats[activeChat].messages : [['Hello. How can I help with your Kubernetes cluster?', 'assistant']]).forEach(([text, type]) => addMessage(text, type, false));
    }

    async function loadMessages() {
      const response = await fetch('/chats/' + activeChat + '/messages');
      const saved = response.ok ? await response.json() : [];
      chats[activeChat].messages = saved.map(item => [item.content, item.role === 'human' ? 'user' : 'assistant']);
      renderMessages();
    }

    function addMessage(text, type, save = true) {
      const message = document.createElement('div');
      message.className = 'message ' + type;
      message.textContent = text;
      chat.appendChild(message);
      if (save) chats[activeChat].messages.push([text, type]);
      chat.scrollTop = chat.scrollHeight;
      return message;
    }

    async function askQuestion() {
      const question = questionBox.value.trim();
      if (!question) { statusText.textContent = 'Please enter a question.'; return; }
      addMessage(question, 'user');
      questionBox.value = '';
      askButton.disabled = true;
      statusText.textContent = 'Working...';
      try {
        const response = await fetch('/ask', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question: question, chat_id: activeChat})
        });
        const data = await response.json();
        if (data.approval_required) {
          const approvalMessage = addMessage(data.approval_message, 'assistant');
          const approve = document.createElement('button'); approve.textContent = 'Approve';
          const deny = document.createElement('button'); deny.textContent = 'Deny';
          approve.onclick = () => submitApproval(data.approval_id, true, approvalMessage, approve, deny);
          deny.onclick = () => submitApproval(data.approval_id, false, approvalMessage, approve, deny);
          approvalMessage.appendChild(document.createElement('br'));
          approvalMessage.appendChild(approve); approvalMessage.appendChild(deny);
        } else {
          addMessage(response.ok ? data.answer : data.detail, 'assistant');
        }
      } catch (error) {
        addMessage('Could not connect to the KubeOps API.', 'assistant');
      } finally {
        askButton.disabled = false;
        statusText.textContent = '';
        questionBox.focus();
      }
    }

    async function submitApproval(id, approved, message, approve, deny) {
      approve.disabled = true; deny.disabled = true;
      const response = await fetch('/approvals/' + id + '?approved=' + approved, {method: 'POST'});
      const data = await response.json();
      if (data.approval_required) {
        message.textContent = data.approval_message;
        const nextApprove = document.createElement('button'); nextApprove.textContent = 'Approve';
        const nextDeny = document.createElement('button'); nextDeny.textContent = 'Deny';
        nextApprove.onclick = () => submitApproval(id, true, message, nextApprove, nextDeny);
        nextDeny.onclick = () => submitApproval(id, false, message, nextApprove, nextDeny);
        message.appendChild(document.createElement('br'));
        message.appendChild(nextApprove); message.appendChild(nextDeny);
      } else {
        message.textContent = approved ? 'Approved. Investigation resumed.' : 'Denied.';
        if (response.ok && data.answer) addMessage(data.answer, 'assistant');
        if (!response.ok) addMessage(data.detail || 'Approval failed.', 'assistant');
      }
    }

    async function switchChat(chatId) {
      if (!chatId || chatId === activeChat) return;
      activeChat = chatId;
      renderChatList();
      await loadMessages();
    }
    async function deleteChat() {
      if (!activeChat) return;
      await fetch('/chats/' + activeChat, {method: 'DELETE'});
      delete chats[activeChat];
      activeChat = Object.keys(chats)[0];
      if (!activeChat) await newChat(); else { renderChatList(); await loadMessages(); }
    }

    async function renameChat() {
      if (!activeChat) return;
      const currentName = chats[activeChat].name;
      const name = prompt('Enter a name for this chat:', currentName);
      if (!name || !name.trim()) return;
      const response = await fetch('/chats/' + activeChat, {
        method: 'PATCH', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name.trim()})
      });
      if (response.ok) { chats[activeChat].name = name.trim(); renderChatList(); }
    }

    loadChats();

    questionBox.addEventListener('keydown', function(event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        askQuestion();
      }
    });
  </script>
</body>
</html>
"""


@app.get("/health")
def health_check():
    """Confirm that the KubeOps API is running."""
    return {"status": "ok"}


@app.get("/chats")
def get_chats():
    """Return saved chats for the current local user."""
    return list_chats(DEFAULT_USER_ID)


@app.post("/chats")
def add_chat(request: ChatRequest):
    """Create and persist a new chat."""
    chat_id = uuid4().hex
    create_chat(chat_id, request.user_id)
    return {"chat_id": chat_id, "name": "New chat"}


@app.get("/chats/{chat_id}/messages")
def get_chat_messages(chat_id: str):
    """Return saved messages for one chat."""
    return get_messages(chat_id, DEFAULT_USER_ID)


@app.post("/ask", response_model=AnswerResponse)
def ask_question(request: QuestionRequest):
    """Send a browser question through the Supervisor Agent."""
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        # Temporary direct mode: MCP, RAG, Supervisor, and agent tools are disabled.
        add_message(request.chat_id, request.user_id, "human", question)
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Set OPENAI_API_KEY in the kubeops .env file.")
        model = ChatOpenAI(
            model="gpt-5.6-luna", api_key=api_key, use_responses_api=True
        )
        # Main mode: Supervisor -> LangGraph agent -> MCP Kubernetes tools.
        try:
            answer = run_supervisor_agent(
                model, question, request.chat_id, interactive_approval=False
            )
        except HumanApprovalRequired as approval:
            approval_id = uuid4().hex
            pending_approvals[approval_id] = approval
            return AnswerResponse(
                answer="",
                approval_required=True,
                approval_id=approval_id,
                approval_message=approval.message,
            )
        add_message(request.chat_id, request.user_id, "ai", answer)
        touch_chat(request.chat_id, request.user_id)
    except Exception as error:
        logger.exception("KubeOps request failed.")
        raise HTTPException(status_code=500, detail=str(error)) from error

    return AnswerResponse(answer=answer)


@app.post("/approvals/{approval_id}", response_model=AnswerResponse)
def answer_approval(approval_id: str, approved: bool):
    """Resume a paused troubleshooting workflow after a UI decision."""
    approval = pending_approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request was not found.")
    state = approval.graph.invoke(
        Command(resume="yes" if approved else "no"), approval.config
    )
    while "__interrupt__" in state:
        request = state["__interrupt__"][0].value
        pending_approvals[approval_id] = HumanApprovalRequired(
            approval.graph, state, approval.config, request["message"]
        )
        return AnswerResponse(
            answer="",
            approval_required=True,
            approval_id=approval_id,
            approval_message=request["message"],
        )
    pending_approvals.pop(approval_id, None)
    return AnswerResponse(answer=state["messages"][-1].text)


@app.delete("/chats/{chat_id}")
def remove_chat(chat_id: str):
    """Delete a chat's saved LangGraph history."""
    delete_chat_history(chat_id)
    delete_chat(chat_id, DEFAULT_USER_ID)
    return {"status": "deleted"}


@app.patch("/chats/{chat_id}")
def rename_chat_endpoint(chat_id: str, request: RenameChatRequest):
    """Rename a saved chat."""
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Chat name cannot be empty.")
    rename_chat(chat_id, DEFAULT_USER_ID, name)
    return {"status": "renamed", "name": name}


if __name__ == "__main__":
    import uvicorn

    # Reload the server automatically whenever Python files change.
    # Keep reload enabled for local development only.
    uvicorn.run("app:app", host="127.0.0.1", port=8200, reload=True)
