/**
 * BeigeBox Vi Mode
 * Loaded dynamically only when web_ui_vi_mode: true in runtime_config.yaml.
 * Adds vim-style keybindings to chat and operator inputs.
 *
 * Modes: NORMAL, INSERT
 * Targets: #chat-input, #op-input, #search-input, #op-input
 */

(function () {
  'use strict';

  // ── State ───────────────────────────────────────────────────────────────
  let mode = 'INSERT';         // start in INSERT so the UI feels normal initially
  let lastKey = '';             // for two-key sequences: dd, gg, yy etc.
  let yankBuffer = '';          // yy / dd buffer
  let undoStack = {};           // per-element undo stack { el: [snapshots] }
  let searchQuery = '';         // / search within textarea
  const MAX_UNDO = 50;

  // ── Indicator ───────────────────────────────────────────────────────────
  const indicator = document.createElement('div');
  indicator.id = 'vi-indicator';
  Object.assign(indicator.style, {
    position:   'fixed',
    bottom:     '32px',        // just above the π button
    right:      '14px',
    fontFamily: "'Fira Code', 'Share Tech Mono', monospace",
    fontSize:   '11px',
    padding:    '2px 8px',
    borderRadius: '2px',
    zIndex:     '9999',
    pointerEvents: 'none',
    letterSpacing: '1px',
    transition: 'opacity 0.15s',
  });
  document.body.appendChild(indicator);

  function setMode(m) {
    mode = m;
    lastKey = '';
    indicator.textContent = m === 'NORMAL' ? '-- NORMAL --' : '-- INSERT --';
    indicator.style.background  = m === 'NORMAL' ? '#7D6080' : '#3a2a3a';
    indicator.style.color       = m === 'NORMAL' ? '#1A1A1A' : '#B48EAD';
    indicator.style.fontWeight  = m === 'NORMAL' ? '600' : '400';
  }

  setMode('INSERT');

  // ── Helpers ─────────────────────────────────────────────────────────────
  function activeInput() {
    const el = document.activeElement;
    if (el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') &&
        el.id !== 'vi-search-input') {
      return el;
    }
    // fallback to chat input if visible
    const chat = document.getElementById('chat-input');
    if (chat && chat.closest('.panel.active')) return chat;
    return null;
  }

  function saveUndo(el) {
    if (!undoStack[el.id]) undoStack[el.id] = [];
    undoStack[el.id].push({ value: el.value, start: el.selectionStart, end: el.selectionEnd });
    if (undoStack[el.id].length > MAX_UNDO) undoStack[el.id].shift();
  }

  function doUndo(el) {
    const stack = undoStack[el.id];
    if (!stack || !stack.length) return;
    const snap = stack.pop();
    el.value = snap.value;
    el.setSelectionRange(snap.start, snap.end);
  }

  function getLine(el) {
    const val = el.value;
    const pos = el.selectionStart;
    const start = val.lastIndexOf('\n', pos - 1) + 1;
    const end   = val.indexOf('\n', pos);
    return { start, end: end === -1 ? val.length : end };
  }

  function moveCursor(el, delta) {
    const pos = Math.max(0, Math.min(el.value.length, el.selectionStart + delta));
    el.setSelectionRange(pos, pos);
  }

  function moveToLineStart(el) {
    const { start } = getLine(el);
    el.setSelectionRange(start, start);
  }

  function moveToLineEnd(el) {
    const { end } = getLine(el);
    el.setSelectionRange(end, end);
  }

  function moveWordForward(el) {
    const val = el.value;
    let pos = el.selectionStart;
    // skip current word chars
    while (pos < val.length && /\S/.test(val[pos])) pos++;
    // skip whitespace
    while (pos < val.length && /\s/.test(val[pos])) pos++;
    el.setSelectionRange(pos, pos);
  }

  function moveWordBackward(el) {
    const val = el.value;
    let pos = el.selectionStart;
    if (pos === 0) return;
    pos--;
    // skip whitespace
    while (pos > 0 && /\s/.test(val[pos])) pos--;
    // skip word chars
    while (pos > 0 && /\S/.test(val[pos - 1])) pos--;
    el.setSelectionRange(pos, pos);
  }

  function deleteLine(el) {
    saveUndo(el);
    const val = el.value;
    const { start, end } = getLine(el);
    yankBuffer = val.slice(start, end);
    // Remove the line including trailing newline if present
    const removeEnd = end < val.length ? end + 1 : end;
    const removeStart = (start > 0 && removeEnd === val.length) ? start - 1 : start;
    el.value = val.slice(0, removeStart) + val.slice(removeEnd);
    el.setSelectionRange(removeStart, removeStart);
  }

  function yankLine(el) {
    const val = el.value;
    const { start, end } = getLine(el);
    yankBuffer = val.slice(start, end);
  }

  function pasteAfter(el) {
    if (!yankBuffer) return;
    saveUndo(el);
    const pos = el.selectionStart;
    const val = el.value;
    const newVal = val.slice(0, pos + 1) + '\n' + yankBuffer + val.slice(pos + 1);
    el.value = newVal;
    el.setSelectionRange(pos + 2, pos + 2);
  }

  function openLineBelow(el) {
    saveUndo(el);
    const { end } = getLine(el);
    const val = el.value;
    el.value = val.slice(0, end) + '\n' + val.slice(end);
    el.setSelectionRange(end + 1, end + 1);
    setMode('INSERT');
  }

  function openLineAbove(el) {
    saveUndo(el);
    const { start } = getLine(el);
    const val = el.value;
    const insertAt = start > 0 ? start - 1 : 0;
    if (start > 0) {
      el.value = val.slice(0, start) + '\n' + val.slice(start);
      el.setSelectionRange(start, start);
    } else {
      el.value = '\n' + val;
      el.setSelectionRange(0, 0);
    }
    setMode('INSERT');
  }

  function deleteChar(el) {
    saveUndo(el);
    const pos = el.selectionStart;
    if (pos < el.value.length) {
      el.value = el.value.slice(0, pos) + el.value.slice(pos + 1);
      el.setSelectionRange(pos, pos);
    }
  }

  function findInBuffer(el, query) {
    if (!query) return;
    const val = el.value;
    const start = el.selectionStart + 1;
    const idx = val.indexOf(query, start);
    if (idx !== -1) {
      el.setSelectionRange(idx, idx + query.length);
    } else {
      // wrap
      const wrapIdx = val.indexOf(query);
      if (wrapIdx !== -1) el.setSelectionRange(wrapIdx, wrapIdx + query.length);
    }
  }

  // ── Main keydown handler ─────────────────────────────────────────────────
  document.addEventListener('keydown', function (e) {
    // Escape always goes to NORMAL from any input
    if (e.key === 'Escape') {
      const el = activeInput();
      if (el) { e.preventDefault(); setMode('NORMAL'); return; }
    }

    if (mode === 'INSERT') return;  // let INSERT mode be normal

    const el = activeInput();
    if (!el) return;

    // Don't interfere with browser shortcuts
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    e.preventDefault();

    const k = e.key;
    const seq = lastKey + k;

    // ── Two-key sequences ──
    if (seq === 'dd') { deleteLine(el); lastKey = ''; return; }
    if (seq === 'yy') { yankLine(el);   lastKey = ''; return; }
    if (seq === 'gg') { el.setSelectionRange(0, 0); lastKey = ''; return; }

    // ── Single-key commands ──
    switch (k) {
      // Mode transitions
      case 'i': setMode('INSERT'); break;
      case 'I': moveToLineStart(el); setMode('INSERT'); break;
      case 'a': moveCursor(el, 1); setMode('INSERT'); break;
      case 'A': moveToLineEnd(el); setMode('INSERT'); break;
      case 'o': openLineBelow(el); break;
      case 'O': openLineAbove(el); break;

      // Motion
      case 'h': moveCursor(el, -1); break;
      case 'l': moveCursor(el,  1); break;
      case 'j': moveCursor(el, el.value.indexOf('\n', el.selectionStart) - el.selectionStart || 1); break;
      case 'k': {
        const pos = el.selectionStart;
        const prevNl = el.value.lastIndexOf('\n', pos - 1);
        const prevPrevNl = prevNl > 0 ? el.value.lastIndexOf('\n', prevNl - 1) : -1;
        const col = pos - (prevNl + 1);
        const lineStart = prevPrevNl + 1;
        el.setSelectionRange(Math.min(lineStart + col, prevNl), Math.min(lineStart + col, prevNl));
        break;
      }
      case 'w': moveWordForward(el); break;
      case 'b': moveWordBackward(el); break;
      case '0': moveToLineStart(el); break;
      case '$': moveToLineEnd(el); break;
      case 'G': el.setSelectionRange(el.value.length, el.value.length); break;

      // Edit
      case 'x': deleteChar(el); break;
      case 'p': pasteAfter(el); break;
      case 'u': doUndo(el); break;

      // Search
      case '/': {
        const q = prompt('Search:');
        if (q !== null) { searchQuery = q; findInBuffer(el, q); }
        break;
      }
      case 'n': findInBuffer(el, searchQuery); break;

      // Enter in normal mode — submit
      case 'Enter': {
        const sendBtn = document.getElementById('send-btn');
        const opBtn   = document.getElementById('op-btn');
        if (el.id === 'chat-input' && sendBtn && !sendBtn.disabled) {
          if (typeof window.sendChat === 'function') window.sendChat();
        } else if (el.id === 'op-input' && opBtn && !opBtn.disabled) {
          if (typeof window.runOp === 'function') window.runOp();
        }
        break;
      }

      default:
        // Buffer first key of potential two-key sequence
        if (k === 'd' || k === 'y' || k === 'g') { lastKey = k; return; }
    }

    lastKey = '';
  }, true);  // capture phase so we get it before textareas

  // ── Focus → INSERT ───────────────────────────────────────────────────────
  document.addEventListener('focusin', function (e) {
    if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') {
      if (mode === 'NORMAL') setMode('INSERT');
    }
  });

  console.log('[BeigeBox] vi mode loaded');
})();
