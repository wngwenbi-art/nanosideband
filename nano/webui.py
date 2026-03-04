"""
nano/webui.py
Flask-based web UI for NanoSideband.

Access from any browser on the same network — phone, tablet, laptop.
All HTML/CSS/JS is inlined here (zero static files needed).

Routes
------
  GET  /                        redirect to /conversations
  GET  /conversations           conversation list
  GET  /conversations/<hash>    message thread
  POST /send/<hash>             send text message
  POST /send_image/<hash>       send image (multipart form)
  POST /new                     create new conversation
  POST /announce                trigger announce
  DELETE /conversations/<hash>  clear conversation
  GET  /image/<msg_hash>        serve stored image blob
  GET  /whoami                  identity info
  GET  /api/conversations       JSON list
  GET  /api/messages/<hash>     JSON messages
  GET  /api/status              JSON system status
  GET  /events                  SSE stream for live updates

Usage
-----
    from nano.webui import run_web
    run_web(core, host="0.0.0.0", port=5000)
"""

import base64
import json
import logging
import queue
import threading
import time
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── HTML templates (inline) ───────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #1a1a2e; --bg2: #16213e; --bg3: #0f3460;
  --accent: #e94560; --accent2: #0f9b58;
  --text: #e0e0e0; --text2: #a0a0b0; --border: #2a2a4a;
  --msg-in: #1e2d4a; --msg-out: #1a3a2a;
  --font: 'Segoe UI', system-ui, -apple-system, sans-serif;
}
body { background: var(--bg); color: var(--text); font-family: var(--font);
       font-size: 15px; height: 100dvh; display: flex; flex-direction: column; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Header */
.header { background: var(--bg3); padding: 10px 16px;
          display: flex; align-items: center; gap: 12px;
          border-bottom: 1px solid var(--border); flex-shrink: 0; }
.header h1 { font-size: 18px; font-weight: 700; color: var(--accent); }
.header .hash { font-size: 11px; color: var(--text2); font-family: monospace; flex: 1; }
.dot { width: 10px; height: 10px; border-radius: 50%;
       background: var(--accent2); flex-shrink: 0; }
.dot.offline { background: var(--accent); }
.btn { background: var(--accent); color: white; border: none; border-radius: 6px;
       padding: 6px 14px; cursor: pointer; font-size: 14px; font-weight: 600; }
.btn:hover { opacity: 0.85; }
.btn.secondary { background: var(--bg3); border: 1px solid var(--border); }
.btn.danger { background: #7a1a1a; }

/* Layout */
.layout { display: flex; flex: 1; overflow: hidden; }
.sidebar { width: 280px; background: var(--bg2);
           border-right: 1px solid var(--border);
           display: flex; flex-direction: column; flex-shrink: 0; }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

/* Sidebar */
.sidebar-header { padding: 12px 16px; border-bottom: 1px solid var(--border);
                  display: flex; align-items: center; gap: 8px; }
.sidebar-header h2 { flex: 1; font-size: 14px; color: var(--text2); text-transform: uppercase;
                      letter-spacing: 0.5px; }
.conv-list { flex: 1; overflow-y: auto; }
.conv-item { padding: 12px 16px; border-bottom: 1px solid var(--border);
             cursor: pointer; display: flex; align-items: center; gap: 10px;
             transition: background 0.1s; }
.conv-item:hover { background: var(--bg3); }
.conv-item.active { background: var(--bg3); border-left: 3px solid var(--accent); }
.conv-avatar { width: 38px; height: 38px; border-radius: 50%;
               background: var(--bg3); display: flex; align-items: center;
               justify-content: center; font-weight: 700; color: var(--accent);
               flex-shrink: 0; font-size: 16px; }
.conv-info { flex: 1; min-width: 0; }
.conv-name { font-weight: 600; font-size: 14px; white-space: nowrap;
             overflow: hidden; text-overflow: ellipsis; }
.conv-preview { font-size: 12px; color: var(--text2); white-space: nowrap;
                overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }
.badge { background: var(--accent); color: white; border-radius: 10px;
         padding: 1px 7px; font-size: 11px; font-weight: 700; flex-shrink: 0; }
.empty-state { padding: 40px 20px; text-align: center; color: var(--text2); }
.empty-state p { margin-bottom: 12px; line-height: 1.5; }

/* Messages */
.msg-header { padding: 12px 16px; border-bottom: 1px solid var(--border);
              display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.msg-header h2 { flex: 1; font-size: 15px; font-weight: 600; }
.msg-header .peer-hash { font-size: 11px; color: var(--text2); font-family: monospace; }
.messages { flex: 1; overflow-y: auto; padding: 16px;
            display: flex; flex-direction: column; gap: 8px; }
.msg { max-width: 75%; border-radius: 12px; padding: 8px 12px;
       line-height: 1.5; word-break: break-word; }
.msg.in { background: var(--msg-in); align-self: flex-start;
          border-bottom-left-radius: 2px; }
.msg.out { background: var(--msg-out); align-self: flex-end;
           border-bottom-right-radius: 2px; }
.msg .sender { font-size: 11px; color: var(--text2); margin-bottom: 3px; font-weight: 600; }
.msg .body { font-size: 14px; }
.msg .meta { font-size: 11px; color: var(--text2); margin-top: 4px;
             display: flex; gap: 8px; justify-content: flex-end; }
.msg .state-icon { font-size: 12px; }
.state-pending { color: #888; }
.state-sent { color: #aaa; }
.state-delivered { color: var(--accent2); }
.state-failed { color: var(--accent); }
.msg img { max-width: 100%; border-radius: 8px; margin-top: 6px; display: block; }
.no-conv { flex: 1; display: flex; align-items: center; justify-content: center;
           color: var(--text2); font-size: 16px; }

/* Compose */
.compose { padding: 12px 16px; border-top: 1px solid var(--border);
           display: flex; gap: 8px; flex-shrink: 0; background: var(--bg2); }
.compose input[type=text] { flex: 1; background: var(--bg3); border: 1px solid var(--border);
                             border-radius: 8px; padding: 10px 14px; color: var(--text);
                             font-size: 15px; outline: none; }
.compose input[type=text]:focus { border-color: var(--accent); }
.compose label.img-btn { background: var(--bg3); border: 1px solid var(--border);
                          border-radius: 8px; padding: 10px 12px; cursor: pointer;
                          font-size: 18px; display: flex; align-items: center; }
.compose label.img-btn:hover { border-color: var(--accent); }
.compose input[type=file] { display: none; }

/* New conversation modal */
.modal-bg { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
            z-index: 100; align-items: center; justify-content: center; }
.modal-bg.open { display: flex; }
.modal { background: var(--bg2); border: 1px solid var(--border); border-radius: 12px;
         padding: 24px; width: 360px; max-width: 95vw; }
.modal h3 { margin-bottom: 16px; font-size: 16px; }
.modal input { width: 100%; background: var(--bg3); border: 1px solid var(--border);
               border-radius: 8px; padding: 10px 12px; color: var(--text);
               font-size: 14px; font-family: monospace; margin-bottom: 12px; outline: none; }
.modal input:focus { border-color: var(--accent); }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; }

/* Whoami page */
.info-card { background: var(--bg2); border: 1px solid var(--border);
             border-radius: 12px; padding: 24px; margin: 24px auto;
             max-width: 500px; }
.info-card h2 { margin-bottom: 16px; color: var(--accent); }
.info-row { display: flex; gap: 12px; margin-bottom: 10px; align-items: flex-start; }
.info-label { color: var(--text2); font-size: 13px; width: 110px; flex-shrink: 0;
               padding-top: 2px; }
.info-value { font-family: monospace; font-size: 13px; word-break: break-all; }

/* Toast */
#toast { position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%);
         background: var(--bg3); border: 1px solid var(--border); border-radius: 8px;
         padding: 10px 20px; font-size: 14px; opacity: 0; transition: opacity 0.3s;
         z-index: 200; pointer-events: none; }
#toast.show { opacity: 1; }

/* Scrollbar */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* Mobile */
@media (max-width: 600px) {
  .sidebar { display: none; }
  .sidebar.mobile-open { display: flex; position: fixed; inset: 0; z-index: 50;
                          width: 100%; }
  .msg { max-width: 90%; }
  .msg-header .peer-hash { display: none; }
}
"""

_JS = """
// ── SSE live updates ─────────────────────────────────────────────────────────
let evtSource = null;
let currentHash = window.location.pathname.startsWith('/conversations/')
    ? window.location.pathname.split('/').pop() : null;

function connectSSE() {
    evtSource = new EventSource('/events');
    evtSource.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'message') {
            if (data.peer === currentHash) {
                appendMessage(data.msg);
                scrollToBottom();
            }
            refreshConvList();
            showToast('← ' + (data.name || data.peer.slice(0,8)) + ': ' + data.preview);
        } else if (data.type === 'status') {
            refreshConvList();
        }
    };
    evtSource.onerror = () => {
        setTimeout(connectSSE, 3000);
    };
}

// ── Message rendering ─────────────────────────────────────────────────────────
function appendMessage(msg) {
    const box = document.getElementById('messages');
    if (!box) return;
    const el = buildMsgEl(msg);
    box.appendChild(el);
}

function buildMsgEl(msg) {
    const div = document.createElement('div');
    div.className = 'msg ' + msg.direction;
    div.dataset.hash = msg.msg_hash;

    const stateIcons = {pending:'○', sent:'◎', delivered:'●', failed:'✗'};
    const stateClasses = {pending:'state-pending', sent:'state-sent',
                          delivered:'state-delivered', failed:'state-failed'};
    const icon = msg.direction === 'out'
        ? `<span class="state-icon ${stateClasses[msg.state]||''}">${stateIcons[msg.state]||''}</span>` : '';

    const ts = new Date(msg.timestamp * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    const sender = msg.direction === 'in'
        ? `<div class="sender">${escHtml(msg.display_name || msg.source_hash.slice(0,12))}</div>` : '';

    let body = `<div class="body">${escHtml(msg.content || '')}</div>`;
    if (msg.has_image) {
        body += `<img src="/image/${msg.msg_hash}" loading="lazy" alt="image">`;
    }

    div.innerHTML = sender + body + `<div class="meta"><span>${ts}</span>${icon}</div>`;
    return div;
}

function scrollToBottom() {
    const box = document.getElementById('messages');
    if (box) box.scrollTop = box.scrollHeight;
}

// ── Conversation list refresh ─────────────────────────────────────────────────
function refreshConvList() {
    fetch('/api/conversations')
        .then(r => r.json())
        .then(data => {
            const list = document.getElementById('conv-list');
            if (!list) return;
            // Update badges only (avoid full re-render flicker)
            data.forEach(c => {
                const item = list.querySelector(`[data-hash="${c.dest_hash}"]`);
                if (item) {
                    const badge = item.querySelector('.badge');
                    if (c.unread > 0) {
                        if (badge) badge.textContent = c.unread;
                        else item.insertAdjacentHTML('beforeend',
                            `<span class="badge">${c.unread}</span>`);
                    } else if (badge) badge.remove();
                }
            });
        });
}

// ── Send ──────────────────────────────────────────────────────────────────────
function sendMessage(hash) {
    const inp = document.getElementById('compose-input');
    const text = inp.value.trim();
    if (!text) return;
    inp.value = '';

    fetch('/send/' + hash, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({content: text})
    })
    .then(r => r.json())
    .then(d => {
        if (d.msg_hash) {
            appendMessage({
                msg_hash: d.msg_hash, direction: 'out', content: text,
                timestamp: Date.now()/1000, state: 'pending', has_image: false
            });
            scrollToBottom();
        } else {
            showToast('Send failed: ' + (d.error || 'unknown error'));
        }
    });
}

// ── Image send ────────────────────────────────────────────────────────────────
function sendImage(hash, file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('image', file);
    const cap = document.getElementById('compose-input');
    if (cap && cap.value.trim()) {
        fd.append('caption', cap.value.trim());
        cap.value = '';
    }
    showToast('Sending image…');
    fetch('/send_image/' + hash, {method: 'POST', body: fd})
        .then(r => r.json())
        .then(d => {
            if (d.msg_hash) {
                appendMessage({
                    msg_hash: d.msg_hash, direction: 'out', content: '',
                    timestamp: Date.now()/1000, state: 'pending', has_image: true
                });
                scrollToBottom();
            } else {
                showToast('Image send failed: ' + (d.error || '?'));
            }
        });
}

// ── New conversation ──────────────────────────────────────────────────────────
function openNewConv() {
    document.getElementById('new-modal').classList.add('open');
    document.getElementById('new-hash').focus();
}
function closeNewConv() {
    document.getElementById('new-modal').classList.remove('open');
}
function submitNewConv() {
    const h = document.getElementById('new-hash').value.trim()
        .replace(/[<>]/g, '').toLowerCase();
    if (h.length !== 32) { showToast('Hash must be 32 hex chars'); return; }
    fetch('/new', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({hash: h})
    }).then(() => { window.location = '/conversations/' + h; });
}

// ── Announce ──────────────────────────────────────────────────────────────────
function doAnnounce() {
    fetch('/announce', {method: 'POST'})
        .then(r => r.json())
        .then(() => showToast('Announced!'));
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => t.classList.remove('show'), 3000);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    connectSSE();
    scrollToBottom();

    // Enter key in compose
    const inp = document.getElementById('compose-input');
    if (inp) {
        inp.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage(currentHash);
            }
        });
    }

    // Enter key in new-conv modal
    const nh = document.getElementById('new-hash');
    if (nh) nh.addEventListener('keydown', e => {
        if (e.key === 'Enter') submitNewConv();
        if (e.key === 'Escape') closeNewConv();
    });

    // Click outside modal to close
    document.getElementById('new-modal')?.addEventListener('click', e => {
        if (e.target.id === 'new-modal') closeNewConv();
    });
});
"""


def _page(title: str, body: str, active_hash: str = "") -> str:
    """Wrap body in full HTML page shell."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#1a1a2e">
<title>{title} — NanoSideband</title>
<style>{_CSS}</style>
</head>
<body>
{body}
<div id="toast"></div>
<script>{_JS}</script>
</body>
</html>"""


def _header_html(core) -> str:
    h = (core.identity_hash or "").strip("<>")
    dot_class = "dot" if core._running else "dot offline"
    return f"""
<div class="header">
  <h1>NanoSideband</h1>
  <span class="hash">{h[:16]}…{h[-8:] if h else ''}</span>
  <span class="{dot_class}" title="{'Connected' if core._running else 'Offline'}"></span>
  <button class="btn secondary" onclick="doAnnounce()">📡</button>
  <a href="/whoami" class="btn secondary" title="Identity">👤</a>
</div>"""


def _conv_list_html(core, db, active_hash: str = "") -> str:
    convs   = {c["peer"]: c for c in db.list_conversations()}
    contacts = db.list_contacts()

    # Merge contacts + anyone who messaged us
    known = {c["dest_hash"] for c in contacts}
    for peer, conv in convs.items():
        if peer not in known:
            contacts.append({
                "dest_hash":    peer,
                "display_name": peer[:8] + "…",
                "unread":       conv.get("unread", 0),
                "last_ts":      conv.get("last_ts", 0),
            })

    for c in contacts:
        conv = convs.get(c["dest_hash"], {})
        c["unread"]  = conv.get("unread", 0)
        c["last_ts"] = conv.get("last_ts", c.get("last_seen", 0))

    contacts.sort(key=lambda c: c.get("last_ts", 0), reverse=True)

    items = ""
    for c in contacts:
        h     = c["dest_hash"]
        name  = c.get("display_name") or h[:8] + "…"
        badge = f'<span class="badge">{c["unread"]}</span>' if c.get("unread") else ""
        active = "active" if h == active_hash else ""
        avatar_letter = (name[0] if name else "?").upper()
        items += f"""
  <a href="/conversations/{h}" class="conv-item {active}" data-hash="{h}">
    <div class="conv-avatar">{avatar_letter}</div>
    <div class="conv-info">
      <div class="conv-name">{name[:20]}</div>
      <div class="conv-preview">{h[:16]}…</div>
    </div>
    {badge}
  </a>"""

    if not items:
        items = """
  <div class="empty-state">
    <p>No conversations yet.</p>
    <p>Press <strong>+</strong> to start one,<br>or announce to be discovered.</p>
  </div>"""

    return f"""
<div class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <h2>Conversations</h2>
    <button class="btn" onclick="openNewConv()" title="New conversation">+</button>
  </div>
  <div class="conv-list" id="conv-list">{items}</div>
</div>"""


def _new_conv_modal() -> str:
    return """
<div class="modal-bg" id="new-modal">
  <div class="modal">
    <h3>New Conversation</h3>
    <input id="new-hash" type="text" maxlength="32" placeholder="32-char destination hash…"
           autocomplete="off" spellcheck="false">
    <div class="modal-actions">
      <button class="btn secondary" onclick="closeNewConv()">Cancel</button>
      <button class="btn" onclick="submitNewConv()">Start</button>
    </div>
  </div>
</div>"""


# ── Flask app factory ─────────────────────────────────────────────────────────

def create_app(core, db):
    """Create and configure the Flask app."""
    try:
        from flask import Flask, Response, request, redirect, jsonify, stream_with_context
    except ImportError:
        raise ImportError("Flask required: pip install flask")

    from nano.image import image_to_field

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit

    # SSE subscriber queues
    _sse_queues: list[queue.Queue] = []
    _sse_lock = threading.Lock()

    def _push_event(data: dict):
        payload = "data: " + json.dumps(data) + "\n\n"
        with _sse_lock:
            dead = []
            for q in _sse_queues:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                _sse_queues.remove(q)

    # Register core callbacks to push SSE
    def _on_message(source_hash, display_name, content,
                    fields, timestamp, msg_hash, has_image, **kw):
        src = source_hash.strip("<>")
        _push_event({
            "type":    "message",
            "peer":    src,
            "name":    display_name or src[:8],
            "preview": (content or "")[:60] + (" [img]" if has_image else ""),
            "msg": {
                "msg_hash":     msg_hash.strip("<>"),
                "direction":    "in",
                "source_hash":  src,
                "display_name": display_name or src[:8],
                "content":      content or "",
                "timestamp":    timestamp,
                "state":        "delivered",
                "has_image":    has_image,
            }
        })

    def _on_status(msg_id, status, **kw):
        _push_event({"type": "status", "msg_id": msg_id, "status": status})

    core.on_message(_on_message)
    core.on_delivery_status(_on_status)

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return redirect("/conversations")

    @app.route("/conversations")
    def conversations():
        sidebar = _conv_list_html(core, db)
        body = f"""
{_header_html(core)}
<div class="layout">
  {sidebar}
  <div class="main">
    <div class="no-conv">Select or start a conversation</div>
  </div>
</div>
{_new_conv_modal()}"""
        return _page("Conversations", body)

    @app.route("/conversations/<peer_hash>")
    def conversation(peer_hash):
        peer_hash = peer_hash.strip().lower().replace("<","").replace(">","")
        if len(peer_hash) != 32:
            return redirect("/conversations")

        # Mark read
        db.mark_read(peer_hash)

        # Get contact name
        contact = db.get_contact(peer_hash)
        name = (contact or {}).get("display_name") or peer_hash[:12] + "…"

        # Get messages
        msgs = db.list_messages(peer_hash, limit=100)
        msgs.reverse()

        # Render messages
        msgs_html = ""
        my_hash = (core.identity_hash or "").strip("<>")
        for m in msgs:
            direction = m["direction"]
            state     = m.get("state", "")
            ts        = datetime.fromtimestamp(m["timestamp"]).strftime("%H:%M")
            content   = m.get("content", "") or ""
            has_img   = bool(m.get("has_image"))
            mhash     = m["msg_hash"]

            state_icons = {"pending":"○","sent":"◎","delivered":"●","failed":"✗"}
            state_cls   = {"pending":"state-pending","sent":"state-sent",
                           "delivered":"state-delivered","failed":"state-failed"}
            icon = ""
            if direction == "out":
                icon = f'<span class="state-icon {state_cls.get(state,"")}">{state_icons.get(state,"")}</span>'

            sender_html = ""
            if direction == "in":
                src = m.get("source_hash", "")
                src_name = (contact or {}).get("display_name") or src[:8]
                sender_html = f'<div class="sender">{src_name}</div>'

            img_html = f'<img src="/image/{mhash}" loading="lazy" alt="image">' if has_img else ""
            body_html = f'<div class="body">{content}</div>{img_html}'
            meta_html = f'<div class="meta"><span>{ts}</span>{icon}</div>'

            msgs_html += f'<div class="msg {direction}" data-hash="{mhash}">{sender_html}{body_html}{meta_html}</div>\n'

        sidebar = _conv_list_html(core, db, active_hash=peer_hash)

        body = f"""
{_header_html(core)}
<div class="layout">
  {sidebar}
  <div class="main">
    <div class="msg-header">
      <button class="btn secondary" onclick="history.back()" style="display:none" id="back-btn">←</button>
      <div>
        <h2>{name}</h2>
        <div class="peer-hash">{peer_hash}</div>
      </div>
      <a href="/conversations/{peer_hash}/delete" class="btn danger"
         onclick="return confirm('Delete conversation?')" title="Delete">🗑</a>
    </div>
    <div class="messages" id="messages">
      {msgs_html}
    </div>
    <div class="compose">
      <input type="text" id="compose-input" placeholder="Type a message…" autocomplete="off">
      <label class="img-btn" title="Send image">
        📷
        <input type="file" accept="image/*" onchange="sendImage('{peer_hash}', this.files[0])">
      </label>
      <button class="btn" onclick="sendMessage('{peer_hash}')">Send</button>
    </div>
  </div>
</div>
{_new_conv_modal()}
<script>
  // Override currentHash for this page
  currentHash = '{peer_hash}';
</script>"""
        return _page(name, body)

    @app.route("/conversations/<peer_hash>/delete")
    def delete_conversation(peer_hash):
        db.clear_conversation(peer_hash.lower())
        return redirect("/conversations")

    @app.route("/send/<peer_hash>", methods=["POST"])
    def send_message(peer_hash):
        peer_hash = peer_hash.lower()
        data = request.get_json(force=True, silent=True) or {}
        content = (data.get("content") or "").strip()
        if not content:
            return jsonify({"error": "empty message"}), 400
        msg_id = core.send_text(peer_hash, content)
        if msg_id:
            return jsonify({"msg_hash": msg_id.strip("<>")})
        return jsonify({"error": "send failed"}), 500

    @app.route("/send_image/<peer_hash>", methods=["POST"])
    def send_image_route(peer_hash):
        peer_hash = peer_hash.lower()
        f = request.files.get("image")
        if not f:
            return jsonify({"error": "no image"}), 400
        caption = request.form.get("caption", "")
        img_data = f.read()
        msg_id = core.send_image(peer_hash, img_data, caption=caption)
        if msg_id:
            return jsonify({"msg_hash": msg_id.strip("<>")})
        return jsonify({"error": "image send failed"}), 500

    @app.route("/new", methods=["POST"])
    def new_conversation():
        data = request.get_json(force=True, silent=True) or {}
        h = (data.get("hash") or "").lower().replace("<","").replace(">","")
        if len(h) == 32:
            db.upsert_contact(h)
        return jsonify({"ok": True})

    @app.route("/announce", methods=["POST"])
    def announce():
        core.announce()
        return jsonify({"ok": True})

    @app.route("/image/<msg_hash>")
    def serve_image(msg_hash):
        img = db.get_image(msg_hash)
        if not img:
            return "", 404
        mime = img.get("mime_type", "image/jpeg")
        return Response(img["image_data"], mimetype=mime,
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.route("/whoami")
    def whoami():
        h     = (core.identity_hash or "").strip("<>")
        name  = core.config.get("display_name", "") if isinstance(core.config, dict) else core.config.get("display_name", "")
        cdir  = str(getattr(core.config, 'base_dir', 'N/A'))
        stats = db.stats()
        body = f"""
{_header_html(core)}
<div style="padding:16px">
  <div class="info-card">
    <h2>Identity</h2>
    <div class="info-row">
      <div class="info-label">Hash</div>
      <div class="info-value">{h}</div>
    </div>
    <div class="info-row">
      <div class="info-label">Display name</div>
      <div class="info-value">{name or '(not set)'}</div>
    </div>
    <div class="info-row">
      <div class="info-label">Config dir</div>
      <div class="info-value">{cdir}</div>
    </div>
    <div class="info-row">
      <div class="info-label">Contacts</div>
      <div class="info-value">{stats['contacts']}</div>
    </div>
    <div class="info-row">
      <div class="info-label">Messages</div>
      <div class="info-value">{stats['messages']}</div>
    </div>
    <div class="info-row">
      <div class="info-label">Images</div>
      <div class="info-value">{stats['images']}</div>
    </div>
  </div>
</div>"""
        return _page("Identity", body)

    # ── JSON API ──────────────────────────────────────────────────────────────

    @app.route("/api/conversations")
    def api_conversations():
        convs    = {c["peer"]: c for c in db.list_conversations()}
        contacts = db.list_contacts()
        known    = {c["dest_hash"] for c in contacts}
        for peer, conv in convs.items():
            if peer not in known:
                contacts.append({
                    "dest_hash":    peer,
                    "display_name": peer[:8] + "…",
                })
        for c in contacts:
            conv = convs.get(c["dest_hash"], {})
            c["unread"]  = conv.get("unread", 0)
            c["last_ts"] = conv.get("last_ts", 0)
        contacts.sort(key=lambda x: x.get("last_ts", 0), reverse=True)
        return jsonify(contacts)

    @app.route("/api/messages/<peer_hash>")
    def api_messages(peer_hash):
        peer_hash = peer_hash.lower()
        limit     = int(request.args.get("limit", 50))
        before_ts = request.args.get("before_ts", type=float)
        msgs      = db.list_messages(peer_hash, limit=limit, before_ts=before_ts)
        msgs.reverse()

        contact = db.get_contact(peer_hash)
        name    = (contact or {}).get("display_name", "")

        result = []
        for m in msgs:
            m["display_name"] = name if m["direction"] == "in" else "You"
            result.append(m)
        return jsonify(result)

    @app.route("/api/status")
    def api_status():
        stats = db.stats()
        return jsonify({
            "running":       core._running,
            "identity_hash": core.identity_hash,
            "display_name":  core.config.get("display_name", ""),
            **stats,
        })

    # ── SSE endpoint ──────────────────────────────────────────────────────────

    @app.route("/events")
    def sse_events():
        q = queue.Queue(maxsize=50)
        with _sse_lock:
            _sse_queues.append(q)

        def generate():
            yield "data: " + json.dumps({"type": "connected"}) + "\n\n"
            try:
                while True:
                    try:
                        msg = q.get(timeout=15)
                        yield msg
                    except queue.Empty:
                        yield ": heartbeat\n\n"
            except GeneratorExit:
                with _sse_lock:
                    try:
                        _sse_queues.remove(q)
                    except ValueError:
                        pass

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )

    return app


# ── Entry point ───────────────────────────────────────────────────────────────

def run_web(core, host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
    """
    Start the Flask web server.

    Call after core.start():
        from nano.webui import run_web
        core.start()
        run_web(core)   # blocks
    """
    try:
        from flask import Flask
    except ImportError:
        raise ImportError("Flask required: pip install flask")

    app = create_app(core, core.db)

    import socket
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "localhost"

    log.info("Web UI starting on http://%s:%d", host, port)
    print(f"\nNanoSideband Web UI")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Network: http://{local_ip}:{port}")
    print(f"  Phone:   scan QR or open http://{local_ip}:{port}\n")

    # Use werkzeug directly to suppress startup banner noise
    from werkzeug.serving import make_server
    srv = make_server(host, port, app, threaded=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
