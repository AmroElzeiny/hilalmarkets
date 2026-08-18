/* The help page at /dashboard/support.
 *
 * One form, sent without leaving the page, with the new message appearing in the
 * person's own list the moment it is accepted. The live page reloaded the whole
 * dashboard half a second after sending, which threw away whatever the person could
 * still see and gave them no moment to read the result.
 *
 * Nothing here decides anything about a request. The server owns the category, the
 * priority and where the message goes.
 */

import { animate, attention, prefersReducedMotion, settleIn } from "./hm-motion.js";

const root = document.querySelector("[data-support-root]");
if (root) start(root);

/** What we accept, said once, and checked against the same list the server uses. */
const PICTURE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const PICTURE_LIMIT = 3;
const PICTURE_BYTES = 5 * 1024 * 1024;

function start(scope) {
  const form = scope.querySelector("[data-h-form]");
  if (!form) return;

  const said = scope.querySelector("[data-h-said]");
  const send = scope.querySelector("[data-h-send]");
  const sendLabel = scope.querySelector("[data-h-send-label]");
  const description = scope.querySelector("[data-h-description]");
  const email = scope.querySelector("[data-h-email]");
  const counter = scope.querySelector("[data-h-count]");
  const filesInput = scope.querySelector("[data-h-files]");
  const fileList = scope.querySelector("[data-h-file-list]");
  const drop = scope.querySelector("[data-h-drop]");
  const tickets = scope.querySelector("[data-h-tickets]");
  const empty = scope.querySelector("[data-h-empty]");

  /** The pictures chosen so far. Held here rather than read back off the file input,
      because a file input cannot have one of its files taken out of it. */
  let pictures = [];

  function paint(element) {
    if (!element || typeof window.icon !== "function") return;
    element.querySelectorAll("[data-icon]").forEach((node) => {
      node.innerHTML = window.icon(node.dataset.icon, node.dataset.iconClass || "icon");
    });
  }

  /** Say one thing, once, where it is both seen and heard.
   *
   * Written straight in rather than on the next frame: the line is hidden while empty,
   * and a hidden element cannot take the keyboard, so a message set a frame late could
   * never be focused. Repeating the same sentence still clears first, because that is
   * the one case a screen reader would otherwise say nothing about. */
  function say(text, tone = "") {
    if (!said) return;
    said.dataset.tone = tone;
    if (said.textContent === text) {
      said.textContent = "";
      window.requestAnimationFrame(() => { said.textContent = text; });
      return;
    }
    said.textContent = text;
  }

  function markField(name, wrong) {
    const field = scope.querySelector(`[data-h-field="${name}"]`);
    if (field) field.dataset.wrong = wrong ? "true" : "false";
  }

  /* ── How much you have written ───────────────────────────────────────────── */

  function drawCount() {
    if (!counter || !description) return;
    counter.textContent = `${description.value.length} / 5000`;
  }
  description?.addEventListener("input", () => {
    drawCount();
    if (description.value.trim()) markField("description", false);
  });
  email?.addEventListener("input", () => {
    if (email.value.trim()) markField("email", false);
  });
  drawCount();

  /* ── Choosing a topic changes the hint, not the layout ───────────────────── */

  const hint = scope.querySelector("[data-h-hint]");
  const HINTS = {
    missing_alert: "Which coin, which Watchlist, and roughly when you expected to hear.",
    billing: "The date, the amount, and what you expected to happen.",
    bug_report: "Which page, what you pressed, and what happened instead.",
    screening: "Which coin, and what you were reading when the question came up.",
    general: "Tell us what you saw, when, and what you expected instead.",
  };
  for (const input of scope.querySelectorAll("[data-h-topic-input]")) {
    input.addEventListener("change", () => {
      if (!hint || !input.checked) return;
      hint.textContent = HINTS[input.value] || HINTS.general;
    });
  }

  /* ── Pictures ────────────────────────────────────────────────────────────── */

  function humanSize(bytes) {
    return bytes >= 1024 * 1024
      ? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
      : `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  /** One preview address per picture, made once and kept until it is let go.
   *
   * The list is redrawn whenever a picture is added or taken out, so making a fresh
   * address every draw would leak one each time — and letting go of it as soon as the
   * image loads can leave the picture blank the next time the row is drawn. Held here
   * instead, and released when the picture itself goes. */
  const previews = new Map();

  function previewUrl(file) {
    if (!previews.has(file)) previews.set(file, URL.createObjectURL(file));
    return previews.get(file);
  }

  function forgetPreview(file) {
    const url = previews.get(file);
    if (!url) return;
    URL.revokeObjectURL(url);
    previews.delete(file);
  }

  function drawPictures() {
    if (!fileList) return;
    fileList.textContent = "";
    pictures.forEach((file, index) => {
      const row = document.createElement("li");
      row.className = "h-file";
      const preview = document.createElement("img");
      preview.alt = "";
      preview.src = previewUrl(file);
      const copy = document.createElement("span");
      copy.className = "h-file-copy";
      const name = document.createElement("strong");
      name.textContent = file.name;
      const size = document.createElement("small");
      size.textContent = humanSize(file.size);
      copy.append(name, size);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "t-icon-btn";
      remove.setAttribute("aria-label", `Take out ${file.name}`);
      const glyph = document.createElement("span");
      glyph.dataset.icon = "trash";
      glyph.dataset.iconClass = "icon-sm";
      remove.append(glyph);
      remove.addEventListener("click", () => {
        pictures.splice(index, 1);
        forgetPreview(file);
        drawPictures();
        say(`${file.name} taken out.`);
      });
      row.append(preview, copy, remove);
      fileList.append(row);
      paint(row);
      if (!prefersReducedMotion()) animate(row, { opacity: [0, 1] }, { duration: 0.18 });
    });
  }

  /** Take some files, keeping only the ones we can really send, and say why not. */
  function takeFiles(list) {
    const refused = [];
    for (const file of list) {
      if (pictures.length >= PICTURE_LIMIT) {
        refused.push(`${file.name} — you can send ${PICTURE_LIMIT} pictures at a time`);
        continue;
      }
      if (!PICTURE_TYPES.includes(file.type)) {
        refused.push(`${file.name} — only PNG, JPEG and WebP`);
        continue;
      }
      if (file.size > PICTURE_BYTES) {
        refused.push(`${file.name} — bigger than 5 MB`);
        continue;
      }
      pictures.push(file);
    }
    drawPictures();
    if (refused.length) say(`Not added: ${refused.join("; ")}.`, "danger");
    else if (pictures.length) say(`${pictures.length} picture${pictures.length === 1 ? "" : "s"} ready to send.`);
  }

  filesInput?.addEventListener("change", () => {
    takeFiles([...(filesInput.files || [])]);
    // Cleared so choosing the same file twice in a row still counts as a change.
    filesInput.value = "";
  });

  for (const name of ["dragenter", "dragover"]) {
    drop?.addEventListener(name, (event) => {
      event.preventDefault();
      drop.dataset.over = "true";
    });
  }
  for (const name of ["dragleave", "drop"]) {
    drop?.addEventListener(name, (event) => {
      event.preventDefault();
      delete drop.dataset.over;
    });
  }
  drop?.addEventListener("drop", (event) => {
    takeFiles([...(event.dataTransfer?.files || [])]);
  });

  /* ── Sending ─────────────────────────────────────────────────────────────── */

  /** One picture, as the shape the endpoint takes. */
  async function asPayload(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    const chunk = 32768;
    for (let index = 0; index < bytes.length; index += chunk) {
      binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
    }
    return {
      filename: file.name,
      content_type: file.type,
      data_base64: window.btoa(binary),
    };
  }

  /** Put a just-sent message at the top of the person's own list, straight away. */
  function addToYours(topicWords, stateWords, body) {
    if (!tickets) return;
    const item = document.createElement("li");
    item.className = "h-ticket";
    item.dataset.hTicket = "";

    const top = document.createElement("div");
    top.className = "h-ticket-top";
    const title = document.createElement("strong");
    title.textContent = topicWords;
    const state = document.createElement("p");
    state.className = "a-state";
    state.dataset.tone = "warning";
    const stateIcon = document.createElement("span");
    stateIcon.dataset.icon = "clock";
    stateIcon.dataset.iconClass = "icon-sm";
    state.append(stateIcon, document.createTextNode(stateWords));
    top.append(title, state);

    const meta = document.createElement("p");
    meta.className = "h-ticket-meta";
    const when = document.createElement("span");
    when.textContent = "Just now";
    const meaning = document.createElement("span");
    meaning.textContent = "We have it. Nobody has replied yet.";
    meta.append(when, meaning);

    const more = document.createElement("details");
    more.className = "t-more";
    const summary = document.createElement("summary");
    const chevron = document.createElement("span");
    chevron.dataset.icon = "chevron";
    chevron.dataset.iconClass = "icon-sm";
    summary.append(chevron, document.createTextNode("What you wrote"));
    const inner = document.createElement("div");
    inner.className = "t-more-body";
    const words = document.createElement("p");
    words.className = "h-ticket-said";
    // Written in as text, never as markup: this is the person's own typing.
    words.textContent = body;
    inner.append(words);
    more.append(summary, inner);

    item.append(top, meta, more);
    tickets.prepend(item);
    paint(item);
    if (empty) empty.hidden = true;
    if (!prefersReducedMotion()) {
      animate(item, { opacity: [0, 1], transform: ["translateY(-8px)", "translateY(0px)"] }, { duration: 0.28 });
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (send?.dataset.busy === "true") return;

    const topic = scope.querySelector("[data-h-topic-input]:checked");
    const body = (description?.value || "").trim();
    const replyTo = (email?.value || "").trim();

    // Marked one by one so the person is told which box needs them, not that "the form"
    // is wrong.
    markField("description", body.length < 3);
    markField("email", !replyTo || !replyTo.includes("@"));
    if (body.length < 3) {
      say("Please tell us what happened first.", "danger");
      description?.focus();
      return;
    }
    if (!replyTo || !replyTo.includes("@")) {
      say("Please write an email address we can reply to.", "danger");
      email?.focus();
      return;
    }

    send.dataset.busy = "true";
    send.disabled = true;
    if (sendLabel) sendLabel.textContent = "Sending…";
    say("Sending your message.");

    try {
      const screenshots = await Promise.all(pictures.map(asPayload));
      const response = await window.fetch("/api/v1/dashboard/support/tickets", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": document.body.dataset.csrfToken || "",
        },
        credentials: "same-origin",
        body: JSON.stringify({
          category: topic?.value || "general",
          email: replyTo,
          subject: topic?.dataset.subject || "Something else",
          description: body,
          context: { source: "dashboard" },
          screenshots,
        }),
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        // The server sends a plain string for some refusals and, for the ones a person
        // can act on, an object holding a code and a sentence. Reading only the string
        // form threw away the useful half: somebody who had reached the message limit
        // was told "we could not send it", with no reason and no time to come back.
        const detail = failure.detail;
        throw new Error(
          typeof detail === "string"
            ? detail
            : (detail && typeof detail.message === "string" && detail.message) ||
              "We could not send it. Nothing was lost — please try again.",
        );
      }
      const sent = await response.json().catch(() => ({}));
      addToYours(topic?.dataset.subject || "Something else", "Waiting for us", body);
      // How many are left, said now rather than discovered by writing another one.
      const left = Number(sent.remaining_requests);
      say(
        Number.isFinite(left)
          ? `Sent. We will reply to ${replyTo}. ${
              left > 0
                ? `You can send ${left} more message${left === 1 ? "" : "s"} this hour.`
                : "That was your last message for this hour. To add anything, reply to our email."
            }`
          : `Sent. We will reply to ${replyTo}.`,
        "success",
      );
      if (description) description.value = "";
      pictures.forEach(forgetPreview);
      pictures = [];
      drawPictures();
      drawCount();
    } catch (error) {
      say(
        error instanceof Error
          ? error.message
          : "We could not send it. Nothing was lost — please try again.",
        "danger",
      );
      if (send) attention(send);
    } finally {
      delete send.dataset.busy;
      send.disabled = false;
      if (sendLabel) sendLabel.textContent = "Send it";
      said?.focus({ preventScroll: true });
    }
  });

  /* ── Arriving ────────────────────────────────────────────────────────────── */

  settleIn(scope.querySelectorAll("[data-h-help]"), { from: 10 });
  settleIn(scope.querySelectorAll("[data-h-ticket]"), { from: 6, delayStep: 0.02 });
}
