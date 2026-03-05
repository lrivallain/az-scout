/* ===================================================================
   Azure Scout – AI Chat Panel  (SSE streaming, tool-call display)
   Requires: app.js (globals: escapeHtml, regions, selectRegion,
             onTenantChange, plannerSubscriptionId)
   =================================================================== */

// ---------------------------------------------------------------------------
// Chat state
// ---------------------------------------------------------------------------
let _chatMessages = [];   // [{role, content}] – conversation history
let _chatStreaming = false;
let _chatInputHistory = [];  // user-sent messages (strings)
let _chatHistoryIdx = -1;    // -1 = composing new message
let _chatDraft = "";         // saved draft while navigating history
let _chatPersist = false;    // whether to save chat history to localStorage
let _chatPinned = false;     // whether chat is pinned to right side
let _chatMode = "discussion"; // "discussion" | "planner"

const _CHAT_STORAGE_KEY = "azm-chat-history";
const _CHAT_PERSIST_KEY = "azm-chat-persist";
const _CHAT_INPUT_HIST_KEY = "azm-chat-input-history";
const _CHAT_MODE_KEY = "azm-chat-mode";

// Per-mode conversation state: { discussion: {messages, inputHistory}, planner: {messages, inputHistory} }
const _chatModeState = {
    discussion: { messages: [], inputHistory: [] },
    planner:   { messages: [], inputHistory: [] },
};

/**
 * Register plugin-contributed chat modes at startup.
 * Called from the inline <script> block in the template after app.js loads.
 * @param {Array} plugins - Array of plugin metadata objects from Jinja2 context.
 */
function registerPluginChatModes(plugins) {
    for (const p of plugins) {
        for (const mode of (p.chat_modes || [])) {
            if (!_chatModeState[mode.id]) {
                _chatModeState[mode.id] = { messages: [], inputHistory: [] };
            }
            if (!_CHAT_WELCOME[mode.id]) {
                _CHAT_WELCOME[mode.id] = mode.welcome_message || `Welcome to **${mode.label}** mode.`;
            }
        }
    }
}


function toggleChatPanel() {
    const panel = document.getElementById("chat-panel");
    if (!panel) return;
    panel.classList.toggle("d-none");
    if (!panel.classList.contains("d-none")) {
        document.getElementById("chat-input")?.focus();
    }
    // If pinned and closing, unpin
    if (panel.classList.contains("d-none") && _chatPinned) {
        _setChatPinned(false);
    }
}

// ---------------------------------------------------------------------------
// Chat mode switching  (Discussion ↔ Planner)
// ---------------------------------------------------------------------------

const _CHAT_WELCOME = {
    discussion: `👋 Hi! I'm your Azure Scout assistant. Ask me about Azure regions, SKU availability, pricing, zone mappings, and more. I can query live Azure data for you.
- [[Show me available VM sizes in this region]]
- [[Compare zone mappings across my subscriptions]]
- [[What are the cheapest spot VMs with 4 vCPUs?]]
- [[List all regions with availability zones]]`,
    planner: `🗺️ Welcome to the **VM Deployment Planner**! I can help you with one of these:
- [[Find the best region for my VM workload]]
- [[Find the right VM size in a specific region]]
- [[Pick the best availability zone for a VM SKU]]`,
};

function switchChatMode(mode) {
    if (mode === _chatMode || _chatStreaming) return;

    // Save current mode's conversation state
    _chatModeState[_chatMode].messages = [..._chatMessages];
    _chatModeState[_chatMode].inputHistory = [..._chatInputHistory];

    // Ensure target mode state exists (plugin modes may be registered dynamically)
    if (!_chatModeState[mode]) {
        _chatModeState[mode] = { messages: [], inputHistory: [] };
    }

    // Switch
    _chatMode = mode;
    try { localStorage.setItem(_CHAT_MODE_KEY, mode); } catch {}

    // Update toggle UI
    document.querySelectorAll("#chat-mode-toggle button").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.mode === mode);
    });

    // Restore target mode's conversation (or start fresh)
    _chatMessages = [...(_chatModeState[mode].messages || [])];
    _chatInputHistory = [...(_chatModeState[mode].inputHistory || [])];
    _chatHistoryIdx = -1;
    _chatDraft = "";

    // Rebuild chat UI
    const container = document.getElementById("chat-messages");
    if (!container) return;
    container.innerHTML = "";

    // Show welcome message
    const welcome = document.createElement("div");
    welcome.className = "chat-message assistant";
    const welcomeText = _CHAT_WELCOME[mode] || `Welcome to **${mode}** mode.`;
    welcome.innerHTML = `<div class="chat-bubble">${_renderMarkdown(welcomeText)}</div>`;
    container.appendChild(welcome);

    // Replay stored messages
    for (const msg of _chatMessages) {
        _appendChatBubble(msg.role, msg.content);
    }

    // Update input placeholder
    const input = document.getElementById("chat-input");
    if (input) {
        input.placeholder = mode === "planner"
            ? "Describe your deployment needs…"
            : "Ask about Azure SKUs, zones, pricing…";
        input.focus();
    }

    _saveChatHistory();
}

function toggleChatPin() {
    _setChatPinned(!_chatPinned);
}

function _setChatPinned(pinned) {
    _chatPinned = pinned;
    document.body.classList.toggle("chat-pinned", _chatPinned);
    const btn = document.getElementById("chat-pin-btn");
    if (btn) {
        btn.classList.toggle("active", _chatPinned);
        btn.title = _chatPinned ? "Unpin chat" : "Pin chat to side";
        btn.dataset.tooltip = _chatPinned ? "Unpin" : "Pin";
        const icon = btn.querySelector("i");
        if (icon) {
            icon.className = _chatPinned ? "bi bi-pin-fill" : "bi bi-pin-angle";
        }
    }
    // Adjust textarea rows for pinned mode
    const ta = document.getElementById("chat-input");
    if (ta) ta.rows = _chatPinned ? 6 : 1;
    // Show panel when pinning
    if (_chatPinned) {
        const panel = document.getElementById("chat-panel");
        if (panel) {
            panel.classList.remove("d-none");
            panel.style.animation = "none";
        }
        // Sync pinned width to content margin
        _syncPinnedWidth();
    }
}

function _syncPinnedWidth() {
    if (!_chatPinned) return;
    const panel = document.getElementById("chat-panel");
    if (!panel) return;
    const w = panel.getBoundingClientRect().width;
    document.documentElement.style.setProperty("--chat-pinned-width", w + "px");
}


function toggleChatPersist() {
    _chatPersist = !_chatPersist;
    const btn = document.getElementById("chat-persist-btn");
    if (btn) btn.classList.toggle("active", _chatPersist);
    try {
        if (_chatPersist) {
            localStorage.setItem(_CHAT_PERSIST_KEY, "1");
            _saveChatHistory();
        } else {
            localStorage.removeItem(_CHAT_PERSIST_KEY);
            localStorage.removeItem(_CHAT_STORAGE_KEY);
            localStorage.removeItem(_CHAT_INPUT_HIST_KEY);
        }
    } catch {}
}

function _saveChatHistory() {
    if (!_chatPersist) return;
    try {
        // Save per-mode state
        _chatModeState[_chatMode].messages = [..._chatMessages];
        _chatModeState[_chatMode].inputHistory = [..._chatInputHistory];
        localStorage.setItem(_CHAT_STORAGE_KEY, JSON.stringify(_chatModeState));
        localStorage.setItem(_CHAT_INPUT_HIST_KEY, JSON.stringify(_chatInputHistory));
        localStorage.setItem(_CHAT_MODE_KEY, _chatMode);
    } catch {}
}

function _restoreChatHistory() {
    try {
        _chatPersist = localStorage.getItem(_CHAT_PERSIST_KEY) === "1";
        const btn = document.getElementById("chat-persist-btn");
        if (btn) btn.classList.toggle("active", _chatPersist);

        // Restore saved mode
        const savedMode = localStorage.getItem(_CHAT_MODE_KEY);
        if (savedMode && (savedMode === "discussion" || savedMode === "planner")) {
            _chatMode = savedMode;
            document.querySelectorAll("#chat-mode-toggle button").forEach(b => {
                b.classList.toggle("active", b.dataset.mode === _chatMode);
            });
        }

        let hasHistory = false;

        if (_chatPersist) {
            const saved = localStorage.getItem(_CHAT_STORAGE_KEY);
            if (saved) {
                const state = JSON.parse(saved);
                // Support both old format (array) and new format (object with per-mode state)
                if (Array.isArray(state)) {
                    // Legacy: migrate old format into discussion mode
                    _chatModeState.discussion.messages = state;
                } else if (state && typeof state === "object") {
                    // Support old "assistant" key for backward compat
                    const disc = state.discussion || state.assistant;
                    if (disc?.messages) _chatModeState.discussion = disc;
                    if (state.planner?.messages) _chatModeState.planner = state.planner;
                }

                // Load current mode's state
                _chatMessages = [...(_chatModeState[_chatMode].messages || [])];
                _chatInputHistory = [...(_chatModeState[_chatMode].inputHistory || [])];
                hasHistory = _chatMessages.length > 0;
            }
        }

        // Build the chat UI in one pass (no flash)
        const container = document.getElementById("chat-messages");
        if (!container) return;

        const welcome = _renderMarkdown(_CHAT_WELCOME[_chatMode]);
        container.innerHTML = `<div class="chat-message assistant"><div class="chat-bubble">${welcome}</div></div>`;

        if (hasHistory) {
            // Add restored-session notice then replay messages
            const notice = document.createElement("div");
            notice.className = "chat-message assistant";
            notice.innerHTML = `<div class="chat-bubble"><em>Restored ${_chatMessages.filter(m => m.role === "user").length} message(s) from previous session.</em></div>`;
            container.appendChild(notice);
            for (const msg of _chatMessages) {
                _appendChatBubble(msg.role, msg.content);
            }
        }

        // Update placeholder
        const input = document.getElementById("chat-input");
        if (input) {
            input.placeholder = _chatMode === "planner"
                ? "Describe your deployment needs…"
                : "Ask about Azure SKUs, zones, pricing…";
        }
    } catch {}
}

function clearChat() {
    _chatMessages = [];
    _chatInputHistory = [];
    _chatHistoryIdx = -1;
    _chatDraft = "";
    _chatModeState[_chatMode].messages = [];
    _chatModeState[_chatMode].inputHistory = [];
    _saveChatHistory();
    const container = document.getElementById("chat-messages");
    if (!container) return;
    const welcome = _renderMarkdown(_CHAT_WELCOME[_chatMode]);
    container.innerHTML = `<div class="chat-message assistant"><div class="chat-bubble">${welcome}</div></div>`;
}

function handleChatKeydown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
    } else if (e.key === "ArrowUp" && !e.shiftKey) {
        _navigateChatHistory(-1, e);
    } else if (e.key === "ArrowDown" && !e.shiftKey) {
        _navigateChatHistory(1, e);
    }
}

async function sendChatMessage() {
    if (_chatStreaming) return;
    const input = document.getElementById("chat-input");
    const text = input?.value?.trim();
    if (!text) return;
    input.value = "";
    _autoResizeChatInput();

    // Track input history
    _chatInputHistory.push(text);
    _chatHistoryIdx = -1;
    _chatDraft = "";

    // Add user message to UI and history
    _appendChatBubble("user", text);
    _chatMessages.push({ role: "user", content: text });
    _saveChatHistory();

    // Create assistant bubble with thinking indicator
    const assistantBubble = _appendChatBubble("assistant", "");
    assistantBubble.innerHTML = '<span class="chat-thinking"><span class="dot"></span><span class="dot"></span><span class="dot"></span></span>';
    _scrollChatBottom();
    const sendBtn = document.getElementById("chat-send-btn");
    _chatStreaming = true;
    if (sendBtn) sendBtn.disabled = true;

    try {
        const tenantId = document.getElementById("tenant-select")?.value || "";
        const regionId = document.getElementById("region-select")?.value || "";
        const resp = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                messages: _chatMessages,
                mode: _chatMode,
                tenant_id: tenantId || undefined,
                region: regionId || undefined,
                subscription_id: plannerSubscriptionId || undefined,
            }),
        });

        if (!resp.ok) {
            const err = await resp.text();
            assistantBubble.innerHTML = `<span class="text-danger">Error: ${escapeHtml(err)}</span>`
                + '<br><button class="chat-choice-chip mt-2" onclick="_retryChatMessage(this)">Retry</button>';
            _chatStreaming = false;
            if (sendBtn) sendBtn.disabled = false;
            return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let fullContent = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            // Process complete SSE lines
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                let payload;
                try { payload = JSON.parse(line.slice(6)); } catch { continue; }

                if (payload.type === "delta") {
                    fullContent += payload.content;
                    assistantBubble.innerHTML = _renderMarkdown(fullContent);
                    assistantBubble.closest(".chat-message")?.classList.remove("is-thinking");
                    _scrollChatBottom();
                } else if (payload.type === "tool_call") {
                    _appendToolStatus(assistantBubble.closest(".chat-message"), payload.name, "calling", payload.arguments);
                    _scrollChatBottom();
                } else if (payload.type === "tool_result") {
                    _updateToolStatus(assistantBubble.closest(".chat-message"), payload.name, "done", payload.arguments, payload.content);
                    _scrollChatBottom();
                } else if (payload.type === "ui_action") {
                    _handleChatUiAction(payload);
                } else if (payload.type === "status") {
                    // Transient status message (e.g. rate-limit retry)
                    assistantBubble.innerHTML = `<span class="text-muted"><em>${escapeHtml(payload.content)}</em></span>`;
                    assistantBubble.closest(".chat-message")?.classList.remove("is-thinking");
                    _scrollChatBottom();
                } else if (payload.type === "error") {
                    fullContent = ""; // Don't store error as assistant message
                    assistantBubble.innerHTML = `<span class="text-danger"><strong>Error:</strong> ${escapeHtml(payload.content)}</span>`
                        + '<br><button class="chat-choice-chip mt-2" onclick="_retryChatMessage(this)">Retry</button>';
                } else if (payload.type === "done") {
                    // Stream finished
                }
            }
        }

        if (fullContent) {
            _chatMessages.push({ role: "assistant", content: fullContent });
            _saveChatHistory();
        }
    } catch (err) {
        assistantBubble.innerHTML = `<span class="text-danger">Connection error: ${escapeHtml(err.message)}</span>`
            + '<br><button class="chat-choice-chip mt-2" onclick="_retryChatMessage(this)">Retry</button>';
    } finally {
        _chatStreaming = false;
        if (sendBtn) sendBtn.disabled = false;
        _scrollChatBottom();
    }
}

function _appendChatBubble(role, content) {
    const container = document.getElementById("chat-messages");
    const msg = document.createElement("div");
    msg.className = `chat-message ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    if (content) bubble.innerHTML = role === "user" ? escapeHtml(content) : _renderMarkdown(content);
    msg.appendChild(bubble);
    container.appendChild(msg);
    _scrollChatBottom();
    return bubble;
}

/** Retry the last failed message — remove the error bubble and resend. */
function _retryChatMessage(btn) {
    if (_chatStreaming) return;
    // Remove the error assistant bubble
    const msgDiv = btn.closest(".chat-message");
    if (msgDiv) msgDiv.remove();
    // Pop the last assistant message if it was stored (shouldn't be on error, but be safe)
    if (_chatMessages.length && _chatMessages[_chatMessages.length - 1].role === "assistant") {
        _chatMessages.pop();
    }
    // Re-send: the last user message is still in _chatMessages
    if (!_chatMessages.length || _chatMessages[_chatMessages.length - 1].role !== "user") return;
    const lastUserMsg = _chatMessages[_chatMessages.length - 1].content;
    // Remove it so sendChatMessage re-adds it
    _chatMessages.pop();
    const input = document.getElementById("chat-input");
    if (input) input.value = lastUserMsg;
    sendChatMessage();
}

/** Handle click on a [[choice]] chip — send the choice text as a user message. */
function _onChatChoiceClick(btn) {
    if (_chatStreaming) return;
    const text = btn.textContent.trim();
    if (!text) return;
    // Dim all choice chips in the same bubble to show selection was made
    const bubble = btn.closest(".chat-bubble");
    if (bubble) {
        bubble.querySelectorAll(".chat-choice-chip").forEach(c => { c.classList.add("used"); });
    }
    // Populate input and send
    const input = document.getElementById("chat-input");
    if (input) input.value = text;
    sendChatMessage();
}

/** Navigate chat input history with Up/Down arrows (like a terminal). */
function _navigateChatHistory(direction, e) {
    if (!_chatInputHistory.length) return;
    const input = document.getElementById("chat-input");
    if (!input) return;

    // Only navigate when cursor is at the very start (Up) or very end (Down)
    if (direction === -1 && input.selectionStart !== 0) return;
    if (direction === 1 && input.selectionStart !== input.value.length) return;

    e.preventDefault();

    if (direction === -1) {
        // Going backwards (older)
        if (_chatHistoryIdx === -1) {
            // Entering history — save current draft
            _chatDraft = input.value;
            _chatHistoryIdx = _chatInputHistory.length - 1;
        } else if (_chatHistoryIdx > 0) {
            _chatHistoryIdx--;
        } else {
            return; // already at oldest
        }
        input.value = _chatInputHistory[_chatHistoryIdx];
    } else {
        // Going forwards (newer)
        if (_chatHistoryIdx === -1) return; // not in history
        if (_chatHistoryIdx >= _chatInputHistory.length - 1) {
            // Back to draft
            _chatHistoryIdx = -1;
            input.value = _chatDraft;
        } else {
            _chatHistoryIdx++;
            input.value = _chatInputHistory[_chatHistoryIdx];
        }
    }
    _autoResizeChatInput();
    // Move cursor to end
    input.setSelectionRange(input.value.length, input.value.length);
}

function _appendToolStatus(msgDiv, toolName, status, argsJson) {
    let toolsDiv = msgDiv.querySelector(".chat-tool-calls");
    if (!toolsDiv) {
        toolsDiv = document.createElement("div");
        toolsDiv.className = "chat-tool-calls";
        // Insert before the bubble so innerHTML changes never affect it
        const bubble = msgDiv.querySelector(".chat-bubble");
        msgDiv.insertBefore(toolsDiv, bubble);
    }
    const badge = document.createElement("span");
    badge.className = "chat-tool-badge calling";
    badge.dataset.tool = toolName;
    const friendlyName = toolName.replace(/_/g, " ");
    badge.innerHTML = `<i class="bi bi-gear-fill spin"></i> ${escapeHtml(friendlyName)}`;
    // Store arguments for later inspection
    if (argsJson) {
        badge.dataset.toolArgs = typeof argsJson === "string" ? argsJson : JSON.stringify(argsJson);
    }
    toolsDiv.appendChild(badge);
}

function _updateToolStatus(msgDiv, toolName, status, argsJson, contentStr) {
    const badge = msgDiv.querySelector(`.chat-tool-badge[data-tool="${toolName}"]`);
    if (!badge) return;
    badge.className = `chat-tool-badge ${status}`;
    const friendlyName = toolName.replace(/_/g, " ");
    badge.innerHTML = `<i class="bi bi-check-circle-fill"></i> ${escapeHtml(friendlyName)}`;
    // Store final arguments (may include auto-injected params) and result content
    if (argsJson) {
        badge.dataset.toolArgs = typeof argsJson === "string" ? argsJson : JSON.stringify(argsJson);
    }
    if (contentStr) {
        badge.dataset.toolContent = contentStr;
    }
    // Make completed badges clickable for inspection
    badge.style.cursor = "pointer";
    badge.title = "Click to inspect tool input/output";
    badge.addEventListener("click", () => _showToolDetailModal(toolName, badge.dataset.toolArgs, badge.dataset.toolContent));
}

function _scrollChatBottom() {
    const container = document.getElementById("chat-messages");
    if (container) container.scrollTop = container.scrollHeight;
}

/** Show a modal with tool input (arguments) and output (result) details. */
function _showToolDetailModal(toolName, argsJson, contentStr) {
    const modalId = "toolDetailModal";
    let modal = document.getElementById(modalId);
    if (!modal) {
        modal = document.createElement("div");
        modal.className = "modal fade";
        modal.id = modalId;
        modal.tabIndex = -1;
        modal.setAttribute("aria-labelledby", "toolDetailModalLabel");
        modal.setAttribute("aria-hidden", "true");
        modal.innerHTML = `
            <div class="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="toolDetailModalLabel"></h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <h6 class="text-muted mb-1"><i class="bi bi-box-arrow-in-right"></i> Input</h6>
                        <div class="tool-detail-wrap">
                            <button class="btn btn-sm tool-copy-btn" data-target="toolDetailInput" title="Copy input">
                                <i class="bi bi-clipboard"></i>
                            </button>
                            <pre class="tool-detail-pre" id="toolDetailInput"></pre>
                        </div>
                        <h6 class="text-muted mb-1 mt-3"><i class="bi bi-box-arrow-right"></i> Output</h6>
                        <div class="tool-detail-wrap">
                            <button class="btn btn-sm tool-copy-btn" data-target="toolDetailOutput" title="Copy output">
                                <i class="bi bi-clipboard"></i>
                            </button>
                            <pre class="tool-detail-pre" id="toolDetailOutput"></pre>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Close</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(modal);
        // Wire up copy buttons
        modal.querySelectorAll(".tool-copy-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const pre = modal.querySelector(`#${btn.dataset.target}`);
                if (!pre) return;
                navigator.clipboard.writeText(pre.textContent).then(() => {
                    btn.innerHTML = '<i class="bi bi-check-lg"></i>';
                    setTimeout(() => { btn.innerHTML = '<i class="bi bi-clipboard"></i>'; }, 1500);
                });
            });
        });
    }
    // Populate content
    const friendlyName = toolName.replace(/_/g, " ");
    modal.querySelector("#toolDetailModalLabel").textContent = `MCP tool \u2013 ${friendlyName}`;
    const inputEl = modal.querySelector("#toolDetailInput");
    const outputEl = modal.querySelector("#toolDetailOutput");
    // Format input arguments
    try {
        const parsed = argsJson ? JSON.parse(argsJson) : {};
        inputEl.innerHTML = _highlightJson(JSON.stringify(parsed, null, 2));
    } catch {
        inputEl.textContent = argsJson || "(none)";
    }
    // Format output content
    try {
        const parsed = contentStr ? JSON.parse(contentStr) : null;
        outputEl.innerHTML = parsed ? _highlightJson(JSON.stringify(parsed, null, 2)) : escapeHtml(contentStr || "(no output)");
    } catch {
        outputEl.textContent = contentStr || "(no output)";
    }
    // Show the modal
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();
}

/** Highlight a JSON string using highlight.js (falls back to escaped plain text). */
function _highlightJson(jsonStr) {
    if (typeof hljs !== "undefined") {
        return hljs.highlight(jsonStr, { language: "json" }).value;
    }
    return escapeHtml(jsonStr);
}

/** Handle UI actions emitted by the chat backend (e.g. tenant/region switching). */
function _handleChatUiAction(payload) {
    if (payload.action === "switch_tenant") {
        const select = document.getElementById("tenant-select");
        if (!select) return;
        const targetId = payload.tenant_id;
        const option = Array.from(select.options).find(o => o.value === targetId);
        if (option && !option.disabled) {
            select.value = targetId;
            onTenantChange();
        }
    } else if (payload.action === "switch_region") {
        const regionName = payload.region;
        // Check the region exists in the loaded regions list
        const r = regions.find(r => r.name === regionName);
        if (r) {
            selectRegion(regionName);
        }
    }
}

/** Auto-resize textarea to fit content (up to 4 lines). */
function _autoResizeChatInput() {
    const el = document.getElementById("chat-input");
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 96) + "px";
}

// Auto-resize on input
document.addEventListener("input", (e) => {
    if (e.target.id === "chat-input") _autoResizeChatInput();
});

/** Minimal Markdown → HTML renderer for chat bubbles. */
function _renderMarkdown(md) {
    let html = escapeHtml(md);

    // Code blocks: ```lang\n...\n```
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) =>
        `<pre><code>${code}</code></pre>`
    );
    // Inline code
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Clickable choice chips: [[choice text]]
    html = html.replace(/\[\[(.+?)\]\]/g,
        '<button class="chat-choice-chip" onclick="_onChatChoiceClick(this)">$1</button>'
    );
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Italic
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    // Headers (## and ###)
    html = html.replace(/^### (.+)$/gm, '<h6 class="mt-2 mb-1">$1</h6>');
    html = html.replace(/^## (.+)$/gm, '<h5 class="mt-2 mb-1">$1</h5>');
    // Horizontal rule
    html = html.replace(/^---$/gm, "<hr>");

    // Tables: detect lines with |
    html = _renderMarkdownTables(html);

    // Unordered lists
    html = html.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
    // Collapse nested <ul>
    html = html.replace(/<\/ul>\s*<ul>/g, "");
    // Convert lists whose items are ALL choice chips into compact chip groups
    html = html.replace(/<ul>([\s\S]*?)<\/ul>/g, (_m, inner) => {
        const items = inner.match(/<li>([\s\S]*?)<\/li>/g);
        if (!items) return `<ul>${inner}</ul>`;
        const allChips = items.every(li => {
            const content = li.replace(/<\/?li>/g, "").trim();
            return _containsOnlyChoiceChips(content);
        });
        if (allChips) {
            const chips = items.map(li => li.replace(/<\/?li>/g, "").trim()).join("");
            if (items.length > 10) {
                return `<div class="chat-suggestions">${chips}</div>`;
            }
            return `<ul class="chat-choices-list">${inner}</ul>`;
        }
        return `<ul>${inner}</ul>`;
    });

    // Line breaks (but not inside pre/code)
    html = html.replace(/\n/g, "<br>");
    // Clean up excessive <br> around block elements
    html = html.replace(/<br>\s*(<h[56]|<pre|<ul|<hr|<table)/g, "$1");
    html = html.replace(/(<\/h[56]>|<\/pre>|<\/ul>|<hr>|<\/table>)\s*<br>/g, "$1");

    return html;
}

function _containsOnlyChoiceChips(content) {
    let remaining = content.trim();
    if (!remaining) return false;

    const chipPrefix = '<button class="chat-choice-chip"';
    const closeTag = "</button>";

    while (remaining.length > 0) {
        if (!remaining.startsWith(chipPrefix)) {
            return false;
        }

        const openTagEnd = remaining.indexOf(">", chipPrefix.length);
        if (openTagEnd === -1) {
            return false;
        }

        const closeTagStart = remaining.indexOf(closeTag, openTagEnd + 1);
        if (closeTagStart === -1) {
            return false;
        }

        remaining = remaining.slice(closeTagStart + closeTag.length).trimStart();
    }

    return true;
}

function _renderMarkdownTables(html) {
    const lines = html.split("\n");
    let result = [];
    let inTable = false;
    let tableRows = [];

    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
            // Skip separator rows (|---|---|, |:---:|, | --- | --- |, etc.)
            const inner = trimmed.slice(1, -1);
            if (inner.split("|").every(c => /^[\s\-:]+$/.test(c))) {
                if (!inTable) inTable = true; // still mark table as started
                continue;
            }
            const cells = trimmed.slice(1, -1).split("|").map(c => c.trim());
            if (!inTable) {
                inTable = true;
                tableRows = [];
                tableRows.push(`<tr>${cells.map(c => `<th>${c}</th>`).join("")}</tr>`);
            } else {
                tableRows.push(`<tr>${cells.map(c => `<td>${c}</td>`).join("")}</tr>`);
            }
        } else {
            if (inTable) {
                result.push(`<table class="table table-sm table-bordered chat-table"><thead>${tableRows[0]}</thead><tbody>${tableRows.slice(1).join("")}</tbody></table>`);
                inTable = false;
                tableRows = [];
            }
            result.push(line);
        }
    }
    if (inTable) {
        result.push(`<table class="table table-sm table-bordered chat-table"><thead>${tableRows[0]}</thead><tbody>${tableRows.slice(1).join("")}</tbody></table>`);
    }
    return result.join("\n");
}

// ---------------------------------------------------------------------------
// Chat panel resize  (drag top-left corner handle)
// ---------------------------------------------------------------------------
(function initChatResize() {
    const handle = document.getElementById("chat-resize-handle");
    if (!handle) return;

    let startX, startY, startW, startH;

    handle.addEventListener("mousedown", onStart);
    handle.addEventListener("touchstart", onStart, { passive: false });

    function onStart(e) {
        e.preventDefault();
        const panel = document.getElementById("chat-panel");
        if (!panel) return;
        const rect = panel.getBoundingClientRect();
        startW = rect.width;
        startH = rect.height;
        const pt = e.touches ? e.touches[0] : e;
        startX = pt.clientX;
        startY = pt.clientY;

        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onEnd);
        document.addEventListener("touchmove", onMove, { passive: false });
        document.addEventListener("touchend", onEnd);
        document.body.style.userSelect = "none";
        panel.style.animation = "none";
    }

    function onMove(e) {
        e.preventDefault();
        const panel = document.getElementById("chat-panel");
        if (!panel) return;
        const pt = e.touches ? e.touches[0] : e;

        if (_chatPinned) {
            // Pinned: resize handle is on the left edge, drag horizontally only
            const dx = startX - pt.clientX; // dragging left → wider
            const newW = Math.max(300, Math.min(startW + dx, window.innerWidth * 0.5));
            panel.style.width = newW + "px";
            _syncPinnedWidth();
        } else {
            // Floating: resize from top-left corner
            const dx = startX - pt.clientX; // dragging left → wider
            const dy = startY - pt.clientY; // dragging up → taller
            const newW = Math.max(300, Math.min(startW + dx, window.innerWidth - 32));
            const newH = Math.max(280, Math.min(startH + dy, window.innerHeight - 112));
            panel.style.width = newW + "px";
            panel.style.height = newH + "px";
        }
    }

    function onEnd() {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onEnd);
        document.removeEventListener("touchmove", onMove);
        document.removeEventListener("touchend", onEnd);
        document.body.style.userSelect = "";
        // Persist size
        const panel = document.getElementById("chat-panel");
        if (panel) {
            try {
                localStorage.setItem("azm-chat-w", panel.style.width);
                localStorage.setItem("azm-chat-h", panel.style.height);
                if (_chatPinned) _syncPinnedWidth();
            } catch {}
        }
    }

    // Restore saved size on load
    try {
        const w = localStorage.getItem("azm-chat-w");
        const h = localStorage.getItem("azm-chat-h");
        const panel = document.getElementById("chat-panel");
        if (panel) {
            if (w) panel.style.width = w;
            if (h) panel.style.height = h;
        }
    } catch {}
})();
