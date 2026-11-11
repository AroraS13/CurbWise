// Context panel: shows the message list the server sends to the model on every turn.
// Include with <script src="/context-panel.js"></script>, then call showContext(sessionId).
//
// CurbWise Phase 4A.2: overlay only — never injects body margins or shrinks the app/map.
// Closed by default. Open via setContextPanelOpen(true). Closing restores layout CSS
// without clearing timeline / agent traces (resetContext() still clears for a new run).
//
// Several agents: store an AgentContexts on the server (see context_panel.py) and call
// showContext(sessionId) on every change. The panel draws a timeline: a column per agent and a row
// per message, at the moment it was written. A sub-agent's fresh context starts where its parent
// called it; a handoff's copied conversation lines up with the original, dimmed. A dashed line marks
// an agent waiting on its own tool call. Columns share the panel's width; past about three on a
// laptop screen, the panel scrolls sideways. resetContext() empties the panel for a new run.

const CONTEXT_PANEL_WIDTH = "min(28rem, 92vw)";

const contextStyle = document.createElement("style");
contextStyle.textContent = `
    :root { --context-width: ${CONTEXT_PANEL_WIDTH}; }
    #context-backdrop {
        display: none;
        position: fixed; inset: 0;
        background: rgba(23, 38, 66, 0.42);
        z-index: 3990;
        border: none;
        padding: 0;
        margin: 0;
        cursor: pointer;
    }
    body.ctx-open #context-backdrop { display: block; }
    #context-panel {
        display: none;
        position: fixed; top: 0; right: 0; bottom: 0;
        width: var(--context-width);
        max-width: min(28rem, 92vw);
        flex-direction: column;
        background: #f7f9fc;
        border-left: 1px solid #d5dbe7;
        font-family: ui-monospace, monospace;
        font-size: 0.75rem;
        z-index: 4000;
        box-shadow: -12px 0 40px rgba(23, 38, 66, 0.18);
    }
    body.ctx-open #context-panel { display: flex; }
    #context-panel .ctx-chrome {
        flex: 0 0 auto;
        position: sticky; top: 0; z-index: 5;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        padding: 0.85rem 1rem;
        background: #172642;
        color: #fff;
        border-bottom: 1px solid rgba(255,255,255,0.12);
    }
    #context-panel .ctx-chrome h2 {
        margin: 0;
        font-family: system-ui, sans-serif;
        font-size: 0.78rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #fff;
    }
    #context-panel .ctx-close {
        flex: 0 0 auto;
        border: 1px solid rgba(255,255,255,0.35);
        background: #356DF3;
        color: #fff;
        border-radius: 8px;
        padding: 0.4rem 0.75rem;
        font-family: system-ui, sans-serif;
        font-size: 0.8rem;
        font-weight: 700;
        cursor: pointer;
        line-height: 1.2;
    }
    #context-panel .ctx-close:hover,
    #context-panel .ctx-close:focus-visible {
        background: #2a5ad1;
        outline: 2px solid #9db6ff;
        outline-offset: 2px;
    }
    #context-panel-body {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
        overflow-x: auto;
        padding: 1rem;
        scroll-padding: 1rem;
    }
    #context-panel-body.timeline { padding: 0 1rem 1rem; }
    #context-panel h2.ctx-section-title, .ctx-head {
        font-family: system-ui, sans-serif;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #6b7a90;
    }
    #context-panel h2.ctx-section-title { margin: 0 0 1rem; }
    .ctx-grid { display: grid; grid-template-columns: repeat(var(--ctx-cols, 1), minmax(14rem, 1fr)); column-gap: 1rem; }
    .ctx-head {
        position: sticky; top: 0; z-index: 1;
        background: #f7f9fc;
        padding: 0.75rem 0 0.6rem;
        border-top: 3px solid transparent;
        margin-bottom: 0.5rem;
    }
    .ctx-head.active { color: #172642; border-top-color: #356DF3; }
    .ctx-wait { border-left: 2px dashed #ccc; margin-left: 1px; }
    .ctx-note { font-family: system-ui, sans-serif; color: #6b7a90; padding: 0.2rem 0.75rem; margin-bottom: 0.5rem; }
    .ctx-note.divider { border-top: 1px solid #d5dbe7; padding-top: 0.5rem; }
    .context-msg {
        border-left: 3px solid #999;
        padding: 0.4rem 0.75rem;
        margin-bottom: 0.5rem;
        white-space: pre-wrap;
        word-break: break-word;
        background: #fff;
        border-radius: 0 8px 8px 0;
    }
    .context-msg .role { font-weight: bold; }
    .context-msg.system { border-color: #DF5A67; }
    .context-msg.user { border-color: #0DAF98; }
    .context-msg.assistant { border-color: #356DF3; }
    .context-msg.tool { border-color: #F3AB47; }
    .context-msg.new { animation: context-flash 1.5s; }
    .context-msg.thinking { border-color: #ccc; color: #aaa; }
    .context-msg summary { cursor: pointer; color: #666; }
    .context-msg.copied { opacity: 0.55; }
    @keyframes context-flash { from { background: #fef3c7; } }
    @media (max-width: 800px) {
        #context-panel {
            left: 0; right: 0; top: auto; bottom: 0;
            width: 100%;
            max-width: 100%;
            height: min(72vh, 34rem);
            border-left: none;
            border-top: 1px solid #d5dbe7;
            border-radius: 14px 14px 0 0;
        }
    }
`;
document.head.appendChild(contextStyle);

const contextBackdrop = document.createElement("button");
contextBackdrop.id = "context-backdrop";
contextBackdrop.type = "button";
contextBackdrop.setAttribute("aria-label", "Dismiss advanced context");
document.body.appendChild(contextBackdrop);

const contextPanel = document.createElement("aside");
contextPanel.id = "context-panel";
contextPanel.setAttribute("aria-hidden", "true");
contextPanel.setAttribute("role", "dialog");
contextPanel.setAttribute("aria-modal", "true");
contextPanel.setAttribute("aria-label", "Advanced context");
contextPanel.innerHTML =
    '<div class="ctx-chrome">' +
    "<h2>Advanced context</h2>" +
    '<button type="button" class="ctx-close" id="ctx-close">Close</button>' +
    "</div>" +
    '<div id="context-panel-body"></div>';
document.body.appendChild(contextPanel);

const contextPanelBody = document.getElementById("context-panel-body");
const contextCloseBtn = document.getElementById("ctx-close");

const CONTEXT_FOLD = 1500;
const TIMELINE_FOLD = 700;

let contextShown = 0;
let contextQueue = Promise.resolve();
let timeline = null;
let contextPanelOpen = false;
let pendingSessionId = null;

function clearContextLayoutEffects() {
    document.body.classList.remove("ctx-open");
    document.documentElement.style.removeProperty("--context-width");
    document.body.style.removeProperty("margin-right");
    document.body.style.removeProperty("marginLeft");
    document.body.style.removeProperty("padding-right");
    document.body.style.removeProperty("width");
    contextPanel.style.removeProperty("top");
    contextPanel.style.removeProperty("height");
    contextBackdrop.style.removeProperty("top");
    contextBackdrop.style.removeProperty("height");
}

function syncContextPanelToTopbar() {
    // Mobile uses a bottom sheet via CSS — do not override those positions.
    if (window.matchMedia("(max-width: 800px)").matches) {
        contextPanel.style.removeProperty("top");
        contextPanel.style.removeProperty("height");
        contextBackdrop.style.removeProperty("top");
        contextBackdrop.style.removeProperty("height");
        return;
    }
    // Desktop: keep the search header usable; overlay the workspace only.
    const topbar = document.querySelector(".topbar");
    const top = topbar ? Math.ceil(topbar.getBoundingClientRect().bottom) : 0;
    contextPanel.style.top = top + "px";
    contextPanel.style.height = `calc(100dvh - ${top}px)`;
    contextBackdrop.style.top = top + "px";
    contextBackdrop.style.height = `calc(100dvh - ${top}px)`;
}

function setContextPanelOpen(open) {
    contextPanelOpen = !!open;
    if (contextPanelOpen) {
        document.documentElement.style.setProperty("--context-width", CONTEXT_PANEL_WIDTH);
        syncContextPanelToTopbar();
        document.body.classList.add("ctx-open");
        contextPanel.setAttribute("aria-hidden", "false");
        if (pendingSessionId) showContext(pendingSessionId);
        // Focus close without destroying timeline DOM.
        requestAnimationFrame(() => {
            try {
                contextCloseBtn.focus({ preventScroll: true });
            } catch (_) {
                contextCloseBtn.focus();
            }
        });
    } else {
        clearContextLayoutEffects();
        contextPanel.setAttribute("aria-hidden", "true");
    }
    if (typeof window.__curbwiseOnContextPanelToggle === "function") {
        window.__curbwiseOnContextPanelToggle(contextPanelOpen);
    }
    return contextPanelOpen;
}

function isContextPanelOpen() {
    return contextPanelOpen;
}

function showContext(sessionId) {
    pendingSessionId = sessionId;
    // Always fetch/update traces; only paint into a visible panel when open.
    contextQueue = contextQueue.then(() => loadContext(sessionId)).catch((e) => console.error(e));
    return contextQueue;
}

function resetContext() {
    contextShown = 0;
    timeline = null;
    pendingSessionId = null;
    contextPanelBody.className = "";
    contextPanelBody.innerHTML = "";
    // Closing is optional — preserve open/closed preference across runs, but never leave margins.
    if (!contextPanelOpen) clearContextLayoutEffects();
}

async function loadContext(sessionId) {
    const after = timeline ? timeline.seq : 0;
    const res = await fetch("/context?session_id=" + encodeURIComponent(sessionId) + "&after=" + after);
    const data = await res.json();
    if (!contextPanelOpen) return; // traces stay on server; UI stays clean until opened
    if (Array.isArray(data)) renderSingle(data);
    else renderTimeline(data.events);
}

function renderSingle(messages) {
    contextPanelBody.className = "";
    contextPanelBody.innerHTML = '<h2 class="ctx-section-title">Context (' + messages.length + " messages)</h2>";
    messages.forEach((m, i) => {
        const isNew = i >= contextShown;
        if (m.thinking) contextPanelBody.appendChild(contextBlock("thinking", m.thinking, isNew));
        contextPanelBody.appendChild(contextBlock(m.role, m.content ?? formatToolCalls(m.tool_calls), isNew));
    });
    contextShown = messages.length;
    contextPanelBody.scrollTop = contextPanelBody.scrollHeight;
}

function renderTimeline(events) {
    if (!timeline) {
        const grid = document.createElement("div");
        grid.className = "ctx-grid";
        contextPanelBody.innerHTML = "";
        contextPanelBody.className = "timeline";
        contextPanelBody.appendChild(grid);
        timeline = { grid, seq: 0, row: 1, agents: [], heads: {}, rows: {}, waiting: {}, rowOf: {}, taken: new Set() };
    }
    const atBottom = contextPanelBody.scrollTop + contextPanelBody.clientHeight >= contextPanelBody.scrollHeight - 40;
    let last = null;
    for (const e of events) {
        if (e.seq < timeline.seq) continue;
        timeline.seq = e.seq + 1;
        if (!timeline.agents.includes(e.agent)) addTimelineAgent(e.agent);
        last = e.agent;

        const cell = timelineCell(e);
        if (cell) {
            let row = timeline.rowOf[e.at];
            if (row !== undefined && timeline.taken.has(e.agent + "|" + row)) row = undefined;
            if (row === undefined) {
                row = ++timeline.row;
                for (const other of timeline.agents) {
                    if (other !== e.agent && timeline.waiting[other]) {
                        placeCell(Object.assign(document.createElement("div"), { className: "ctx-wait" }), other, row);
                    }
                }
            }
            timeline.rowOf[e.seq] = row;
            timeline.taken.add(e.agent + "|" + row);
            placeCell(cell, e.agent, row);
            timeline.rows[e.agent] = (timeline.rows[e.agent] ?? 0) + 1;
        }
        if (e.type === "context") timeline.waiting[e.agent] = false;
        if (e.type === "message" && !e.from) timeline.waiting[e.agent] = e.message.role === "assistant" && !!e.message.tool_calls;
    }
    if (!last) return;
    for (const [agent, head] of Object.entries(timeline.heads)) head.classList.toggle("active", agent === last);
    timeline.heads[last].scrollIntoView({ inline: "nearest", block: "nearest" });
    if (atBottom) contextPanelBody.scrollTop = contextPanelBody.scrollHeight;
}

function addTimelineAgent(agent) {
    timeline.agents.push(agent);
    const n = timeline.agents.length;
    const head = document.createElement("div");
    head.className = "ctx-head";
    head.textContent = agent;
    timeline.heads[agent] = head;
    placeCell(head, agent, 1);
    timeline.grid.style.setProperty("--ctx-cols", n);
    // Width stays capped — never grow body margins or shrink the map.
    if (contextPanelOpen) {
        document.documentElement.style.setProperty("--context-width", CONTEXT_PANEL_WIDTH);
    }
}

function timelineCell(e) {
    if (e.type === "message") {
        const m = e.message;
        const block = contextBlock(m.role, m.content ?? formatToolCalls(m.tool_calls), true, TIMELINE_FOLD);
        if (e.from) {
            block.classList.add("copied");
            block.querySelector(".role").textContent += " (from " + e.from + ")";
        }
        return block;
    }
    if (e.type !== "context" || !timeline.rows[e.agent]) return null;
    const note = document.createElement("div");
    note.className = "ctx-note divider";
    note.textContent = "new context (" + e.total + " messages)";
    return note;
}

function placeCell(cell, agent, row) {
    cell.style.gridColumn = timeline.agents.indexOf(agent) + 1;
    cell.style.gridRow = row;
    timeline.grid.appendChild(cell);
}

function formatToolCalls(calls) {
    return (calls ?? [])
        .map((call) => {
            const name = call.name ?? call.function?.name;
            let args = call.arguments ?? call.function?.arguments;
            if (typeof args === "string") {
                try {
                    args = JSON.parse(args);
                } catch {}
            }
            const lines =
                args && typeof args === "object"
                    ? Object.entries(args).map(([k, v]) => k + ": " + (typeof v === "string" ? v : JSON.stringify(v, null, 2)))
                    : [String(args ?? "")].filter(Boolean);
            if (!lines.length) return name + "()";
            return name + "(\n" + lines.map((l) => "  " + l.replaceAll("\n", "\n  ")).join("\n") + "\n)";
        })
        .join("\n");
}

function contextBlock(label, text, isNew, fold = CONTEXT_FOLD) {
    const div = document.createElement("div");
    div.className = "context-msg " + label + (isNew ? " new" : "");
    const role = document.createElement("div");
    role.className = "role";
    role.textContent = label;
    div.appendChild(role);
    const body = text == null ? "" : String(text);
    if (body.length > fold) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = body.slice(0, 300) + "... (" + body.length + " characters)";
        details.append(summary, body);
        div.appendChild(details);
    } else {
        div.append(body);
    }
    return div;
}

contextCloseBtn.addEventListener("click", () => setContextPanelOpen(false));
contextBackdrop.addEventListener("click", () => setContextPanelOpen(false));
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && contextPanelOpen) {
        setContextPanelOpen(false);
    }
});
window.addEventListener("resize", () => {
    if (contextPanelOpen) syncContextPanelToTopbar();
});
