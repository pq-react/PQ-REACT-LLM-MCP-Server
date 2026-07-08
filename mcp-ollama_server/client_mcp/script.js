const API = '';

  // ── Conversation history ─────────────────────────────────────────────
  // A flat list of {role, content} pairs sent with each /chat call so
  // the agent can resolve back-references like "list 10 of them" against
  // prior turns. Capped at the last 12 user/assistant pairs (24 entries)
  // so the request payload doesn't grow unboundedly. Persisted only in
  // memory — survives within a tab session, gone on hard refresh; the
  // explicit "Clear chat" button forgets it on demand.
  const HISTORY_MAX_TURNS = 24;
  let chatHistory = [];

  function pushHistory(role, content) {
    if (!content) return;
    chatHistory.push({ role, content: String(content).slice(0, 16000) });
    if (chatHistory.length > HISTORY_MAX_TURNS) {
      chatHistory = chatHistory.slice(-HISTORY_MAX_TURNS);
    }
  }

  function clearChat() {
    chatHistory = [];
    const area = document.getElementById('chat-area');
    if (area) area.innerHTML = '';
    const splash = document.getElementById('status-message');
    if (splash) splash.style.display = 'flex';
    addMessage('Conversation cleared.', 'system-msg');
  }

  function scrollToBottom() {
    const area = document.getElementById('chat-area');
    requestAnimationFrame(() => { area.scrollTop = area.scrollHeight; });
  }

  // ── Markdown rendering (safe-by-construction) ─────────────────────
  // The LLM emits markdown (headings, **bold**, _italic_, `code`,
  // ```fenced``` blocks, tables, lists, links). Before this we set
  // div.textContent which displayed it literally — the assistant
  // looked like it was talking in raw markdown. We now:
  //   1. HTML-escape the WHOLE string first (everything becomes inert
  //      text — no XSS possible from LLM/tool output here on)
  //   2. then add specific markdown tags back as <strong>, <em>,
  //      <code>, <pre>, <ul>/<ol>, <h1>…<h4>, <p>, <a>, <table>
  //   3. write the resulting HTML via innerHTML — safe because step 1
  //      neutralised every char that could change the DOM shape
  //
  // Restricted to block + inline forms we actually see on the wire.
  // Code fences are detected BEFORE any inline parsing so their
  // contents stay literal.
  function renderMarkdown(src) {
    if (!src) return '';
    src = String(src);
    const lines = src.split('\n').map(escapeHtml);
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];

      // ─── Code fence
      const fenceMatch = /^(\s*)```\s*([\w-]*)\s*$/.exec(line);
      if (fenceMatch) {
        const lang = fenceMatch[2] || '';
        i++;
        const start = i;
        while (i < lines.length && !/^(\s*)```\s*$/.test(lines[i])) i++;
        const body = lines.slice(start, i).join('\n');
        out.push('<pre><code'
          + (lang ? ` class="lang-${lang.replace(/[^\w-]/g,'')}"` : '')
          + '>' + body + '</code></pre>');
        if (i < lines.length) i++;
        continue;
      }

      // ─── Heading (## up to ######)
      const h = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
      if (h) {
        const level = Math.min(6, h[1].length);
        // level → real tag: ## → h2, ### → h3, etc. (matches the CSS
        // which styles .message.assistant h1..h4)
        const tag = 'h' + Math.min(4, Math.max(1, level));
        out.push('<' + tag + '>' + renderInline(h[2]) + '</' + tag + '>');
        i++; continue;
      }

      // ─── Horizontal rule
      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) {
        out.push('<hr>');
        i++; continue;
      }

      // ─── Table (header row + separator row).
      // Accept both plain markdown style ("|---|---|") and SQL-CLI
      // style ("---+---+---") in the rule row — `execute_query` and
      // other DB tools emit the +-separated form when relaying psql /
      // sqlite output, so the parser has to recognise that as a
      // table too (otherwise it renders as a wall of pipes that the
      // user can't read). The data rows still use `|` either way.
      if (/\|/.test(line)
          && i + 1 < lines.length
          && /^\s*[\s\-:|+]+\s*$/.test(lines[i + 1])
          && /-/.test(lines[i + 1])
          && /[|+]/.test(lines[i + 1])) {
        const header = splitRow(line);
        const aligns = splitRow(lines[i + 1].replace(/\+/g, '|')).map(cellAlign);
        i += 2;
        const body = [];
        while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== '') {
          body.push(splitRow(lines[i]));
          i++;
        }
        let t = '<table class="md-table"><thead><tr>';
        header.forEach((c, idx) => {
          const a = aligns[idx] ? ` style="text-align:${aligns[idx]}"` : '';
          t += '<th' + a + '>' + renderInline(c) + '</th>';
        });
        t += '</tr></thead><tbody>';
        for (const row of body) {
          t += '<tr>';
          row.forEach((c, idx) => {
            const a = aligns[idx] ? ` style="text-align:${aligns[idx]}"` : '';
            t += '<td' + a + '>' + renderInline(c) + '</td>';
          });
          t += '</tr>';
        }
        t += '</tbody></table>';
        out.push(t);
        continue;
      }

      // ─── Blockquote (consume consecutive > lines)
      if (/^\s*&gt;\s?/.test(line)) {
        const block = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
          block.push(lines[i].replace(/^\s*&gt;\s?/, ''));
          i++;
        }
        out.push('<blockquote>' + renderInline(block.join('\n')) + '</blockquote>');
        continue;
      }

      // ─── Unordered list
      if (/^\s*[-*+]\s+\S/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*+]\s+\S/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*+]\s+/, ''));
          i++;
        }
        out.push('<ul>'
          + items.map((x) => '<li>' + renderInline(x) + '</li>').join('')
          + '</ul>');
        continue;
      }

      // ─── Ordered list
      if (/^\s*\d+\.\s+\S/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+\.\s+\S/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
          i++;
        }
        out.push('<ol>'
          + items.map((x) => '<li>' + renderInline(x) + '</li>').join('')
          + '</ol>');
        continue;
      }

      // ─── Blank line → paragraph break (skip)
      if (/^\s*$/.test(line)) { i++; continue; }

      // ─── Paragraph (group consecutive non-special lines)
      const para = [];
      while (i < lines.length && !isBlockBoundary(lines, i)) {
        para.push(lines[i]);
        i++;
      }
      if (para.length > 0) {
        // Join with <br> so explicit single-line breaks survive.
        out.push('<p>' + renderInline(para.join('\n')).replace(/\n/g, '<br>') + '</p>');
      }
    }
    return out.join('\n');
  }

  // True iff the line at index k starts a new block-level construct.
  function isBlockBoundary(lines, k) {
    const line = lines[k];
    if (/^\s*$/.test(line)) return true;
    if (/^(\s*)```/.test(line)) return true;
    if (/^#{1,6}\s+/.test(line)) return true;
    if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) return true;
    if (/^\s*&gt;\s?/.test(line)) return true;
    if (/^\s*[-*+]\s+\S/.test(line)) return true;
    if (/^\s*\d+\.\s+\S/.test(line)) return true;
    if (/\|/.test(line)
        && k + 1 < lines.length
        && /^\s*[\s\-:|+]+\s*$/.test(lines[k + 1])
        && /-/.test(lines[k + 1])
        && /[|+]/.test(lines[k + 1])) return true;
    return false;
  }

  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith('|')) s = s.slice(1);
    if (s.endsWith('|'))   s = s.slice(0, -1);
    return s.split('|').map((c) => c.trim());
  }

  function cellAlign(spec) {
    spec = spec.trim();
    if (/^:.*:$/.test(spec))  return 'center';
    if (/^:/.test(spec))      return 'left';
    if (/:$/.test(spec))      return 'right';
    return '';
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Inline parsing. Operates on already-escaped text.
  // Order matters: inline code first (so its content isn't re-parsed),
  // then bold (`**` / `__`), then italic (`*` / `_`), then links.
  function renderInline(text) {
    if (!text) return '';

    // 1. Inline code — protect content from further inline parsing by
    //    replacing it with placeholders and restoring at the end.
    const codePlaceholders = [];
    text = text.replace(/`([^`\n]+)`/g, (_m, body) => {
      codePlaceholders.push('<code>' + body + '</code>');
      return '\x01CODE' + (codePlaceholders.length - 1) + '\x01';
    });

    // 2. Bold — **…** and __…__ (greedy minimal)
    text = text.replace(/\*\*(\S(?:.*?\S)?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/__(\S(?:.*?\S)?)__/g, '<strong>$1</strong>');

    // 3. Italic — *…* and _…_ with flanking rules:
    //    open delimiter is at start-of-text or preceded by whitespace
    //    or punctuation (NOT an alnum char), and the inner text must
    //    not be empty and must not start/end with whitespace. This
    //    makes "complexity_mod" inside "_Score = … hndl_mod_" stay
    //    italicised as a single span instead of triggering on the
    //    internal underscore.
    text = text.replace(
      /(^|[\s(\[{>])\*(\S(?:[\s\S]*?\S)?)\*(?=[\s.,;:!?)\]}>"']|$)/g,
      '$1<em>$2</em>',
    );
    text = text.replace(
      /(^|[\s(\[{>])_(\S(?:[\s\S]*?\S)?)_(?=[\s.,;:!?)\]}>"']|$)/g,
      '$1<em>$2</em>',
    );

    // 4. Links — [text](url). Only allow http(s) / mailto / # / /
    text = text.replace(
      /\[([^\]\n]+)\]\(([^)\s]+)\)/g,
      (_m, label, url) => {
        if (!/^(https?:\/\/|mailto:|#|\/|\.\/|\.\.\/)/.test(url)) return _m;
        return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">'
          + label + '</a>';
      },
    );

    // 5. Restore inline code placeholders.
    text = text.replace(/\x01CODE(\d+)\x01/g, (_m, n) => codePlaceholders[+n] || '');

    return text;
  }

  function addMessage(text, type) {
    const area = document.getElementById('chat-area');
    const div = document.createElement('div');
    div.className = `message ${type}`;
    // Assistant messages get markdown rendering (headings, tables,
    // bold/italic/code, lists, links). All other message types (user
    // input, system notices, error banners) stay as plain text so the
    // operator's literal keystrokes are preserved.
    if (type === 'assistant') {
      div.innerHTML = renderMarkdown(text);
    } else {
      div.textContent = text;
    }
    area.appendChild(div);
    scrollToBottom();
    return div;
  }

  function showThinking() {
    const area = document.getElementById('chat-area');
    const div = document.createElement('div');
    div.className = 'thinking';
    div.id = 'thinking-indicator';
    div.innerHTML = '<span></span><span></span><span></span>';
    area.appendChild(div);
    scrollToBottom();
  }

  function hideThinking() {
    const el = document.getElementById('thinking-indicator');
    if (el) el.remove();
  }

  function setConnected(connected, serverPath) {
    const dot  = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const dBtn = document.getElementById('disconnect-btn');
    const cBtn = document.getElementById('connect-btn');
    const input = document.getElementById('query-input');
    const send  = document.getElementById('send-btn');

    if (connected) {
      dot.classList.add('connected');
      if (text) text.textContent = `Connected · ${serverPath}`;
      if (dBtn) dBtn.style.display = 'block';
      if (cBtn) cBtn.disabled = true;
      input.disabled = false;
      send.disabled = false;
      input.focus();
    } else {
      dot.classList.remove('connected');
      if (text) text.textContent = 'Disconnected';
      if (dBtn) dBtn.style.display = 'none';
      if (cBtn) cBtn.disabled = false;
      input.disabled = true;
    //const toolsBar = document.getElementById('tools-bar');
      send.disabled = true;
      if (toolsBar) toolsBar.style.display = 'none';
    }
  }


  async function sendMessage() {
    const input = document.getElementById('query-input');
    const query = input.value.trim();
    if (!query) return;

    input.value = '';
    input.style.height = 'auto';
    addMessage(query, 'user');
    showThinking();
    // Hide the splash logo once the conversation starts.
    const splash = document.getElementById('status-message');
    if (splash && splash.style.display !== 'none') splash.style.display = 'none';

    const sendBtn = document.getElementById('send-btn');
    sendBtn.disabled = true;
    input.disabled = true;

    try {
      // Pass prior turns (NOT including the query we just added) so the
      // agent can resolve back-references on follow-ups.
      const historyPayload = [...chatHistory];
      const res = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, history: historyPayload })
      });
      const data = await res.json();
      hideThinking();
      if (!res.ok) throw new Error(data.detail || 'Request failed');
      // Record both turns in history AFTER the round-trip succeeds, so
      // a failed call doesn't poison the next turn.
      pushHistory('user', query);
      pushHistory('assistant', data.response);
      addMessage(data.response, 'assistant');
    } catch (e) {
      hideThinking();
      addMessage(`Error: ${e.message}`, 'error-msg');
    } finally {
      sendBtn.disabled = false;
      input.disabled = false;
      input.focus();
    }
  }

  document.getElementById('query-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });


  document.getElementById('query-input').addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 140) + 'px';
  });

  // ── Suggestions sidebar wiring ──────────────────────────────────────
  function fillSuggestion(text) {
    const input = document.getElementById('query-input');
    if (input.disabled) {
      addMessage('Not connected yet — wait for the connection to come up first.', 'system-msg');
      return;
    }
    input.value = text;
    // resize textarea to fit
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
    input.focus();
    // close the sidebar on small screens after a click
    const sb = document.getElementById('suggestions');
    if (sb && window.matchMedia('(max-width: 720px)').matches) sb.classList.remove('open');
  }
  document.querySelectorAll('#suggestions .suggestion').forEach(btn => {
    btn.addEventListener('click', () => fillSuggestion(btn.textContent.trim()));
  });
  // Mobile/responsive toggle
  const toggleBtn = document.getElementById('toggle-suggestions');
  const sidebar   = document.getElementById('suggestions');
  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener('click', () => sidebar.classList.toggle('open'));
  }

  // Clear-chat button (resets conversation history + the rendered area)
  const clearBtn = document.getElementById('clear-chat');
  if (clearBtn) clearBtn.addEventListener('click', clearChat);

  // ── Theme toggle (light / dark / auto-from-system) ─────────────────
  // The bootstrap script in <head> already set data-theme based on
  // saved preference + prefers-color-scheme. This handler cycles
  // through three states on click:  light → dark → auto → light → …
  // and writes the choice to localStorage so it survives reloads.
  (function wireThemeToggle() {
    const root = document.documentElement;
    const btn  = document.getElementById('theme-toggle');
    if (!btn) return;

    const ICONS = { dark: 'DARK', light: 'LIGHT', auto: 'AUTO' };
    const NEXT  = { light: 'dark', dark: 'auto', auto: 'light' };

    function refresh() {
      const t = root.getAttribute('data-theme') || 'auto';
      btn.textContent = ICONS[t] || ICONS.auto;
      btn.title = `Theme: ${t} — click for ${NEXT[t]}`;
    }
    refresh();

    btn.addEventListener('click', () => {
      const cur = root.getAttribute('data-theme') || 'auto';
      const next = NEXT[cur] || 'dark';
      root.setAttribute('data-theme', next);
      try {
        if (next === 'auto') localStorage.removeItem('pqreact_theme');
        else localStorage.setItem('pqreact_theme', next);
      } catch(e) { /* private mode etc. — ignore */ }
      refresh();
    });

    // React to OS theme changes when in 'auto' mode (no explicit choice).
    if (window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      const onChange = () => {
        if ((root.getAttribute('data-theme') || 'auto') === 'auto') refresh();
      };
      // Both APIs for older Safari compatibility.
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  })();


  document.getElementById('server-path').addEventListener('keydown', e => {
    if (e.key === 'Enter') connectServer();
  });

  // Default MCP SSE endpoint: read from server-injected meta tag, then
  // window.MCP_SERVER_URL global, then fall back to relative /sse on the
  // same origin. No hardcoded testbed IPs.
  const META_SERVER = document.querySelector('meta[name="mcp-server-url"]');
  const DEFAULT_SERVER = (META_SERVER && META_SERVER.content)
                       || window.MCP_SERVER_URL
                       || `${location.protocol}//${location.host}/sse`;

  (async () => {
    try {
      const res = await fetch(`${API}/status`);
      const data = await res.json();
      if (data.connected) {
        setConnected(true, data.server);
      } else {
        addMessage('Connecting to database…', 'system-msg');
        const res = await fetch(`${API}/connect`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ server_path: DEFAULT_SERVER })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Connection failed');
        setConnected(true, DEFAULT_SERVER, data.tools);
        addMessage('Welcome to Post-quantum Algorithm chat by pq-react project', 'system-msg');
      }
    } catch (e) {
      addMessage(`Auto-connect failed: ${e.message}`, 'error-msg');
    }
  })();

  // ── Phase B: deep-link prefill via ?q=URL-encoded-question ──────────
  // Lets external systems (e.g., the QUJATA Portal "Ask MCP" link) deep-link
  // into the chat with a contextual question already typed in the textarea.
  // We do NOT auto-send — the user reviews and presses Send. Keeps the
  // "human-in-the-loop" demo property and avoids accidental tool invocations.
  (function applyDeepLinkPrefill() {
    const params = new URLSearchParams(window.location.search);
    const q = params.get('q');
    if (!q) return;
    const apply = () => {
      const input = document.getElementById('query-input');
      if (!input) { setTimeout(apply, 100); return; }
      // wait until the input is enabled (auto-connect finished)
      if (input.disabled) { setTimeout(apply, 200); return; }
      input.value = q;
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 140) + 'px';
      input.focus();
      const send = document.getElementById('send-btn');
      if (send) send.classList.add('highlight');
      addMessage('Question pre-filled from a deep link. Review and press Send.', 'system-msg');
    };
    apply();
  })();
