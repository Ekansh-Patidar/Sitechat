/**
 * An embeddable chat widget.
 *
 * Everything lives inside a shadow root, so the host page's CSS cannot reach
 * in and ours cannot leak out. The one exception is the font family, which is
 * inherited deliberately: a widget that imports its own typeface looks like
 * something bolted on.
 *
 * Nothing from the network is ever written as HTML. Model output rendered
 * into a customer's page would be an injection route into their site.
 */
(function () {
  "use strict";

  if (window.__sitechat) return;
  window.__sitechat = true;

  var script = document.currentScript;
  var siteKey = script && script.getAttribute("data-site-key");
  // data-teaser="off" keeps the launcher still, for sites that want no motion.
  var teaserAllowed = !(script && script.getAttribute("data-teaser") === "off");
  var API = new URL(script.src).origin;

  if (!siteKey) {
    console.error("[sitechat] The script tag needs a data-site-key attribute.");
    return;
  }

  var STORAGE_KEY = "sitechat:" + siteKey;
  var MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

  var conversationId = loadConversationId();
  var config = null;
  var isOpen = false;
  var busy = false;

  // ---------------------------------------------------------------- styles

  var CSS = `
    :host {
      all: initial;
      font-family: inherit;
    }

    *, *::before, *::after { box-sizing: border-box; }

    .root {
      --accent: #1f6f5c;
      --accent-ink: #ffffff;
      --ink: #1a1c1b;
      --ink-soft: #6a7370;
      --paper: #ffffff;
      --raised: #f6f8f7;
      --line: #e4e8e6;
      --shadow: 0 10px 40px rgba(0, 0, 0, 0.12), 0 2px 8px rgba(0, 0, 0, 0.06);

      font-family: inherit;
      font-size: 15px;
      line-height: 1.55;
      color: var(--ink);
    }

    @media (prefers-color-scheme: dark) {
      .root {
        --ink: #e8eceb;
        --ink-soft: #949e9b;
        --paper: #161918;
        --raised: #1f2423;
        --line: #2c3331;
        --shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
      }
    }

    /* ---------------------------------------------------------- launcher */

    .launcher {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 2147483000;

      display: inline-flex;
      align-items: center;
      gap: 9px;
      padding: 13px 20px 13px 17px;

      border: none;
      border-radius: 999px;
      background: var(--accent);
      color: var(--accent-ink);

      font: inherit;
      font-size: 14.5px;
      font-weight: 500;
      letter-spacing: 0.01em;
      cursor: pointer;

      box-shadow: var(--shadow);
      max-width: min(300px, calc(100vw - 48px));
      transition: transform 0.18s cubic-bezier(0.2, 0, 0.2, 1),
        width 0.35s cubic-bezier(0.2, 0, 0.2, 1);
    }

    .launch-text {
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      /* Clip, not "...": while the launcher widens, the text is revealed
         as it grows; at rest it always fits (fitsLauncher). */
      text-overflow: clip;
      transition: opacity 0.15s ease;
    }
    .launch-text.fading { opacity: 0; }

    .launcher:hover { transform: translateY(-1px); }
    .launcher:focus-visible {
      outline: 2px solid var(--ink);
      outline-offset: 3px;
    }
    .launcher[hidden] { display: none; }

    .launcher svg { flex-shrink: 0; }

    /* ------------------------------------------------------------- panel */

    .panel {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 2147483000;

      width: 390px;
      height: min(620px, calc(100vh - 48px));

      display: flex;
      flex-direction: column;
      overflow: hidden;

      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: var(--shadow);

      transform-origin: bottom right;
      animation: rise 0.22s cubic-bezier(0.2, 0, 0.2, 1);
    }

    .panel[hidden] { display: none; }

    @keyframes rise {
      from { opacity: 0; transform: scale(0.94) translateY(8px); }
      to   { opacity: 1; transform: scale(1) translateY(0); }
    }

    @media (prefers-reduced-motion: reduce) {
      .panel { animation: none; }
      .launcher, .launch-text { transition: none; }
    }

    @media (max-width: 520px) {
      .panel {
        inset: 0;
        width: 100%;
        height: 100%;
        border-radius: 0;
        border: none;
      }
    }

    /* ------------------------------------------------------------ header */

    .head {
      display: flex;
      align-items: baseline;
      gap: 10px;
      padding: 17px 18px 14px;
      border-bottom: 1px solid var(--line);
      flex-shrink: 0;
    }

    .head .name {
      font-size: 15px;
      font-weight: 600;
      margin: 0;
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .head button {
      border: none;
      background: none;
      color: var(--ink-soft);
      cursor: pointer;
      padding: 4px;
      margin: -4px;
      border-radius: 6px;
      display: flex;
      align-self: center;
    }
    .head button:hover { color: var(--ink); }
    .head button:focus-visible { outline: 2px solid var(--accent); }

    /* --------------------------------------------------------------- log */

    .log {
      flex: 1;
      overflow-y: auto;
      overscroll-behavior: contain;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .turn { max-width: 88%; }
    .turn.you { align-self: flex-end; max-width: 80%; }

    .bubble {
      margin: 0;
      padding: 11px 14px;
      white-space: pre-wrap;
      word-wrap: break-word;
      overflow-wrap: anywhere;
    }

    .turn.them .bubble {
      background: var(--raised);
      border-radius: 3px 14px 14px 14px;
    }

    /* Later bubbles of one reply sit close under the first, like a run of
       messages from one person. */
    .turn.them.follow { margin-top: -10px; }
    .turn.them.follow .bubble { border-radius: 14px; }

    /* Confirms a contact request: set apart from the bot's own words. */
    .confirm {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      max-width: 88%;
      padding: 11px 14px;
      border: 1px solid var(--accent);
      border-radius: 12px;
      background: var(--paper);
      color: var(--ink);
      font-size: 13.5px;
      line-height: 1.45;
    }
    .confirm svg { flex-shrink: 0; color: var(--accent); margin-top: 1px; }

    .turn.you .bubble {
      background: var(--accent);
      color: var(--accent-ink);
      border-radius: 14px 3px 14px 14px;
    }

    /* ------------------------------------------------------- suggestions */

    .prompts {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 8px;
    }

    .prompts button {
      border: 1px solid var(--line);
      background: transparent;
      color: var(--ink);
      padding: 9px 15px;
      border-radius: 999px;
      font: inherit;
      font-size: 14px;
      text-align: left;
      cursor: pointer;
      transition: border-color 0.15s, color 0.15s;
    }
    .prompts button:hover { border-color: var(--accent); color: var(--accent); }
    .prompts button:focus-visible { outline: 2px solid var(--accent); }

    @media (prefers-reduced-motion: reduce) {
      .prompts button { transition: none; }
    }

    /* ---------------------------------------------------------- thinking */

    .waiting { display: flex; gap: 5px; padding: 15px 14px; }
    .waiting i {
      width: 5px; height: 5px;
      border-radius: 50%;
      background: var(--ink-soft);
      animation: breathe 1.4s ease-in-out infinite;
    }
    .waiting i:nth-child(2) { animation-delay: 0.18s; }
    .waiting i:nth-child(3) { animation-delay: 0.36s; }

    @keyframes breathe {
      0%, 100% { opacity: 0.22; }
      50% { opacity: 0.75; }
    }

    @media (prefers-reduced-motion: reduce) {
      .waiting i { animation: none; opacity: 0.45; }
    }

    /* ---------------------------------------------------------- feedback */

    /* Thumbs up/down under a reply: quiet until used. */
    .rate {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 2px;
      margin-top: -10px;
    }
    .rate-btn {
      border: none;
      background: transparent;
      color: var(--ink-soft);
      padding: 5px;
      border-radius: 6px;
      cursor: pointer;
      line-height: 0;
      opacity: 0.75;
    }
    .rate-btn:hover { opacity: 1; background: var(--raised); }
    .rate-btn:focus-visible { outline: 2px solid var(--accent); opacity: 1; }
    .rate-btn[aria-pressed="true"] { color: var(--accent); opacity: 1; }
    .rate-btn.down svg { transform: rotate(180deg); }
    .rate-reasons { display: flex; flex-wrap: wrap; gap: 6px; width: 100%; margin-top: 4px; }
    .rate-reasons button {
      border: 1px solid var(--line);
      background: transparent;
      color: var(--ink);
      padding: 5px 11px;
      border-radius: 999px;
      font: inherit;
      font-size: 12.5px;
      cursor: pointer;
    }
    .rate-reasons button:hover { border-color: var(--accent); color: var(--accent); }
    .rate-reasons button:focus-visible { outline: 2px solid var(--accent); }
    .rate-thanks { font-size: 12.5px; color: var(--ink-soft); margin-left: 6px; }

    /* ------------------------------------------------------------ notice */

    .notice {
      font-size: 13.5px;
      color: var(--ink-soft);
      padding: 11px 14px;
      background: var(--raised);
      border-radius: 10px;
    }

    /* ------------------------------------------------------------- input */

    .foot {
      border-top: 1px solid var(--line);
      padding: 13px 15px 11px;
      flex-shrink: 0;
    }

    .composer { display: flex; gap: 9px; align-items: flex-end; }

    textarea {
      flex: 1;
      resize: none;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      font: inherit;
      font-size: 15px;
      line-height: 1.5;
      max-height: 120px;
      color: var(--ink);
      background: var(--paper);
    }
    textarea::placeholder { color: var(--ink-soft); }
    textarea:focus { outline: none; border-color: var(--accent); }

    .send {
      border: none;
      background: var(--accent);
      color: var(--accent-ink);
      width: 40px;
      height: 40px;
      border-radius: 10px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .send:disabled { opacity: 0.35; cursor: default; }
    .send:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }

    /* Always within reach once the chat has started, so a visitor who
       skims the replies still sees how to reach a person. */
    .handoff {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 100%;
      margin: 0 0 10px;
      border: none;
      background: var(--accent);
      color: var(--accent-ink);
      font: inherit;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      padding: 10px 16px;
      border-radius: 999px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .handoff:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22);
    }
    .handoff:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
    .handoff[hidden] { display: none; }
    /* A short pulse when it first appears, so the eye goes to it once. */
    .handoff.arrive { animation: handoff-pulse 1.2s ease-out 2; }
    @keyframes handoff-pulse {
      0% { box-shadow: 0 0 0 0 var(--accent); }
      70% { box-shadow: 0 0 0 10px transparent; }
      100% { box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18); }
    }
    @media (prefers-reduced-motion: reduce) {
      .handoff, .handoff.arrive { animation: none; transition: none; }
      .handoff:hover { transform: none; }
    }

    .again {
      display: block;
      margin: 9px auto 0;
      border: none;
      background: none;
      color: var(--ink-soft);
      font: inherit;
      font-size: 12.5px;
      cursor: pointer;
      padding: 2px 6px;
      border-radius: 4px;
    }
    .again:hover { color: var(--ink); }
    .again:focus-visible { outline: 2px solid var(--accent); }

    .disclosure {
      margin: 6px 0 0;
      text-align: center;
      font-size: 11.5px;
      line-height: 1.4;
      color: var(--ink-soft);
    }
    .disclosure a { color: inherit; text-decoration: underline; }
    .disclosure a:hover { color: var(--ink); }
  `;

  // ---------------------------------------------------------------- markup

  var host = document.createElement("div");
  host.setAttribute("data-sitechat", "");
  var shadow = host.attachShadow({ mode: "open" });

  var style = document.createElement("style");
  style.textContent = CSS;
  shadow.appendChild(style);

  var root = document.createElement("div");
  root.className = "root";
  shadow.appendChild(root);

  // Built with DOM calls rather than innerHTML, so the habit holds
  // everywhere and there is no template that someone later interpolates into.

  var launcher = el("button", "launcher");
  launcher.type = "button";
  launcher.appendChild(icon(
    "M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.9-.9L3 20l1.3-3.8A8.2 " +
    "8.2 0 0 1 3 11.5a8.4 8.4 0 0 1 9-8.4 8.4 8.4 0 0 1 9 8.4z", 17
  ));
  var LAUNCHER_TEXT = "Chat with us";
  var launchText = el("span", "launch-text");
  launchText.textContent = LAUNCHER_TEXT;
  launcher.appendChild(launchText);

  var panel = el("div", "panel");
  panel.hidden = true;
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Chat");

  var head = el("div", "head");
  var name = el("h2", "name");
  name.textContent = "Chat";
  var closeButton = el("button");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Close chat");
  closeButton.appendChild(icon("M18 6 6 18M6 6l12 12", 17));
  head.appendChild(name);
  head.appendChild(closeButton);

  var log = el("div", "log");
  log.setAttribute("role", "log");
  log.setAttribute("aria-live", "polite");

  var foot = el("div", "foot");
  var composer = el("div", "composer");
  var input = document.createElement("textarea");
  input.rows = 1;
  input.placeholder = "Ask a question";
  input.setAttribute("aria-label", "Your message");
  var send = el("button", "send");
  send.type = "button";
  send.setAttribute("aria-label", "Send");
  send.appendChild(icon("M5 12h14M13 6l6 6-6 6", 17));
  composer.appendChild(input);
  composer.appendChild(send);

  var handoff = el("button", "handoff");
  handoff.type = "button";
  // A person-with-speech icon, so it reads as "a human" at a glance.
  handoff.appendChild(icon("M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8", 17));
  handoff.appendChild(document.createTextNode("Talk to the team"));
  handoff.hidden = true;

  var again = el("button", "again");
  again.type = "button";
  again.textContent = "Start a new chat";

  // Visitors must be told they are talking to an AI, and where to read how
  // their details are used. The privacy link is added once /config says
  // where the customer's notice is.
  var disclosure = el("p", "disclosure");
  disclosure.textContent = "AI assistant · Answers can be wrong";

  foot.appendChild(handoff);
  foot.appendChild(composer);
  foot.appendChild(again);
  foot.appendChild(disclosure);

  panel.appendChild(head);
  panel.appendChild(log);
  panel.appendChild(foot);

  root.appendChild(launcher);
  root.appendChild(panel);

  function el(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function icon(path, size) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(ns, "path");
    p.setAttribute("d", path);
    svg.appendChild(p);
    return svg;
  }

  // ------------------------------------------------------- conversation id

  function loadConversationId() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var saved = JSON.parse(raw);
      if (Date.now() - saved.at > MAX_AGE_MS) {
        localStorage.removeItem(STORAGE_KEY);
        return null;
      }
      return saved.id;
    } catch (e) {
      // Private browsing or storage disabled. The chat still works, it just
      // will not resume on a later visit.
      return null;
    }
  }

  function saveConversationId(id) {
    conversationId = id;
    try {
      localStorage.setItem(
        STORAGE_KEY, JSON.stringify({ id: id, at: Date.now() })
      );
    } catch (e) {}
  }

  // --------------------------------------------------------------- render

  // Replies may use Markdown bold ("**Ravi Nagar**"). It is rendered as a
  // <strong> built with DOM calls, never innerHTML, so model text can
  // never become markup. Heading markers ("### ") at the start of a line
  // are dropped; everything else stays plain text (lists read fine as
  // they are, since the bubble keeps line breaks).
  function renderReply(bubble, text) {
    text = String(text).replace(/^#{1,6}[ \t]+/gm, "");
    var parts = text.split(/\*\*([^*\n]+)\*\*/);
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) continue;
      if (i % 2 === 1) {
        var strong = document.createElement("strong");
        strong.textContent = parts[i];
        bubble.appendChild(strong);
      } else {
        bubble.appendChild(document.createTextNode(parts[i].replace(/\*\*/g, "")));
      }
    }
  }

  // ---------------------------------------------------------- reply bubbles

  // A reply is shown as a run of bubbles, one per paragraph, the way a
  // person sends several short messages. Only the display changes: the
  // reply is stored and emailed as one message.
  var MAX_BUBBLES = 4;
  var queued = [];
  var queueTimer = null;

  function isListItem(line) {
    return /^\s*([-*•]|\d+[.)])\s/.test(line);
  }

  function splitReply(text) {
    var bubbles = [];
    String(text).split(/\n[ \t]*\n/).forEach(function (block) {
      block = block.trim();
      if (!block) return;
      var previous = bubbles[bubbles.length - 1];
      var lastLine = previous ? previous.split("\n").pop() : "";
      // A list stays together, and with the line that introduces it.
      if (previous && isListItem(block) &&
          (/:\s*$/.test(lastLine) || isListItem(lastLine))) {
        bubbles[bubbles.length - 1] = previous + "\n" + block;
      } else {
        bubbles.push(block);
      }
    });
    while (bubbles.length > MAX_BUBBLES) {
      var last = bubbles.pop();
      bubbles[bubbles.length - 1] += "\n\n" + last;
    }
    return bubbles.length ? bubbles : [String(text)];
  }

  // Roughly the time it takes to type a short message, kept brief.
  function gapBefore(text) {
    return Math.min(1500, 500 + text.length * 8);
  }

  function reducedMotion() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // Queued items are bubble text, {rate: id} for the thumbs under the
  // reply's last bubble, or {confirm: text} for the card that follows a
  // reply which placed a contact request.
  function showReply(text, confirmation, replyId) {
    showQueued();
    var bubbles = splitReply(text);
    addTurn("them", bubbles[0]);
    queued = bubbles.slice(1);
    if (replyId) queued.push({ rate: replyId });
    if (confirmation) queued.push({ confirm: confirmation });
    if (reducedMotion()) showQueued();
    else showNext();
  }

  function showItem(item) {
    if (typeof item === "string") addTurn("them", item, true);
    else if (item.rate) addRating(item.rate);
    else addConfirmation(item.confirm);
  }

  function showNext() {
    if (!queued.length) return;
    var item = queued[0];
    // The card is not typed, so no typing dots before it.
    if (typeof item === "string") addWaiting();
    queueTimer = setTimeout(function () {
      queueTimer = null;
      clearWaiting();
      showItem(queued.shift());
      showNext();
    }, typeof item === "string" ? gapBefore(item) : item.rate ? 0 : 400);
  }

  // Thumbs up/down for one reply. A tap is sent at once and can be
  // changed; after a thumbs down, an optional reason. Sending is best
  // effort: a lost rating is not worth troubling the visitor about.
  var THUMB = "M7 10v12M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 " +
    "17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 " +
    "2a3.13 3.13 0 0 1 3 3.88Z";
  var REASONS = [
    ["wrong", "Wrong information"],
    ["not_answered", "Didn't answer my question"],
    ["other", "Other"],
  ];

  function sendRating(replyId, rating, reason) {
    if (!conversationId) return;
    var body = { conversation_id: conversationId, reply_id: replyId, rating: rating };
    if (reason) body.reason = reason;
    fetch(API + "/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Site-Key": siteKey },
      body: JSON.stringify(body),
    }).catch(function () {});
  }

  function addRating(replyId) {
    var row = el("div", "rate");
    var buttons = {};
    var extra = null; // the reasons, or the thanks

    function setExtra(node) {
      if (extra) extra.remove();
      extra = node;
      if (node) row.appendChild(node);
      toEnd();
    }

    function thanks() {
      var note = el("span", "rate-thanks");
      note.textContent = "Thanks for the feedback.";
      setExtra(note);
    }

    function choose(rating) {
      buttons.up.setAttribute("aria-pressed", String(rating === "up"));
      buttons.down.setAttribute("aria-pressed", String(rating === "down"));
      sendRating(replyId, rating);
      if (rating === "up") return thanks();
      var reasons = el("div", "rate-reasons");
      REASONS.forEach(function (pair) {
        var chip = el("button");
        chip.type = "button";
        chip.textContent = pair[1];
        chip.addEventListener("click", function () {
          sendRating(replyId, "down", pair[0]);
          thanks();
        });
        reasons.appendChild(chip);
      });
      setExtra(reasons);
    }

    [["up", "Helpful"], ["down", "Not helpful"]].forEach(function (pair) {
      var button = el("button", "rate-btn " + pair[0]);
      button.type = "button";
      button.setAttribute("aria-label", pair[1]);
      button.setAttribute("aria-pressed", "false");
      button.appendChild(icon(THUMB, 15));
      button.addEventListener("click", function () { choose(pair[0]); });
      buttons[pair[0]] = button;
      row.appendChild(button);
    });
    log.appendChild(row);
    toEnd();
  }

  function addConfirmation(text) {
    var card = el("div", "confirm");
    card.setAttribute("role", "status");
    card.appendChild(icon("M20 6 9 17l-5-5", 16));
    var words = el("span");
    words.textContent = text;
    card.appendChild(words);
    log.appendChild(card);
    toEnd();
  }

  // Everything still waiting appears at once: when the visitor starts
  // typing or sends, they should not wait on the animation.
  function showQueued() {
    if (queueTimer) {
      clearTimeout(queueTimer);
      queueTimer = null;
      clearWaiting();
    }
    while (queued.length) showItem(queued.shift());
  }

  function dropQueued() {
    if (queueTimer) clearTimeout(queueTimer);
    queueTimer = null;
    queued = [];
  }

  function addTurn(who, text, follow) {
    var turn = el("div", "turn " + who + (follow ? " follow" : ""));
    var bubble = el("p", "bubble");
    if (who === "them") {
      renderReply(bubble, text);
    } else {
      bubble.textContent = text;
    }
    turn.appendChild(bubble);
    log.appendChild(turn);
    toEnd();
    return turn;
  }

  function addWaiting() {
    var turn = el("div", "turn them");
    turn.dataset.waiting = "1";
    var dots = el("div", "waiting");
    dots.appendChild(el("i"));
    dots.appendChild(el("i"));
    dots.appendChild(el("i"));
    turn.appendChild(dots);
    log.appendChild(turn);
    toEnd();
  }

  function clearWaiting() {
    var node = log.querySelector('[data-waiting="1"]');
    if (node) node.remove();
  }

  function addNotice(text) {
    var notice = el("div", "notice");
    notice.textContent = text;
    log.appendChild(notice);
    toEnd();
  }

  function addPrompts(questions) {
    if (!questions || !questions.length) return;

    var box = el("div", "prompts");
    questions.slice(0, 4).forEach(function (q) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = q;
      button.addEventListener("click", function () {
        box.remove();
        ask(q);
      });
      box.appendChild(button);
    });
    log.appendChild(box);
    toEnd();
  }

  function toEnd() {
    log.scrollTop = log.scrollHeight;
  }

  // Keep the newest message in view when the chat area changes height,
  // e.g. when the Talk to the team button appears below it or the input
  // grows. Not if the visitor has scrolled up to reread something.
  // Whether it was at the end is judged by the height before the change:
  // by the time the observer runs, the area has already shrunk.
  var lastLogHeight = 0;
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(function () {
      var wasAtEnd =
        log.scrollHeight - log.scrollTop - (lastLogHeight || log.clientHeight) < 24;
      lastLogHeight = log.clientHeight;
      if (wasAtEnd) toEnd();
    }).observe(log);
  }

  function disable(message) {
    addNotice(message);
    handoff.hidden = true;
    input.disabled = true;
    send.disabled = true;
  }

  // -------------------------------------------------------------- network

  function loadConfig() {
    return fetch(API + "/config", { headers: { "X-Site-Key": siteKey } })
      .then(function (r) {
        if (!r.ok) {
          explainRefusal(r.status);
          throw new Error(String(r.status));
        }
        return r.json();
      })
      .then(function (data) {
        config = data;
        name.textContent = data.display_name;
        launcher.setAttribute("aria-label", "Chat with " + data.display_name);
        if (data.accent_colour) {
          root.style.setProperty("--accent", data.accent_colour);
        }
        addPrivacyLink(data.privacy_url);
        addTurn("them", data.greeting);
        addPrompts(data.suggested_questions);
        startTeaser(data.suggested_questions);
      })
      .catch(function () {
        disable("This chat is not available right now. Please try again later.");
      });
  }

  // Visitors only see "not available". The site owner installing the
  // widget needs the reason, so it goes to the browser console.
  function explainRefusal(status) {
    if (status === 401) {
      console.error(
        "[sitechat] The site key in the script tag is not recognised. " +
        "Check the data-site-key attribute."
      );
    } else if (status === 403) {
      console.error(
        "[sitechat] This site key is not allowed on " + location.hostname +
        ", or the chat is switched off. Ask for " + location.hostname +
        " to be added to the allowed domains."
      );
    }
  }

  function addPrivacyLink(url) {
    if (!url || !/^https?:\/\//i.test(url)) return;
    var link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Privacy";
    disclosure.appendChild(document.createTextNode(" · "));
    disclosure.appendChild(link);
  }

  var emailNoticeShown = false;

  // Shown once, when the visitor first types something that looks like an
  // email address, so they know where it goes before relying on it.
  function noteEmailUse(text) {
    if (emailNoticeShown || !/\S+@\S+\.\S+/.test(text)) return;
    emailNoticeShown = true;
    var business = config ? config.display_name : "the business";
    addNotice(
      "Your email goes only to " + business + "'s team, so they can " +
      "reply to you."
    );
  }

  function ask(text) {
    text = (text || "").trim();
    if (!text || busy) return;

    showQueued();

    busy = true;
    send.disabled = true;
    addTurn("you", text);
    noteEmailUse(text);
    addWaiting();

    var body = { message: text };
    if (conversationId) body.conversation_id = conversationId;

    fetch(API + "/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Site-Key": siteKey,
      },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        if (r.status === 429) throw new Error("rate");
        if (r.status === 503) {
          // The server's 503 messages are written for visitors ("try
          // again", "taking a break until tomorrow"); show them as text.
          return r.json().then(function (data) {
            var error = new Error("503");
            if (data && typeof data.detail === "string") error.detail = data.detail;
            throw error;
          }, function () { throw new Error("503"); });
        }
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then(function (data) {
        clearWaiting();
        saveConversationId(data.conversation_id);
        showReply(data.reply, data.confirmation, data.reply_id);
        if (data.confirmation) {
          // The team has the request, however it was made; the button has
          // nothing left to do in this chat.
          handoffUsed = true;
          handoff.hidden = true;
        }
        if (!handoffUsed && handoff.hidden) {
          handoff.hidden = false;
          handoff.classList.add("arrive");
          toEnd(); // for browsers without ResizeObserver
        }
      })
      .catch(function (err) {
        clearWaiting();
        if (err.message === "rate") {
          addNotice("That is a lot of questions at once. Wait a moment, then try again.");
        } else if (err.detail) {
          addNotice(err.detail);
        } else {
          addNotice("That message did not get through. Try again in a moment.");
        }
      })
      .finally(function () {
        busy = false;
        send.disabled = false;
        input.focus();
      });
  }

  // ------------------------------------------------------------- teaser

  // The launcher shows "Chat with us", then after a few seconds widens to
  // show a few of the business's suggested questions, one after another,
  // and settles back. It rests, then repeats. Pointing at it (or keyboard
  // focus on it) holds it still. Clicking it opens the chat (asking the
  // question on show, if any); it pauses while the chat is open and starts
  // again when it is closed. Visitors who ask for reduced motion never
  // see it, nor does a site whose script tag has data-teaser="off".
  var TEASER_FIRST_MS = 3000;
  var TEASER_STEP_MS = 2000;
  var TEASER_PER_ROUND = 3;
  var TEASER_REST_MS = 30 * 1000;
  var TEASER_MAX_CHARS = 32;

  var teaserTimer = null;
  var teaserHeld = false;
  var teaserWaiting = null; // the step that came due while held
  var teaserQuestion = null; // the question on show, if any
  var teaserRestart = null; // starts the rounds again, once there are any
  var teaserGeneration = 0; // bumped by resetTeaser, to drop stale fades

  function startTeaser(questions) {
    if (!teaserAllowed || reducedMotion()) return;
    var short = (questions || []).filter(function (q) {
      return q && q.length <= TEASER_MAX_CHARS && fitsLauncher(q);
    }).slice(0, TEASER_PER_ROUND);
    if (!short.length) return;

    function round() {
      var steps = short.map(function (q) {
        return function () { showLauncherText(q, q); };
      });
      steps.push(function () { showLauncherText(LAUNCHER_TEXT, null); });

      var i = 0;
      function next() {
        steps[i++]();
        if (i < steps.length) later(next, TEASER_STEP_MS);
        else later(round, TEASER_REST_MS);
      }
      next();
    }
    teaserRestart = function () { later(round, TEASER_FIRST_MS); };
    teaserRestart();
  }

  // Whether the question shows in full at the launcher's widest; one that
  // would be cut off with "..." is skipped rather than shown broken.
  function fitsLauncher(text) {
    var before = launchText.textContent;
    launchText.textContent = text;
    launcher.style.width = "auto";
    var fits = launchText.scrollWidth <= launchText.clientWidth;
    launchText.textContent = before;
    launcher.style.width = "";
    return fits;
  }

  // One chain of steps at a time: scheduling replaces anything pending.
  function later(step, ms) {
    if (teaserTimer) clearTimeout(teaserTimer);
    teaserTimer = setTimeout(function () {
      teaserTimer = null;
      if (teaserHeld || isOpen) teaserWaiting = step;
      else step();
    }, ms);
  }

  // Fade the text out, swap it, and animate the width to fit the new text.
  function showLauncherText(text, question) {
    teaserQuestion = question;
    var from = launcher.offsetWidth;
    var generation = teaserGeneration;
    launchText.classList.add("fading");
    setTimeout(function () {
      if (generation !== teaserGeneration) return;
      launchText.textContent = text;
      launcher.setAttribute(
        "aria-label", question ? "Ask: " + question : launcherLabel()
      );
      launcher.style.width = "auto";
      var to = launcher.offsetWidth;
      launcher.style.width = from + "px";
      launcher.offsetWidth; // apply the start width before animating
      launcher.style.width = to + "px";
      launchText.classList.remove("fading");
    }, 150);
  }

  function launcherLabel() {
    return config ? "Chat with " + config.display_name : LAUNCHER_TEXT;
  }

  function holdTeaser() { teaserHeld = true; }

  // Keyboard focus only: closing the chat focuses the launcher, and that
  // should not freeze it for someone using a mouse.
  function holdTeaserOnFocus() {
    var keyboard = true;
    try { keyboard = launcher.matches(":focus-visible"); } catch (e) {}
    if (keyboard) holdTeaser();
  }

  function releaseTeaser() {
    teaserHeld = false;
    if (teaserWaiting) {
      var step = teaserWaiting;
      teaserWaiting = null;
      later(step, TEASER_STEP_MS);
    }
  }

  // Back to "Chat with us" with nothing pending, for when the chat opens.
  function resetTeaser() {
    teaserGeneration++;
    if (teaserTimer) clearTimeout(teaserTimer);
    teaserTimer = null;
    teaserWaiting = null;
    teaserQuestion = null;
    teaserHeld = false;
    launchText.classList.remove("fading");
    launchText.textContent = LAUNCHER_TEXT;
    launcher.style.width = "";
    launcher.setAttribute("aria-label", launcherLabel());
  }

  launcher.addEventListener("mouseenter", holdTeaser);
  launcher.addEventListener("mouseleave", releaseTeaser);
  launcher.addEventListener("focus", holdTeaserOnFocus);
  launcher.addEventListener("blur", releaseTeaser);

  // --------------------------------------------------------------- events

  function open() {
    isOpen = true;
    panel.hidden = false;
    launcher.hidden = true;
    input.focus();
  }

  function close() {
    isOpen = false;
    panel.hidden = true;
    launcher.hidden = false;
    launcher.focus();
    if (teaserRestart) teaserRestart();
  }

  // Sends a request the bot handles like any other: it records it for
  // the team and asks for an email address if it has none.
  var handoffUsed = false;
  handoff.addEventListener("click", function () {
    if (busy) return;
    handoffUsed = true;
    handoff.hidden = true;
    ask("I'd like someone from the team to contact me.");
  });

  launcher.addEventListener("click", function () {
    var question = teaserQuestion;
    resetTeaser();
    open();
    if (question && config && !busy) {
      var prompts = log.querySelector(".prompts");
      if (prompts) prompts.remove();
      ask(question);
    }
  });
  closeButton.addEventListener("click", close);

  send.addEventListener("click", function () {
    var text = input.value;
    input.value = "";
    input.style.height = "auto";
    ask(text);
  });

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send.click();
    }
  });

  input.addEventListener("input", function () {
    showQueued();
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  });

  again.addEventListener("click", function () {
    dropQueued();
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    conversationId = null;
    emailNoticeShown = false;
    handoffUsed = false;
    handoff.hidden = true;
    log.textContent = "";
    input.disabled = false;
    send.disabled = false;
    if (config) {
      addTurn("them", config.greeting);
      addPrompts(config.suggested_questions);
    } else {
      loadConfig();
    }
  });

  // Escape closes the panel, which people expect of anything dialog-shaped.
  shadow.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && isOpen) close();
  });

  // ----------------------------------------------------------------- mount

  function mount() {
    document.body.appendChild(host);
    loadConfig();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();