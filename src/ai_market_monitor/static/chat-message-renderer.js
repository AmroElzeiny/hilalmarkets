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
    if (!user && payload.response_fingerprint) {
      wrapper.dataset.responseFingerprint = String(payload.response_fingerprint);
    }
    if (!user && payload.active_language) {
      wrapper.lang = String(payload.active_language);
    }
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
    const scannerUi = payload.scanner_ui || {};
    const allScannerRows = Array.isArray(scannerResult?.results)
      ? scannerResult.results
      : [];
    const scannerRows = allScannerRows.slice(0, 5);
    const safeMatchPercentage = (value) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? Math.round(parsed) : 0;
    };
    const confirmedCount = Number.isFinite(Number(scannerResult?.confirmed_count))
      ? Number(scannerResult.confirmed_count)
      : scannerRows.filter((row) => row?.category === "confirmed" || row?.outcome === "confirmed").length;
    const formingCount = Number.isFinite(Number(scannerResult?.forming_count))
      ? Number(scannerResult.forming_count)
      : scannerRows.filter((row) => ["forming", "near_miss"].includes(row?.outcome)).length;
    const readOnlyScanner = payload.read_only === true
      && scannerResult?.read_only === true
      && scannerResult?.strategy_mutated === false;
    const scanner = !user
      && item.message_type === "scanner_result"
      && scannerResult
      ? readOnlyScanner
        ? `<section class="ai-scanner-result-card"><strong>${clean(scannerUi.title || "Read-only Scanner")}</strong><span>${clean(scannerResult.symbols_scanned ?? 0)} ${clean(scannerUi.checked || "screened coins checked")} · ${clean(allScannerRows.length)} ${clean(scannerUi.matched || "matched")}</span><small>${clean(scannerUi.read_only || "Read-only")} · ${clean(scannerUi.no_changes || "no setup changes")}${scannerResult.evaluated_at ? ` · ${clean(scannerResult.evaluated_at)}` : ""}</small><small>${clean(scannerUi.research || "Research only. Not buy or sell advice.")}</small></section>`
        : `<section class="ai-scanner-result-card"><strong>${clean(scannerUi.title || "Scanner result")}</strong><span>${clean(scannerResult.symbols_scanned ?? 0)} ${clean(scannerUi.checked || "symbols scanned")} · ${clean(confirmedCount)} ${clean(scannerUi.confirmed || "confirmed")} · ${clean(formingCount)} ${clean(scannerUi.forming || "forming")}</span>${scannerRows.length ? `<div class="ai-scanner-result-list">${scannerRows.map((row) => `<div><strong>${clean(row.symbol)}</strong><span>${clean(String(safeMatchPercentage(row.match_percentage)))}% · ${clean(String(row.outcome || "evaluated").replaceAll("_", " "))}</span></div>`).join("")}</div>` : ""}${scannerResult.common_missing_reasons?.length ? `<small>${clean(scannerUi.commonMisses || "Common misses")}: ${scannerResult.common_missing_reasons.map((entry) => `${clean(entry.condition)} (${clean(entry.count)})`).join(", ")}</small>` : ""}<small>${clean(scannerResult.disclaimer || scannerUi.research || "Research only. Not buy or sell advice.")}</small></section>`
      : "";
    // A failed message says *why* it failed, and only offers a retry when trying again
    // could actually work. "Retry below" under an exhausted daily allowance sends a
    // beginner round a loop that can never succeed, and hides the fact that the Builder
    // is still there and still free.
    const deliveryReason = item.failed && item.failure_reason
      ? `<small class="ai-chat-delivery failed">${clean(item.failure_reason)}</small>`
      : "";
    const delivery = item.failed
      ? `${deliveryReason}<small class="ai-chat-delivery failed">${
          item.failure_retryable === false
            ? "Not sent · you can still build this yourself"
            : "Not sent · Retry below"
        }</small>`
      : item.pending ? '<small class="ai-chat-delivery">Sending…</small>' : "";
    const avatarIcon = window.icon?.(user ? "user" : "spark", "icon") || "";
    wrapper.innerHTML = `<span class="ai-chat-avatar">${avatarIcon}</span><div class="ai-chat-bubble"><p>${text}</p>${understanding}${jargon}${snapshot}${scanner}${delivery}<small class="ai-chat-message-meta">${clean(timestamp)}</small></div>`;
    return wrapper;
  }

  window.HilalChatMessageRenderer = Object.freeze({ render });
})();
