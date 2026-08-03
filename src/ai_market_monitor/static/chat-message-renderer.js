(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function render(item, options = {}) {
    const user = item.role === "user";
    const clean = options.clean || escapeHtml;
    const transformAssistant = options.transformAssistant || ((value) => value);
    const variant = options.variant || "setup";
    const wrapper = document.createElement("article");
    wrapper.className = variant === "admin"
      ? `brain-assistant-message ${user ? "user" : "assistant"}`
      : `ai-chat-message ${user ? "user" : "assistant"}${item.pending ? " pending" : ""}${item.failed ? " failed" : ""}`;
    if (!user) wrapper.dataset.testid = "shared-assistant-message";
    if (item.id || item.message_id) wrapper.dataset.messageId = item.id || item.message_id;
    if (item.client_message_id) wrapper.dataset.clientMessageId = item.client_message_id;

    const payload = item.payload || {};
    const text = clean(user ? item.content : transformAssistant(item.content));
    const timestamp = item.created_at
      ? new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(item.created_at))
      : "";
    if (variant === "admin") {
      wrapper.innerHTML = `<p>${text}</p>${timestamp ? `<small>${clean(timestamp)}</small>` : ""}`;
      return wrapper;
    }

    const understanding = !user && payload.understanding_summary
      ? `<section class="ai-understanding-card"><span>What I understood</span><p>${clean(transformAssistant(payload.understanding_summary))}</p></section>`
      : "";
    const jargon = !user && Array.isArray(payload.jargon) && payload.jargon.length
      ? `<section class="ai-jargon-card">${payload.jargon.map((entry) => `<p><strong>${clean(entry.term)}</strong> ${clean(transformAssistant(entry.explanation))}</p>`).join("")}</section>`
      : "";
    const snapshot = !user && item.message_type === "market_snapshot" && payload.status === "available"
      ? `<section class="ai-snapshot-card"><strong>${clean(payload.provider_name)} · ${clean(payload.symbols_checked)} symbols</strong><span>BTC ${clean(payload.btc_status?.percentage_24h ?? "n/a")}% · ETH ${clean(payload.eth_status?.percentage_24h ?? "n/a")}%</span><span>${clean(payload.advancing)} advancing · ${clean(payload.declining)} declining · ${clean(payload.unchanged)} unchanged</span><span>${clean(payload.volatility_label)} dispersion · average ${clean(payload.average_change_24h)}%</span></section>`
      : "";
    const scannerResult = payload.scanner_result;
    const scannerRows = Array.isArray(scannerResult?.results) ? scannerResult.results.slice(0, 5) : [];
    const scanner = !user && item.message_type === "scanner_result" && scannerResult
      ? `<section class="ai-scanner-result-card"><strong>Scanner result</strong><span>${clean(scannerResult.symbols_scanned)} symbols scanned · ${clean(scannerResult.confirmed_count)} confirmed · ${clean(scannerResult.forming_count)} forming</span>${scannerRows.length ? `<div class="ai-scanner-result-list">${scannerRows.map((row) => `<div><strong>${clean(row.symbol)}</strong><span>${clean(String(Math.round(Number(row.match_percentage || 0))))}% · ${clean(String(row.outcome || "evaluated").replaceAll("_", " "))}</span></div>`).join("")}</div>` : ""}${scannerResult.common_missing_reasons?.length ? `<small>Common misses: ${scannerResult.common_missing_reasons.map((entry) => `${clean(entry.condition)} (${clean(entry.count)})`).join(", ")}</small>` : ""}<small>${clean(scannerResult.disclaimer || "Research only. Not buy or sell advice.")}</small></section>`
      : "";
    const delivery = item.failed
      ? '<small class="ai-chat-delivery failed">Not sent · Retry below</small>'
      : item.pending ? '<small class="ai-chat-delivery">Sending…</small>' : "";
    const avatarIcon = window.icon?.(user ? "user" : "spark", "icon") || "";
    wrapper.innerHTML = `<span class="ai-chat-avatar">${avatarIcon}</span><div class="ai-chat-bubble"><p>${text}</p>${understanding}${jargon}${snapshot}${scanner}${delivery}<small class="ai-chat-message-meta">${clean(timestamp)}</small></div>`;
    return wrapper;
  }

  window.HilalChatMessageRenderer = Object.freeze({ render });
})();
