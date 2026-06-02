const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send');
const emptyEl = document.getElementById('empty');

const CONV_KEY = 'huginn_conversation_id';
let conversationId = localStorage.getItem(CONV_KEY) || null;
let isStreaming = false;

function clearEmpty() {
  if (emptyEl && emptyEl.parentNode) emptyEl.remove();
}

function setConversationId(cid) {
  conversationId = cid;
  localStorage.setItem(CONV_KEY, cid);
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function autoResize() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
}

function addUserMessage(text) {
  clearEmpty();
  const wrap = document.createElement('div');
  wrap.className = 'msg';
  wrap.innerHTML = `
    <div class="msg-role user">You</div>
    <div class="msg-content"></div>
  `;
  wrap.querySelector('.msg-content').textContent = text;
  messagesEl.appendChild(wrap);
  scrollToBottom();
}

function startAgentMessage() {
  const wrap = document.createElement('div');
  wrap.className = 'msg';
  wrap.innerHTML = `<div class="msg-role agent">Huginn</div>`;
  messagesEl.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function addTextBlock(parent, text, options = {}) {
  const block = document.createElement('div');
  block.className = 'msg-content';
  if (options.error) block.classList.add('msg-error');
  block._rawText = text;
  block.innerHTML = marked.parse(text);
  parent.appendChild(block);
  scrollToBottom();
  return block;
}

function addToolBlock(parent, name, input) {
  const block = document.createElement('div');
  block.className = 'tool-block';
  block.innerHTML = `
    <div class="tool-header">
      <span class="tool-icon">▸</span>
      <span class="tool-name">${name}</span>
      <span class="tool-status">running...</span>
    </div>
    <pre class="tool-input"></pre>
    <pre class="tool-output" style="display:none;"></pre>
  `;
  block.querySelector('.tool-input').textContent =
    typeof input === 'string' ? input : JSON.stringify(input, null, 2);
  parent.appendChild(block);
  scrollToBottom();
  return block;
}

function completeToolBlock(block, result) {
  block.querySelector('.tool-status').textContent = 'done';
  const output = block.querySelector('.tool-output');
  output.style.display = 'block';
  try {
    const parsed = JSON.parse(result);
    output.textContent = JSON.stringify(parsed, null, 2);
  } catch {
    output.textContent = result;
  }
  scrollToBottom();
}

async function send() {
  if (isStreaming) return;
  const message = inputEl.value.trim();
  if (!message) return;

  inputEl.value = '';
  autoResize();
  addUserMessage(message);

  const agentWrap = startAgentMessage();
  let currentTextBlock = null;
  const toolBlocks = {};

  isStreaming = true;
  sendBtn.disabled = true;

  try {
    const response = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, conversation_id: conversationId })
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const event = JSON.parse(line.slice(6));

        if (event.type === 'text') {
          if (!currentTextBlock) {
            currentTextBlock = addTextBlock(agentWrap, '');
          }
          currentTextBlock._rawText = (currentTextBlock._rawText || '') + event.content;
          currentTextBlock.innerHTML = marked.parse(currentTextBlock._rawText);
          scrollToBottom();
        } else if (event.type === 'tool_use') {
          currentTextBlock = null;
          const key = `${event.name}-${Date.now()}-${Math.random()}`;
          toolBlocks[key] = addToolBlock(agentWrap, event.name, event.input);
          toolBlocks._latest = key;
        } else if (event.type === 'tool_result') {
          const key = toolBlocks._latest;
          if (key && toolBlocks[key]) {
            completeToolBlock(toolBlocks[key], event.result);
          }
        } else if (event.type === 'error') {
          currentTextBlock = null;
          addTextBlock(agentWrap, `Error: ${event.message}`, { error: true });
        } else if (event.type === 'done') {
          if (event.conversation_id) setConversationId(event.conversation_id);
        }
      }
    }
  } catch (err) {
    addTextBlock(agentWrap, `Error: ${err.message}`, { error: true });
  } finally {
    isStreaming = false;
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

async function reset() {
  if (isStreaming) return;
  try {
    const response = await fetch('/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: conversationId })
    });
    if (response.ok) {
      const data = await response.json();
      if (data.conversation_id) setConversationId(data.conversation_id);
    }
  } catch (e) {
    console.error('Reset failed:', e);
  }
  messagesEl.innerHTML = `
    <div class="empty" id="empty">
      <div class="empty-title">Huginn is ready</div>
      <div class="empty-sub">Send a message to begin</div>
    </div>
  `;
  inputEl.focus();
}

async function loadHistory() {
  if (!conversationId) return;
  try {
    const response = await fetch(`/history?conversation_id=${encodeURIComponent(conversationId)}`);
    if (!response.ok) return;
    const data = await response.json();
    const messages = data.messages || [];
    if (messages.length === 0) return;

    clearEmpty();
    const toolBlocksById = {};

    for (const msg of messages) {
      if (msg.role === 'user') {
        if (typeof msg.content === 'string') {
          addUserMessage(msg.content);
        } else if (Array.isArray(msg.content)) {
          for (const block of msg.content) {
            if (block.type === 'tool_result' && toolBlocksById[block.tool_use_id]) {
              completeToolBlock(toolBlocksById[block.tool_use_id], block.content);
            }
          }
        }
      } else if (msg.role === 'assistant' && Array.isArray(msg.content)) {
        const agentWrap = startAgentMessage();
        for (const block of msg.content) {
          if (block.type === 'text' && block.text) {
            addTextBlock(agentWrap, block.text);
          } else if (block.type === 'tool_use') {
            toolBlocksById[block.id] = addToolBlock(agentWrap, block.name, block.input);
          }
        }
      }
    }
  } catch (err) {
    console.error('Failed to load history:', err);
  }
}

inputEl.addEventListener('input', autoResize);
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
inputEl.focus();

loadHistory();
