(function () {
  const root = document.body;
  const apiBase = root?.dataset.dashboardApi || "/api/v1/dashboard";

  function showToast(message, type = "success") {
    const toast = document.getElementById("dash-toast");
    if (!toast) return;
    toast.textContent = message;
    toast.className = `dash-toast ${type}`;
    toast.hidden = false;
    window.setTimeout(() => {
      toast.hidden = true;
    }, 6000);
  }

  async function requestJson(url, options = {}) {
    const { headers = {}, ...requestOptions } = options;
    const method = String(requestOptions.method || "GET").toUpperCase();
    const csrfToken = root?.dataset.csrfToken;
    const response = await fetch(url, {
      credentials: "same-origin",
      ...requestOptions,
      headers: {
        "Content-Type": "application/json",
        ...headers,
        ...(!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken
          ? { "X-CSRF-Token": csrfToken }
          : {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail || payload.message || response.statusText;
      throw new Error(friendlyApiError(detail));
    }
    return payload;
  }

  async function api(path, options = {}) {
    return requestJson(`${apiBase}${path}`, options);
  }

  async function whatsappApi(path, options = {}) {
    return requestJson(`/api/v1/whatsapp${path}`, options);
  }

  function friendlyApiError(detail) {
    if (detail === "notification_channel_required") {
      return "Connect Telegram before starting monitoring.";
    }
    const verifiedMessages = {
      interpretation_approval_required: "Review and approve the interpretation first.",
      interpretation_unresolved: "Resolve every ambiguous or unsupported interpretation item.",
      strategy_examples_regressed: "A saved example fails on this version. Review it before approval.",
      approved_version_immutable: "This approved version is immutable. Create a new draft to edit it.",
      strategy_conflict_detected: "The rules contain a critical contradiction that must be resolved.",
      proof_integrity_violation: "The stored proof failed its integrity check.",
    };
    if (typeof detail === "string" && verifiedMessages[detail]) return verifiedMessages[detail];
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const strategyVersionIssue = detail.find((item) => {
        const loc = Array.isArray(item?.loc) ? item.loc : [];
        return loc.includes("strategy_version_id");
      });
      if (strategyVersionIssue) return "Strategy version is missing or invalid.";
      const firstMessage = detail.find((item) => item?.msg)?.msg;
      return firstMessage || "The request needs one required field before it can run.";
    }
    if (detail && typeof detail === "object") {
      return detail.message || detail.detail || "The request could not be processed.";
    }
    return "The request could not be processed.";
  }

  const pendingMonitorPublishKey = "traceedge-pending-monitor-publish";

  function notificationChannelRequiredUrl(message = "notification_channel_required") {
    return `/dashboard/integrations?message=${encodeURIComponent(message)}&t=${Date.now()}`;
  }

  function pendingMonitorPublishUrl() {
    return notificationChannelRequiredUrl("notification_channel_required");
  }

  function savePendingMonitorPublish(strategyId, version) {
    if (!strategyId || !version?.id) return;
    window.localStorage.setItem(
      pendingMonitorPublishKey,
      JSON.stringify({
        strategy_id: strategyId,
        strategy_version_id: version.id,
        expected_schema_hash: version.schema_hash || null,
        created_at: new Date().toISOString(),
      }),
    );
  }

  function connectedNotificationChannel(payload) {
    const channelActive = (channel) =>
      Boolean(channel && channel.status === "active" && channel.alerts_enabled !== false);
    return channelActive(payload?.telegram);
  }

  async function hasNotificationChannel() {
    try {
      return connectedNotificationChannel(await api("/integrations"));
    } catch {
      return false;
    }
  }

  async function publishStrategyVersion(strategyId, version) {
    return api(`/strategies/${strategyId}/publish`, {
      method: "POST",
      body: JSON.stringify({
        strategy_version_id: version.id || version.strategy_version_id,
        expected_schema_hash: version.schema_hash || version.expected_schema_hash || null,
      }),
    });
  }

  const defaultCapabilities = {
    condition_types: [
      { value: "indicator", label: "Indicator" },
      { value: "price_action", label: "Price action" },
      { value: "candle_pattern", label: "Candle pattern" },
      { value: "market_filter", label: "Market filter" },
      { value: "risk", label: "Risk" },
    ],
    indicators: [
      { name: "ema", label: "EMA" },
      { name: "sma", label: "SMA" },
      { name: "rsi", label: "RSI" },
      { name: "macd", label: "MACD" },
      { name: "volume_ratio", label: "Volume ratio" },
      { name: "vwap", label: "VWAP" },
    ],
    price_actions: [
      { name: "range_breakout", label: "Range breakout" },
      { name: "bullish_liquidity_sweep", label: "Bullish liquidity sweep" },
    ],
    candle_patterns: [
      { name: "bullish_engulfing", label: "Bullish engulfing" },
      { name: "strong_close_near_high", label: "Strong close near high" },
    ],
    market_filters: [{ name: "average_volume", label: "Average volume" }],
    risk_rules: [],
    items: [],
    categories: [],
    logic_operators: [
      { key: "and", display_name: "All of", parameters: [] },
      { key: "or", display_name: "Any of", parameters: [] },
      { key: "not", display_name: "Not", parameters: [] },
      { key: "sequence", display_name: "Sequence / Then", parameters: [] },
    ],
  };

  let capabilityRegistry = defaultCapabilities;

  async function loadCapabilityRegistry() {
    try {
      capabilityRegistry = await api("/capabilities");
    } catch {
      capabilityRegistry = defaultCapabilities;
    }
    return capabilityRegistry;
  }

  function csv(value) {
    return String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function field(form, name) {
    return form.elements[name];
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function chartTheme() {
    const light =
      document.documentElement.dataset.theme === "light" ||
      root?.classList.contains("theme-light");
    return light
      ? {
          background: "#fafbfc",
          text: "#2b2e35",
          grid: "rgba(99, 113, 108, .13)",
          up: "#55712a",
          down: "#8d3029",
          target: "#8a6316",
          stop: "#8d3029",
          entry: "#46551b",
        }
      : {
          background: "#202329",
          text: "#ffffff",
          grid: "rgba(225, 229, 234, .12)",
          up: "#cbfa4d",
          down: "#e4b8b2",
          target: "#cbfa4d",
          stop: "#e4b8b2",
          entry: "#7ba428",
        };
  }

  function safeJson(value, fallback = {}) {
    try {
      return JSON.parse(value || "{}");
    } catch {
      return fallback;
    }
  }

  function renderLoadingState(element, message = "Loading...") {
    if (!element) return;
    element.hidden = false;
    element.classList.remove("error", "success");
    element.textContent = message;
  }

  function renderSuccessState(element, payload, emptyMessage = "No data returned.") {
    if (!element) return;
    element.hidden = false;
    element.classList.remove("error");
    element.classList.add("success");
    if (payload === null || payload === undefined || payload === "") {
      element.textContent = emptyMessage;
      return;
    }
    element.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  }

  function renderWrittenReport(elementId, report) {
    const element = document.getElementById(elementId);
    if (!element) return;
    if (!report) {
      element.textContent = "No written report was produced for this run.";
      return;
    }
    const passed = safeArray(report.passed_conditions)
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("");
    const blockers = safeArray(report.blocking_conditions)
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("");
    const bestParts = [];
    if (report.best_symbol) bestParts.push(`Best symbol: ${report.best_symbol}`);
    if (report.best_score !== undefined && report.best_score !== null) {
      bestParts.push(`Best score: ${report.best_score}%`);
    }
    if (report.best_outcome) bestParts.push(`Outcome: ${report.best_outcome}`);
    element.innerHTML = `
      <h3>${escapeHtml(report.title || "Replay Report")}</h3>
      <p><strong>${escapeHtml(report.headline || "Replay completed.")}</strong></p>
      <p>${escapeHtml(report.summary || "")}</p>
      ${bestParts.length ? `<p>${bestParts.map(escapeHtml).join(" - ")}</p>` : ""}
      ${passed ? `<p>Passed highlights:</p><ul>${passed}</ul>` : ""}
      ${blockers ? `<p>Blocking or missing conditions:</p><ul>${blockers}</ul>` : ""}
      ${report.next_step ? `<p>${escapeHtml(report.next_step)}</p>` : ""}
    `;
  }

  function renderEmptyState(element, message = "Nothing to show yet.") {
    if (!element) return;
    element.hidden = false;
    element.classList.remove("error");
    element.textContent = message;
  }

  function renderErrorState(element, error, recovery = "Try again or adjust the inputs.") {
    if (!element) return;
    element.hidden = false;
    element.classList.add("error");
    const message = error?.message || String(error || "Unknown error");
    element.textContent = `${message}\n\n${recovery}`;
  }

  /* The older assistant page and the one-time Scanner used to live here: about 3,700
   * lines that drew a rule form, translated a typed sentence into rules, and ran a
   * single scan. Both pages are gone — one canvas authors a monitor now — and the code
   * went with them rather than staying as a second, unreachable way to build a rule.
   *
   * Nothing outside it used any of it. Three names it declared (`titleize`,
   * `openDrawer`, `renderInterpretation`) are declared again further down this file and
   * were already shadowing these copies, so the live pages keep the definitions they
   * were really running. */

  function initBacktests() {
    const form = document.getElementById("backtest-form");
    const chartHost = document.getElementById("backtest-trading-chart");
    const annotationLayer = document.getElementById("backtest-annotation-layer");
    const toolbar = document.getElementById("backtest-chart-toolbar");
    const timeframeSelect = document.getElementById("backtest-chart-timeframe");
    const chartStatus = document.getElementById("backtest-chart-status");
    const colors = chartTheme();
    const workspace = {
      jobId: null,
      timeframe: null,
      chart: null,
      series: null,
      annotations: [],
      dirty: false,
      tool: "pointer",
      firstPoint: null,
      resizeObserver: null,
      rendering: false,
    };

    function setChartStatus(message, dirty = false) {
      if (!chartStatus) return;
      chartStatus.textContent = message;
      chartStatus.classList.toggle("dirty", dirty);
    }

    function destroyBacktestChart() {
      workspace.resizeObserver?.disconnect();
      workspace.resizeObserver = null;
      workspace.chart?.remove();
      workspace.chart = null;
      workspace.series = null;
      chartHost?.replaceChildren();
      annotationLayer?.replaceChildren();
    }

    function svgElement(name, attributes) {
      const element = document.createElementNS("http://www.w3.org/2000/svg", name);
      Object.entries(attributes).forEach(([key, value]) => {
        element.setAttribute(key, String(value));
      });
      return element;
    }

    function renderBacktestAnnotations() {
      if (
        workspace.rendering ||
        !annotationLayer ||
        !workspace.chart ||
        !workspace.series
      ) return;
      workspace.rendering = true;
      window.requestAnimationFrame(() => {
        const width = annotationLayer.clientWidth;
        const height = annotationLayer.clientHeight;
        annotationLayer.setAttribute("viewBox", `0 0 ${width} ${height}`);
        annotationLayer.replaceChildren();
        workspace.annotations.forEach((annotation) => {
          const y1 = workspace.series.priceToCoordinate(Number(annotation.price1));
          if (!Number.isFinite(y1)) return;
          if (annotation.type === "horizontal") {
            annotationLayer.appendChild(svgElement("line", {
              x1: 0,
              y1,
              x2: width,
              y2: y1,
              stroke: annotation.color || colors.entry,
              "stroke-width": 2,
              "stroke-dasharray": "7 5",
            }));
            return;
          }
          const x1 = workspace.chart.timeScale().timeToCoordinate(Number(annotation.time1));
          if (!Number.isFinite(x1)) return;
          if (annotation.type === "line") {
            const x2 = workspace.chart.timeScale().timeToCoordinate(Number(annotation.time2));
            const y2 = workspace.series.priceToCoordinate(Number(annotation.price2));
            if (!Number.isFinite(x2) || !Number.isFinite(y2)) return;
            annotationLayer.appendChild(svgElement("line", {
              x1,
              y1,
              x2,
              y2,
              stroke: annotation.color || colors.entry,
              "stroke-width": 2,
            }));
            return;
          }
          const text = svgElement("text", {
            x: Math.min(width - 10, x1 + 7),
            y: Math.max(16, y1 - 8),
            fill: annotation.color || colors.entry,
          });
          text.textContent = annotation.text || "Note";
          annotationLayer.appendChild(text);
        });
        workspace.rendering = false;
      });
    }

    function annotationTimestamp(value) {
      if (typeof value === "number") return Math.floor(value);
      if (value && typeof value === "object" && "year" in value) {
        return Math.floor(Date.UTC(value.year, value.month - 1, value.day) / 1000);
      }
      return null;
    }

    function chartPoint(event) {
      if (!annotationLayer || !workspace.chart || !workspace.series) return null;
      const rect = annotationLayer.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const time = annotationTimestamp(workspace.chart.timeScale().coordinateToTime(x));
      const price = workspace.series.coordinateToPrice(y);
      return time !== null && Number.isFinite(price) ? { time, price: Number(price) } : null;
    }

    function annotationId() {
      return window.crypto?.randomUUID?.() || `annotation-${Date.now()}-${Math.random()}`;
    }

    function setBacktestTool(tool) {
      workspace.tool = tool;
      workspace.firstPoint = null;
      document.querySelectorAll("[data-backtest-tool]").forEach((button) => {
        button.classList.toggle("active", button.dataset.backtestTool === tool);
      });
      annotationLayer?.classList.toggle("drawing", tool !== "pointer");
      setChartStatus(
        tool === "pointer"
          ? "Pan and zoom the chart, or choose a drawing tool."
          : tool === "line"
            ? "Click two chart points to draw a trend line."
            : `Click the chart to place a ${tool === "text" ? "short note" : "horizontal level"}.`,
        workspace.dirty,
      );
    }

    function addBacktestAnnotation(point) {
      if (workspace.tool === "line" && !workspace.firstPoint) {
        workspace.firstPoint = point;
        setChartStatus("First point placed. Click the second point.", workspace.dirty);
        return;
      }
      let annotation;
      if (workspace.tool === "line") {
        annotation = {
          id: annotationId(),
          type: "line",
          time1: workspace.firstPoint.time,
          price1: workspace.firstPoint.price,
          time2: point.time,
          price2: point.price,
          color: colors.entry,
        };
      } else if (workspace.tool === "horizontal") {
        annotation = {
          id: annotationId(),
          type: "horizontal",
          price1: point.price,
          color: colors.entry,
        };
      } else if (workspace.tool === "text") {
        const requested = window.prompt("Short chart note (1 to 5 words):", "");
        const text = String(requested || "").trim().split(/\s+/).slice(0, 5).join(" ");
        if (!text) return;
        annotation = {
          id: annotationId(),
          type: "text",
          time1: point.time,
          price1: point.price,
          text,
          color: colors.entry,
        };
      }
      if (!annotation) return;
      workspace.annotations.push(annotation);
      workspace.firstPoint = null;
      workspace.dirty = true;
      renderBacktestAnnotations();
      setChartStatus("Unsaved drawing changes.", true);
    }

    async function saveBacktestAnnotations() {
      if (!workspace.jobId || !workspace.timeframe) return;
      setChartStatus("Saving drawings...");
      try {
        await api(`/charts/backtest/${workspace.jobId}/annotations`, {
          method: "PUT",
          body: JSON.stringify({
            timeframe: workspace.timeframe,
            annotations: workspace.annotations,
          }),
        });
        workspace.dirty = false;
        setChartStatus("Drawings saved for this symbol and timeframe.");
      } catch (error) {
        setChartStatus(`Could not save drawings: ${error.message}`, true);
        showToast(error.message, "error");
      }
    }

    async function loadBacktest(jobId, timeframe = null) {
      const result = document.getElementById("backtest-result");
      renderLoadingState(result, "Loading historical replay...");
      try {
        if (workspace.dirty) await saveBacktestAnnotations();
        const suffix = timeframe ? `?timeframe=${encodeURIComponent(timeframe)}` : "";
        const payload = await api(`/charts/backtest/${jobId}${suffix}`);
        destroyBacktestChart();
        workspace.jobId = jobId;
        workspace.timeframe = payload.chart.timeframe;
        workspace.annotations = safeArray(payload.annotations);
        workspace.dirty = false;
        workspace.firstPoint = null;
        if (toolbar) toolbar.hidden = false;
        if (timeframeSelect) {
          timeframeSelect.innerHTML = safeArray(payload.timeframes)
            .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
            .join("");
          timeframeSelect.value = workspace.timeframe;
        }
        if (!window.LightweightCharts || !chartHost || !payload.chart?.candles?.length) {
          throw new Error("Interactive replay chart data is unavailable");
        }
        workspace.chart = window.LightweightCharts.createChart(chartHost, {
          width: chartHost.clientWidth || 720,
          height: chartHost.clientHeight || 520,
          layout: { background: { color: colors.background }, textColor: colors.text },
          grid: {
            vertLines: { color: colors.grid },
            horzLines: { color: colors.grid },
          },
          timeScale: {
            timeVisible: workspace.timeframe !== "1d",
            secondsVisible: false,
            borderColor: colors.grid,
          },
          rightPriceScale: { borderColor: colors.grid },
          crosshair: { mode: 1 },
        });
        workspace.series = workspace.chart.addCandlestickSeries({
          upColor: colors.up,
          downColor: colors.down,
          borderVisible: false,
          wickUpColor: colors.up,
          wickDownColor: colors.down,
        });
        workspace.series.setData(payload.chart.candles.map((candle) => ({
          time: candleTime(candle.timestamp),
          open: safeNumber(candle.open),
          high: safeNumber(candle.high),
          low: safeNumber(candle.low),
          close: safeNumber(candle.close),
        })));
        overlayPrices(payload.chart.overlays).forEach((line) => {
          workspace.series.createPriceLine({
            price: line.value,
            color: line.color,
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: line.label,
          });
        });
        workspace.series.setMarkers(safeArray(payload.chart.markers).map((marker) => ({
          time: candleTime(marker.time),
          position: marker.position || "belowBar",
          color: marker.kind === "condition" ? colors.entry : colors.up,
          shape: marker.shape || "circle",
          text: marker.text,
        })));
        workspace.chart.timeScale().fitContent();
        workspace.chart.timeScale().subscribeVisibleLogicalRangeChange(
          renderBacktestAnnotations,
        );
        workspace.resizeObserver = new ResizeObserver(() => {
          if (!workspace.chart || !chartHost) return;
          workspace.chart.resize(chartHost.clientWidth, chartHost.clientHeight);
          renderBacktestAnnotations();
        });
        workspace.resizeObserver.observe(chartHost);
        window.setTimeout(renderBacktestAnnotations, 40);
        setBacktestTool("pointer");
        setChartStatus(
          payload.annotations_saved_at
            ? "Saved drawings restored for this symbol and timeframe."
            : "Pan and zoom the chart, or choose a drawing tool.",
        );
        renderWrittenReport("backtest-report", payload.report);
        renderSuccessState(result, payload);
      } catch (error) {
        renderErrorState(result, error, "Run the replay again or reduce the time window.");
        showToast(error.message, "error");
      }
    }
    document.querySelectorAll("[data-load-backtest]").forEach((button) => {
      button.addEventListener("click", () => loadBacktest(button.dataset.loadBacktest));
    });
    document.querySelectorAll("[data-backtest-tool]").forEach((button) => {
      button.addEventListener("click", () => setBacktestTool(button.dataset.backtestTool));
    });
    annotationLayer?.addEventListener("click", (event) => {
      if (workspace.tool === "pointer") return;
      const point = chartPoint(event);
      if (point) addBacktestAnnotation(point);
    });
    timeframeSelect?.addEventListener("change", () => {
      loadBacktest(workspace.jobId, timeframeSelect.value);
    });
    document.querySelector("[data-backtest-undo]")?.addEventListener("click", () => {
      if (!workspace.annotations.length) return;
      workspace.annotations.pop();
      workspace.dirty = true;
      renderBacktestAnnotations();
      setChartStatus("Last drawing removed. Save to keep this change.", true);
    });
    document.querySelector("[data-backtest-clear]")?.addEventListener("click", () => {
      workspace.annotations = [];
      workspace.dirty = true;
      renderBacktestAnnotations();
      setChartStatus("All drawings cleared. Save to keep this change.", true);
    });
    document.querySelector("[data-backtest-save]")?.addEventListener(
      "click",
      saveBacktestAnnotations,
    );
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = document.getElementById("backtest-result");
      renderLoadingState(result, "Processing historical replay...");
      try {
        const selected = field(form, "strategy_id").selectedOptions[0];
        const payload = {
          strategy_id: field(form, "strategy_id").value,
          strategy_version_id: selected?.dataset.versionId || null,
          exchange: field(form, "exchange").value || "binance",
          symbols: csv(field(form, "symbols").value),
          timeframe: field(form, "timeframe").value || "15m",
          started_at_range: new Date(field(form, "started_at_range").value).toISOString(),
          ended_at_range: new Date(field(form, "ended_at_range").value).toISOString(),
          parameters: { source: "dashboard" },
        };
        const created = await api("/backtests", { method: "POST", body: JSON.stringify(payload) });
        const run = await api(`/backtests/${created.job.id}/run`, { method: "POST" });
        renderSuccessState(result, run.result);
        await loadBacktest(created.job.id, payload.timeframe);
        showToast("Historical replay completed.");
      } catch (error) {
        renderErrorState(result, error, "Check the symbols and replay dates.");
        showToast(error.message, "error");
      }
    });
  }

  function initExports() {
    const form = document.getElementById("export-form");
    async function runExport(jobId) {
      const result = document.getElementById("export-result");
      renderLoadingState(result, "Generating export...");
      try {
        const payload = await api(`/exports/${jobId}/run`, { method: "POST" });
        renderSuccessState(result, payload);
        showToast("Export generated.");
        if (payload.job?.file_url) window.location.href = payload.job.file_url;
      } catch (error) {
        renderErrorState(result, error, "Try again or reduce the export filter scope.");
        showToast(error.message, "error");
      }
    }
    document.querySelectorAll("[data-run-export]").forEach((button) => {
      button.addEventListener("click", () => runExport(button.dataset.runExport));
    });
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = document.getElementById("export-result");
      renderLoadingState(result, "Creating export job...");
      try {
        const created = await api("/exports", {
          method: "POST",
          body: JSON.stringify({
            export_type: field(form, "export_type").value,
            format: field(form, "format").value,
            filters: { source: "dashboard" },
          }),
        });
        await runExport(created.job.id);
      } catch (error) {
        renderErrorState(result, error, "Check the export type and try again.");
        showToast(error.message, "error");
      }
    });
  }

  function initSupport() {
    const form = document.getElementById("support-ticket-form");
    if (!form) return;
    const screenshotInput = field(form, "screenshots");
    const screenshotName = form.querySelector("[data-support-file-name]");
    screenshotInput?.addEventListener("change", () => {
      const files = Array.from(screenshotInput.files || []);
      if (screenshotName) {
        screenshotName.textContent = files.length
          ? files.map((file) => file.name).join(", ")
          : "No files chosen";
      }
    });
    async function screenshotPayload(file) {
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
        throw new Error(`${file.name} is not a supported screenshot type.`);
      }
      if (file.size > 5 * 1024 * 1024) {
        throw new Error(`${file.name} is larger than 5 MB.`);
      }
      const buffer = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      const chunkSize = 32_768;
      for (let index = 0; index < buffer.length; index += chunkSize) {
        binary += String.fromCharCode(...buffer.subarray(index, index + chunkSize));
      }
      return {
        filename: file.name,
        content_type: file.type,
        data_base64: window.btoa(binary),
      };
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = document.getElementById("support-result");
      renderLoadingState(result, "Sending support request...");
      try {
        const files = Array.from(field(form, "screenshots").files || []);
        if (files.length > 3) throw new Error("Attach no more than three screenshots.");
        const screenshots = await Promise.all(files.map(screenshotPayload));
        await api("/support/tickets", {
          method: "POST",
          body: JSON.stringify({
            category: "general",
            email: field(form, "email").value,
            subject: field(form, "subject").value,
            description: field(form, "description").value,
            context: { source: "dashboard" },
            screenshots,
          }),
        });
        renderSuccessState(result, "Support request sent. We will contact you by email.");
        showToast("Support request sent.");
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        renderErrorState(result, error, "Review the email, description and screenshots.");
        showToast(error.message, "error");
      }
    });
  }

  function candleTime(value) {
    const millis = new Date(value).getTime();
    return Number.isFinite(millis) ? Math.floor(millis / 1000) : value;
  }

  function overlayPrices(overlays) {
    const colors = chartTheme();
    const prices = [];
    if (!overlays) return prices;
    const entry = overlays.entry_zone;
    if (entry && typeof entry === "object") {
      ["low", "high", "entry_low", "entry_high"].forEach((key) => {
        if (Number.isFinite(Number(entry[key]))) {
          prices.push({ label: `entry ${key}`, value: safeNumber(entry[key]), color: colors.entry });
        }
      });
    }
    ["entry_zone_low", "entry_zone_high", "stop_price", "invalidation_level"].forEach((key) => {
      if (Number.isFinite(Number(overlays[key]))) {
        prices.push({
          label: key,
          value: safeNumber(overlays[key]),
          color: key.includes("stop") || key.includes("invalidation") ? colors.stop : colors.entry,
        });
      }
    });
    safeArray(overlays.targets || overlays.target_levels).forEach((target, index) => {
      const value = typeof target === "object" ? target.price || target.value : target;
      if (Number.isFinite(Number(value))) {
        prices.push({ label: `target ${index + 1}`, value: safeNumber(value), color: colors.target });
      }
    });
    const grouped = [];
    prices.forEach((line) => {
      const existing = grouped.find(
        (item) => Math.abs(item.value - line.value) <= Math.max(1e-10, Math.abs(line.value) * 1e-8),
      );
      if (existing) {
        if (!existing.label.includes(line.label)) existing.label += ` / ${line.label}`;
        if (line.color === colors.stop) existing.color = colors.stop;
      } else {
        grouped.push({ ...line });
      }
    });
    return grouped;
  }

  function renderReplayChart(payload) {
    const candles = safeArray(payload.candles);
    const container = document.getElementById("replay-chart");
    const fallback = document.getElementById("replay-chart-fallback");
    const colors = chartTheme();
    if (window.LightweightCharts && container && candles.length) {
      container.innerHTML = "";
      container.style.display = "block";
      if (fallback) fallback.hidden = true;
      const chart = window.LightweightCharts.createChart(container, {
        height: 456,
        width: container.clientWidth || undefined,
        layout: { background: { color: colors.background }, textColor: colors.text },
        grid: {
          vertLines: { color: colors.grid },
          horzLines: { color: colors.grid },
        },
      });
      window.setTimeout(() => {
        chart.resize(container.clientWidth || 720, 456);
        chart.timeScale().fitContent();
      }, 0);
      const series = chart.addCandlestickSeries({
        upColor: colors.up,
        downColor: colors.down,
        borderVisible: false,
        wickUpColor: colors.up,
        wickDownColor: colors.down,
      });
      series.setData(candles.map((candle) => ({
        time: candleTime(candle.timestamp),
        open: safeNumber(candle.open),
        high: safeNumber(candle.high),
        low: safeNumber(candle.low),
        close: safeNumber(candle.close),
      })));
      overlayPrices(payload.overlays).forEach((line) => {
        series.createPriceLine({
          price: line.value,
          color: line.color,
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: line.label,
        });
      });
      series.setMarkers(safeArray(payload.markers).map((marker) => ({
        time: candleTime(marker.time),
        position: marker.label === "confirmed" ? "aboveBar" : "belowBar",
        color: marker.label === "confirmed" ? colors.up : colors.entry,
        shape: marker.label === "confirmed" ? "arrowUp" : "circle",
        text: marker.text || `${safeNumber(marker.score).toFixed(0)}%`,
      })));
      chart.timeScale().fitContent();
      return;
    }
    if (container) container.style.display = "none";
    if (fallback) {
      fallback.hidden = false;
      drawCandles(fallback, candles, payload.overlays, safeArray(payload.markers));
    }
  }

  async function initChart() {
    const canvas = document.getElementById("amm-chart");
    if (!canvas) return;
    const url = canvas.dataset.candlesUrl;
    if (!url) return;
    try {
      const response = await fetch(url, { credentials: "same-origin" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Chart data unavailable");
      drawCandles(canvas, safeArray(payload.items));
    } catch (error) {
      const ctx = canvas.getContext("2d");
      // --hm-copy. This is the sentence that tells somebody the chart could not load,
      // so it is the one thing on the canvas that must be readable; the old value was
      // the muted grey, which measured 3.98:1 on white.
      ctx.fillStyle = "#50555e";
      ctx.font = "14px Onest, ui-sans-serif, system-ui, sans-serif";
      ctx.fillText(error.message, 20, 40);
    }
  }

  function drawCandles(canvas, candles, overlays = null, markers = []) {
    candles = safeArray(candles).map((candle) => ({
      ...candle,
      open: safeNumber(candle.open),
      high: safeNumber(candle.high),
      low: safeNumber(candle.low),
      close: safeNumber(candle.close),
      volume: safeNumber(candle.volume),
    }));
    markers = safeArray(markers);
    const parent = canvas.parentElement;
    const width = Math.max(300, (parent?.clientWidth || 720) - 24);
    const height = 456;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    const colors = chartTheme();
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = colors.background;
    ctx.fillRect(0, 0, width, height);
    if (!candles.length) {
      ctx.fillStyle = colors.text;
      ctx.fillText("No candles returned.", 20, 40);
      return;
    }
    const pad = 24;
    const highs = candles.map((candle) => safeNumber(candle.high));
    const lows = candles.map((candle) => safeNumber(candle.low));
    overlayPrices(overlays).forEach((line) => {
      highs.push(line.value);
      lows.push(line.value);
    });
    const max = Math.max(...highs);
    const min = Math.min(...lows);
    const span = max - min || 1;
    const xStep = (width - pad * 2) / candles.length;
    const y = (price) => height - pad - ((price - min) / span) * (height - pad * 2);
    ctx.strokeStyle = colors.grid;
    ctx.lineWidth = 1;
    for (let i = 0; i < 5; i += 1) {
      const gridY = pad + (i * (height - pad * 2)) / 4;
      ctx.beginPath();
      ctx.moveTo(pad, gridY);
      ctx.lineTo(width - pad, gridY);
      ctx.stroke();
    }
    candles.forEach((candle, index) => {
      const x = pad + index * xStep + xStep / 2;
      const up = candle.close >= candle.open;
      ctx.strokeStyle = up ? colors.up : colors.down;
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.moveTo(x, y(candle.high));
      ctx.lineTo(x, y(candle.low));
      ctx.stroke();
      const top = y(Math.max(candle.open, candle.close));
      const bottom = y(Math.min(candle.open, candle.close));
      const bodyWidth = Math.max(2, xStep * 0.58);
      ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, Math.max(2, bottom - top));
    });
    const usedLabelRows = new Set();
    const labelY = (baseY) => {
      let row = Math.max(1, Math.min(Math.floor((height - 18) / 16), Math.round(baseY / 16)));
      while (usedLabelRows.has(row) && row < Math.floor((height - 18) / 16)) row += 1;
      usedLabelRows.add(row);
      return row * 16;
    };
    overlayPrices(overlays).forEach((line) => {
      ctx.strokeStyle = line.color;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(pad, y(line.value));
      ctx.lineTo(width - pad, y(line.value));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = line.color;
      ctx.fillText(line.label, Math.max(pad, width - pad - 130), labelY(y(line.value)));
    });
    const markerSlots = new Map();
    markers.forEach((marker) => {
      const markerTime = new Date(marker.time).getTime();
      let index = candles.findIndex((candle) => new Date(candle.timestamp).getTime() === markerTime);
      if (index < 0) {
        index = candles.reduce((best, candle, currentIndex) => {
          const distance = Math.abs(new Date(candle.timestamp).getTime() - markerTime);
          const bestDistance = Math.abs(new Date(candles[best].timestamp).getTime() - markerTime);
          return distance < bestDistance ? currentIndex : best;
        }, 0);
      }
      const candle = candles[index];
      if (!candle) return;
      const x = pad + index * xStep + xStep / 2;
      const eventLabel = marker.text || marker.label || marker.outcome || "event";
      const confirmed = String(eventLabel).includes("confirmed");
      const slotKey = `${index}:${confirmed ? "top" : "bottom"}`;
      const slot = markerSlots.get(slotKey) || 0;
      markerSlots.set(slotKey, slot + 1);
      const pointY = confirmed ? y(candle.high) - 10 - slot * 16 : y(candle.low) + 10 + slot * 16;
      ctx.fillStyle = confirmed ? colors.up : colors.entry;
      ctx.beginPath();
      ctx.arc(x, pointY, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillText(eventLabel, Math.min(x + 6, width - pad - 140), pointY - 6);
    });
    ctx.fillStyle = colors.text;
    ctx.font = "12px Onest, ui-sans-serif, system-ui, sans-serif";
    ctx.fillText(`High ${max.toFixed(4)} · Low ${min.toFixed(4)}`, pad, 16);
  }

  function drawLineChart(canvas, rows, key) {
    rows = safeArray(rows);
    const parent = canvas.parentElement;
    const width = Math.max(300, (parent?.clientWidth || 720) - 24);
    const height = 456;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    const colors = chartTheme();
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = colors.background;
    ctx.fillRect(0, 0, width, height);
    if (!rows.length) {
      ctx.fillStyle = colors.text;
      ctx.fillText("No historical replay points returned.", 20, 40);
      return;
    }
    const pad = 24;
    const values = rows.map((row) => safeNumber(row[key], 0));
    const max = Math.max(...values);
    const min = Math.min(...values);
    const span = max - min || 1;
    const xStep = (width - pad * 2) / Math.max(1, rows.length - 1);
    const y = (value) => height - pad - ((value - min) / span) * (height - pad * 2);
    ctx.strokeStyle = colors.up;
    ctx.lineWidth = 2;
    ctx.beginPath();
    values.forEach((value, index) => {
      const x = pad + index * xStep;
      const pointY = y(value);
      if (index === 0) ctx.moveTo(x, pointY);
      else ctx.lineTo(x, pointY);
    });
    ctx.stroke();
    ctx.fillStyle = colors.text;
    ctx.font = "12px Onest, ui-sans-serif, system-ui, sans-serif";
    ctx.fillText(`Hypothetical quality index ${min.toFixed(3)} - ${max.toFixed(3)}`, pad, 16);
  }

  function initLifecycles() {
    const dialog = document.getElementById("lifecycle-chart-dialog");
    const observabilityRoot = document.querySelector("[data-observability-root]");
    const radarList = document.querySelector("[data-radar-list]");
    const radarSummary = document.querySelector("[data-radar-summary]");
    const radarPagination = document.querySelector("[data-radar-pagination]");
    const radarState = document.querySelector("[data-radar-state]");
    const radarSort = document.querySelector("[data-radar-sort]");
    const radarView = document.querySelector("[data-radar-view]");
    const healthList = document.querySelector("[data-health-list]");
    const bottleneckList = document.querySelector("[data-bottleneck-list]");
    const bottleneckRequired = document.querySelector("[data-bottleneck-required]");
    const drawer = document.querySelector("[data-observability-drawer]");
    const drawerBackdrop = document.querySelector("[data-observability-drawer-backdrop]");
    const drawerContent = document.querySelector("[data-observability-drawer-content]");
    let previousFocus = null;
    let radarPage = 1;

    const selectedMonitorId = () => new URLSearchParams(window.location.search).get("monitor") || "";
    const pretty = (value) => String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
    const dateText = (value) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Not available";
    const relativeTime = (value) => {
      if (!value) return "Not available";
      const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000);
      const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
      if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
      const minutes = Math.round(seconds / 60);
      if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
      const hours = Math.round(minutes / 60);
      if (Math.abs(hours) < 48) return formatter.format(hours, "hour");
      return formatter.format(Math.round(hours / 24), "day");
    };
    const valueText = (value) => {
      if (value === null || value === undefined || value === "") return "Unavailable";
      if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
      return String(value);
    };
    const iconUrl = (name, color = "0f5c4d") => {
      const aliases = {
        "triangle-alert": "alert",
        "lock-keyhole": "shield",
        "circle-check": "check",
        "clock-3": "clock",
        timer: "clock",
        "flask-conical": "info",
        "circle-x": "close",
      };
      const svg = (window.icon?.(aliases[name] || name, "icon") || "")
        .replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ')
        .replaceAll("currentColor", `#${color}`);
      return `data:image/svg+xml,${encodeURIComponent(svg)}`;
    };

    function updateObservabilityUrl() {
      const params = new URLSearchParams(window.location.search);
      if (radarState?.value) params.set("state", radarState.value); else params.delete("state");
      if (radarSort?.value && radarSort.value !== "readiness") params.set("sort", radarSort.value); else params.delete("sort");
      window.history.replaceState({}, "", `${window.location.pathname}${params.size ? `?${params}` : ""}${window.location.hash}`);
    }

    function renderRadar(payload) {
      if (!radarList) return;
      const requestedState = radarState?.value || "";
      const requestedSort = radarSort?.value || "readiness";
      const items = safeArray(payload.items)
        .filter((item) => !requestedState || item.state === requestedState)
        .sort((left, right) => {
          if (requestedSort === "newest_change") {
            return new Date(right.last_changed_at || right.last_evaluated_at || 0)
              - new Date(left.last_changed_at || left.last_evaluated_at || 0);
          }
          if (requestedSort === "symbol") {
            return String(left.symbol || "").localeCompare(String(right.symbol || ""));
          }
          if (requestedSort === "blocker") {
            return String(left.blocker?.label || "").localeCompare(
              String(right.blocker?.label || ""),
            );
          }
          const leftTotal = safeNumber(left.required?.total);
          const rightTotal = safeNumber(right.required?.total);
          const leftReadiness = leftTotal ? safeNumber(left.required?.passed) / leftTotal : 0;
          const rightReadiness = rightTotal ? safeNumber(right.required?.passed) / rightTotal : 0;
          return rightReadiness - leftReadiness;
        });
      radarList.setAttribute("aria-busy", "false");
      radarSummary.innerHTML = items.length
        ? `<strong>${items.length}</strong><span>candidates in this view</span><span>${items.filter((item) => item.state === "confirmation_pending" || item.state === "near_miss").length} close now</span>`
        : "";
      if (!items.length) {
        radarList.innerHTML = `<div class="observability-empty"><img src="${iconUrl("radar")}" alt=""><strong>No readiness evidence yet</strong><p>The next completed worker evaluation will add candidates here.</p></div>`;
        radarPagination.replaceChildren();
        return;
      }
      radarList.innerHTML = items.map((item) => {
        const passed = safeNumber(item.required?.passed);
        const total = safeNumber(item.required?.total);
        const completion = total ? Math.round(passed / total * 100) : 0;
        const blocker = item.blocker || {};
        const detailButton = item.setup_id
          ? `<button type="button" class="candidate-open" data-candidate-investigate="${escapeHtml(item.setup_id)}">Inspect evidence</button>`
          : `<button type="button" class="candidate-open" data-candidate-detail="${escapeHtml(item.id)}">View conditions</button>`;
        return `<article class="readiness-candidate state-${escapeHtml(item.state)}" data-candidate-id="${escapeHtml(item.id)}">
          <div class="candidate-state-line"><span class="candidate-state"><i></i>${escapeHtml(pretty(item.state))}</span><span class="candidate-health ${escapeHtml(item.data_health)}"><img src="${iconUrl(item.data_health === "healthy" ? "database" : "triangle-alert")}" alt="">${escapeHtml(pretty(item.data_health))}</span></div>
          <div class="candidate-identity"><div><strong>${escapeHtml(item.symbol)}</strong><span>${escapeHtml(item.exchange)} · ${escapeHtml(item.timeframe)}</span></div><span class="candidate-monitor-tag" title="${escapeHtml(item.monitor_name)}"><img src="${iconUrl("radar", "0f5c4d")}" alt="">${escapeHtml(item.monitor_name)}</span></div>
          <div class="candidate-readiness"><div><strong>${passed}/${total}</strong><span>required rules passed</span></div><div class="candidate-progress" role="progressbar" aria-valuenow="${completion}" aria-valuemin="0" aria-valuemax="100"><i style="--candidate-progress:${completion}%"></i></div><span>${safeNumber(item.optional?.passed)}/${safeNumber(item.optional?.total)} optional</span></div>
          <div class="candidate-blocker"><img src="${iconUrl(blocker.key ? "lock-keyhole" : "circle-check")}" alt=""><div><span>${blocker.key ? "Current blocker" : "Required rules complete"}</span><strong>${escapeHtml(blocker.label || "No required blocker")}</strong>${blocker.key ? `<small>Current: ${escapeHtml(valueText(blocker.actual))} · Required: ${escapeHtml(valueText(blocker.required))}${blocker.distance !== null && blocker.distance !== undefined ? ` · Distance: ${escapeHtml(valueText(blocker.distance))}` : ""}</small>` : ""}</div></div>
          <div class="candidate-meta"><span><img src="${iconUrl("activity")}" alt="">${escapeHtml(item.most_recent_change)}</span><span><img src="${iconUrl("clock-3")}" alt="">Evaluated ${escapeHtml(relativeTime(item.last_evaluated_at))}</span><span><img src="${iconUrl("timer")}" alt="">Next close ${escapeHtml(relativeTime(item.next_candle_close_at))}</span></div>
          <div class="candidate-actions">${detailButton}<a href="/dashboard/create-monitor?monitor=${escapeHtml(item.monitor_id)}">Edit in Canvas</a></div>
        </article>`;
      }).join("");
      radarPagination.innerHTML = payload.pages > 1
        ? `<button type="button" data-radar-page="${Math.max(1, payload.page - 1)}" ${payload.page <= 1 ? "disabled" : ""}>Previous</button><span>Page ${payload.page} of ${payload.pages}</span><button type="button" data-radar-page="${Math.min(payload.pages, payload.page + 1)}" ${payload.page >= payload.pages ? "disabled" : ""}>Next</button>`
        : "";
      radarList.querySelectorAll("[data-candidate-detail]").forEach((button) => {
        button.addEventListener("click", () => openCandidateDetail(items.find((item) => item.id === button.dataset.candidateDetail), button));
      });
      radarList.querySelectorAll("[data-candidate-investigate]").forEach((button) => {
        button.addEventListener("click", () => openInvestigation(button.dataset.candidateInvestigate, button));
      });
      radarPagination.querySelectorAll("[data-radar-page]").forEach((button) => {
        button.addEventListener("click", () => { radarPage = Number(button.dataset.radarPage); loadRadar(); });
      });
    }

    async function loadRadar({ quiet = false } = {}) {
      if (!radarList) return;
      if (!quiet) radarList.setAttribute("aria-busy", "true");
      // Everything that can fail lives inside the try, including building the request
      // and rendering the answer. A throw outside it used to leave the placeholder
      // spinning with no message, which reads to a user as a page that never loads.
      try {
        const params = new URLSearchParams({ page: String(radarPage), page_size: "50", sort: radarSort?.value || "readiness" });
        if (selectedMonitorId()) params.set("monitor_id", selectedMonitorId());
        if (radarState?.value) params.set("lifecycle_state", radarState.value);
        renderRadar(await api(`/observability/radar?${params}`));
      } catch (error) {
        radarList.setAttribute("aria-busy", "false");
        radarList.innerHTML = `<div class="observability-error"><strong>Readiness evidence is unavailable</strong><p>${escapeHtml(error.message)}</p><button type="button" data-radar-retry>Retry</button></div>`;
        radarList.querySelector("[data-radar-retry]")?.addEventListener("click", () => loadRadar());
      }
    }

    function renderHealth(payload) {
      if (!healthList) return;
      const items = safeArray(payload.items);
      if (!items.length) { healthList.innerHTML = `<div class="observability-empty"><strong>No health history yet</strong><p>Health appears after the worker completes an active monitor cycle.</p></div>`; return; }
      healthList.innerHTML = items.map((item) => `<article class="monitor-health-card">
        <div class="health-card-head"><span class="candidate-monitor-tag"><img src="${iconUrl("radar", "0f5c4d")}" alt="">${escapeHtml(item.monitor_name)}</span><small>Version ${escapeHtml(item.strategy_version)}</small></div>
        <div class="health-dimension"><span>Technical health</span><strong class="health-status ${escapeHtml(item.technical_status)}"><i></i>${escapeHtml(pretty(item.technical_status))}</strong>${safeArray(item.technical_causes).map((cause) => `<p>${escapeHtml(cause.message)}</p>`).join("")}</div>
        <div class="health-dimension"><span>Strategy health</span><strong class="health-status ${escapeHtml(item.strategy_status)}"><i></i>${escapeHtml(pretty(item.strategy_status))}</strong>${safeArray(item.strategy_causes).map((cause) => `<p>${escapeHtml(cause.message)}</p>`).join("")}</div>
        <div class="health-metrics"><span><strong>${safeNumber(item.metrics?.symbols_scanned)}/${safeNumber(item.metrics?.symbols_expected)}</strong> symbols</span><span><strong>${safeNumber(item.metrics?.provider_errors)}</strong> provider errors</span><span><strong>${safeNumber(item.metrics?.alerts_24h)}</strong> alerts/24h</span></div>
        <div class="health-actions"><a href="/dashboard/opportunities?monitor=${escapeHtml(item.monitor_id)}">Open candidates</a><a href="#condition-bottlenecks">Inspect top blocker</a><button type="button" data-health-explain="${escapeHtml(item.monitor_id)}">Ask AI to explain</button><a href="/dashboard/create-monitor?monitor=${escapeHtml(item.monitor_id)}">Edit in Canvas</a></div>
      </article>`).join("");
      healthList.querySelectorAll("[data-health-explain]").forEach((button) => {
        button.addEventListener("click", async () => {
          if (!drawerContent) return;
          drawerContent.innerHTML = `<div class="investigation-loading"><div class="observability-skeleton"></div></div>`;
          openDrawer(button);
          try {
            const result = await api(`/observability/health/${button.dataset.healthExplain}/explain`, { method: "POST", body: JSON.stringify({ explanation_type: "monitor_health" }) });
            drawerContent.innerHTML = `<section class="investigation-ai"><strong>Grounded health explanation</strong><p>${escapeHtml(result.explanation)}</p></section><section class="investigation-actions"><button type="button" data-observability-drawer-close>Close</button></section>`;
            drawerContent.querySelector("[data-observability-drawer-close]")?.addEventListener("click", closeDrawer);
          } catch (error) { drawerContent.innerHTML = `<div class="observability-error"><strong>Explanation unavailable</strong><p>${escapeHtml(error.message)}</p></div>`; }
        });
      });
    }

    async function loadHealth() {
      if (!healthList) return;
      try {
        const suffix = selectedMonitorId() ? `?monitor_id=${encodeURIComponent(selectedMonitorId())}` : "";
        renderHealth(await api(`/observability/health${suffix}`));
      } catch (error) { healthList.innerHTML = `<div class="observability-error"><strong>Health summary unavailable</strong><p>${escapeHtml(error.message)}</p></div>`; }
    }

    function renderBottlenecks(payload) {
      if (!bottleneckList) return;
      const items = safeArray(payload.items);
      if (!items.length) { bottleneckList.innerHTML = `<div class="observability-empty"><strong>Not enough condition history</strong><p>Hilal Markets will rank blockers after retained lifecycle evidence is aggregated.</p></div>`; return; }
      bottleneckList.innerHTML = items.map((item, index) => `<article class="bottleneck-row ${item.sample_status === "low_sample" ? "low-sample" : ""}">
        <span class="bottleneck-rank">${index + 1}</span><div class="bottleneck-copy"><div><strong>${escapeHtml(item.condition_label)}</strong><span>${escapeHtml(pretty(item.rule_role))} · ${escapeHtml(item.timeframe || "Any timeframe")}</span></div><p>Final blocker for ${escapeHtml(valueText(item.final_blocker_share))}% of near-complete candidates · ${item.evaluation_count} evaluations</p>${item.median_actual_when_blocked !== null ? `<small>Median value when blocked: ${escapeHtml(valueText(item.median_actual_when_blocked))} · Required: ${escapeHtml(valueText(item.average_required))}</small>` : ""}<div class="bottleneck-bar"><i style="--blocker-share:${Math.min(100, safeNumber(item.final_blocker_share))}%"></i></div>${item.sample_status === "low_sample" ? `<span class="low-sample-label">Low sample · interpret cautiously</span>` : ""}${item.counterfactual ? `<div class="counterfactual-preview"><img src="${iconUrl("flask-conical")}" alt=""><p>${escapeHtml(item.counterfactual.message)}</p></div>` : ""}</div><div class="bottleneck-actions"><a href="/dashboard/create-monitor?monitor=${escapeHtml(item.monitor_id)}">Change this monitor</a><a href="/dashboard/create-monitor?monitor=${escapeHtml(item.monitor_id)}">Review rule</a></div>
      </article>`).join("");
    }

    async function loadBottlenecks() {
      if (!bottleneckList) return;
      try {
        const params = new URLSearchParams();
        if (selectedMonitorId()) params.set("monitor_id", selectedMonitorId());
        if (bottleneckRequired?.checked) params.set("required", "true");
        renderBottlenecks(await api(`/observability/bottlenecks?${params}`));
      } catch (error) { bottleneckList.innerHTML = `<div class="observability-error"><strong>Bottleneck history unavailable</strong><p>${escapeHtml(error.message)}</p></div>`; }
    }

    function openDrawer(trigger) {
      if (!drawer || !drawerBackdrop) return;
      previousFocus = trigger || document.activeElement;
      drawer.hidden = false; drawerBackdrop.hidden = false;
      document.body.classList.add("observability-drawer-open");
      window.requestAnimationFrame(() => drawer.classList.add("open"));
      drawer.focus();
    }

    function closeDrawer() {
      if (!drawer || !drawerBackdrop) return;
      drawer.classList.remove("open");
      document.body.classList.remove("observability-drawer-open");
      window.setTimeout(() => { drawer.hidden = true; drawerBackdrop.hidden = true; previousFocus?.focus?.(); }, 180);
    }

    function conditionRows(conditions) {
      return safeArray(conditions).map((condition) => `<details class="investigation-condition status-${escapeHtml(condition.status || condition.outcome)}"><summary><span><i></i><strong>${escapeHtml(condition.label)}</strong></span><b>${escapeHtml(pretty(condition.status || condition.outcome))}</b></summary><div><span>Current: <strong>${escapeHtml(valueText(condition.actual))}</strong></span><span>Required: <strong>${escapeHtml(valueText(condition.required_value ?? condition.required))}</strong></span><span>Timeframe: <strong>${escapeHtml(condition.timeframe || "Configured")}</strong></span>${condition.explanation ? `<p>${escapeHtml(condition.explanation)}</p>` : ""}</div></details>`).join("");
    }

    function openCandidateDetail(item, trigger) {
      if (!item || !drawerContent) return;
      drawerContent.innerHTML = `<section class="investigation-hero"><span class="candidate-state"><i></i>${escapeHtml(pretty(item.state))}</span><h3>${escapeHtml(item.symbol)} · ${escapeHtml(item.monitor_name)}</h3><p>${escapeHtml(item.most_recent_change)}</p></section><section class="investigation-section"><h3>Latest condition tree</h3>${conditionRows(item.latest_values)}</section><section class="investigation-actions"><a href="/dashboard/create-monitor?monitor=${escapeHtml(item.monitor_id)}">Open Strategy Canvas</a><a href="/dashboard/create-monitor?monitor=${escapeHtml(item.monitor_id)}">Change this monitor</a><button type="button" data-observability-drawer-close>Close</button></section>`;
      drawerContent.querySelector("[data-observability-drawer-close]")?.addEventListener("click", closeDrawer);
      openDrawer(trigger);
    }

    function renderInvestigation(payload) {
      if (!drawerContent) return;
      const retry = payload.actions?.retry_delivery_id ? `<button type="button" data-retry-delivery="${escapeHtml(payload.actions.retry_delivery_id)}">Retry Notification</button>` : "";
      drawerContent.innerHTML = `<section class="investigation-hero category-${escapeHtml(payload.primary_category)}"><span class="investigation-evidence">${escapeHtml(pretty(payload.evidence_availability))} evidence</span><h3>${escapeHtml(payload.symbol)} · ${escapeHtml(payload.monitor_name)}</h3><p>${escapeHtml(payload.primary_reason)}</p><div><span>Version ${escapeHtml(payload.strategy_version)}</span><span>${escapeHtml(payload.exchange)} · ${escapeHtml(payload.timeframe)}</span><span>${escapeHtml(dateText(payload.evaluated_window?.to))}</span></div></section><section class="investigation-summary"><article><strong>${safeNumber(payload.condition_summary?.passed)}</strong><span>checks passed</span></article><article><strong>${safeNumber(payload.condition_summary?.failed_required)}</strong><span>required failed</span></article><article><strong>${escapeHtml(pretty(payload.provider_health?.status))}</strong><span>provider health</span></article></section><section class="investigation-section"><h3>Condition evidence</h3>${conditionRows(payload.conditions) || `<p>Exact condition snapshots are unavailable for this historical lifecycle.</p>`}</section><section class="investigation-section"><h3>Lifecycle timeline</h3><div class="investigation-timeline">${safeArray(payload.events).map((event) => `<div><i></i><p><strong>${escapeHtml(pretty(event.to))}</strong><span>${escapeHtml(pretty(event.reason))} · ${escapeHtml(dateText(event.occurred_at))}</span></p></div>`).join("") || `<p>No state-change events were retained.</p>`}</div></section><section class="investigation-section"><h3>Notification path</h3>${safeArray(payload.notification_deliveries).length ? safeArray(payload.notification_deliveries).map((delivery) => `<div class="delivery-evidence"><img src="${iconUrl(delivery.status === "sent" || delivery.status === "delivered" ? "circle-check" : "circle-x")}" alt=""><div><strong>${escapeHtml(pretty(delivery.channel))} · ${escapeHtml(pretty(delivery.status))}</strong><span>${delivery.last_error_detail ? escapeHtml(delivery.last_error_detail) : `Attempts: ${safeNumber(delivery.attempt_count)}`}</span></div></div>`).join("") : `<p>Delivery was not attempted because the setup did not reach a deliverable confirmed state.</p>`}</section><section class="investigation-ai" data-investigation-ai hidden></section><section class="investigation-actions"><button type="button" data-ask-investigation-ai="${escapeHtml(payload.setup_id)}">Ask AI to Explain</button><a href="${escapeHtml(payload.actions.view_full_lifecycle)}">View Full Lifecycle</a><a href="${escapeHtml(payload.actions.open_canvas)}">Open Strategy Canvas</a><a href="${escapeHtml(payload.actions.refine_chat)}">Refine in AI Chat</a><a href="${escapeHtml(payload.actions.view_monitor_health)}">View Monitor Health</a>${retry}<button type="button" data-observability-drawer-close>Close</button></section>`;
      drawerContent.querySelector("[data-observability-drawer-close]")?.addEventListener("click", closeDrawer);
      drawerContent.querySelector("[data-ask-investigation-ai]")?.addEventListener("click", async (event) => {
        const target = drawerContent.querySelector("[data-investigation-ai]"); target.hidden = false; target.textContent = "Explaining the retained evidence...";
        try { const result = await api(`/lifecycles/${event.currentTarget.dataset.askInvestigationAi}/investigation/explain`, { method: "POST", body: JSON.stringify({ explanation_type: "why_no_alert" }) }); target.innerHTML = `<strong>Grounded explanation</strong><p>${escapeHtml(result.explanation)}</p>`; }
        catch (error) { target.innerHTML = `<strong>Explanation unavailable</strong><p>${escapeHtml(error.message)}</p>`; }
      });
      drawerContent.querySelector("[data-retry-delivery]")?.addEventListener("click", async (event) => {
        try { await api(`/notification-deliveries/${event.currentTarget.dataset.retryDelivery}/retry`, { method: "POST" }); event.currentTarget.disabled = true; event.currentTarget.textContent = "Retry queued"; showToast("Notification retry queued."); }
        catch (error) { showToast(error.message, "error"); }
      });
    }

    async function openInvestigation(setupId, trigger) {
      if (!drawerContent) return;
      drawerContent.innerHTML = `<div class="investigation-loading"><div class="observability-skeleton"></div><div class="observability-skeleton"></div></div>`;
      openDrawer(trigger);
      try { renderInvestigation(await api(`/lifecycles/${setupId}/investigation`)); }
      catch (error) { drawerContent.innerHTML = `<div class="observability-error"><strong>Investigation unavailable</strong><p>${escapeHtml(error.message)}</p><button type="button" data-investigation-retry>Retry</button></div>`; drawerContent.querySelector("[data-investigation-retry]")?.addEventListener("click", () => openInvestigation(setupId, trigger)); }
    }

    if (observabilityRoot) {
      const params = new URLSearchParams(window.location.search);
      if (radarState) radarState.value = params.get("state") || "";
      if (radarSort) radarSort.value = params.get("sort") || "readiness";
      const lifecycleStack = document.querySelector(".journey-panel > .stack");
      const lifecycleCards = [...document.querySelectorAll("[data-lifecycle-card]")];
      let lifecycleEmpty = document.querySelector("[data-lifecycle-filter-empty]");
      if (lifecycleStack && lifecycleCards.length && !lifecycleEmpty) {
        lifecycleEmpty = document.createElement("div");
        lifecycleEmpty.className = "empty-state compact";
        lifecycleEmpty.dataset.lifecycleFilterEmpty = "";
        lifecycleEmpty.innerHTML = "<h3>No journeys match these filters</h3><p>Choose another state or sort order to review the retained lifecycle evidence.</p>";
        lifecycleEmpty.hidden = true;
        lifecycleStack.after(lifecycleEmpty);
      }
      const groupedLifecycleState = (state) => {
        if (["candidate_detected", "detected", "forming"].includes(state)) return "forming";
        if (["near_confirmation", "armed"].includes(state)) return "confirmation_pending";
        if (["confirmed", "alert_sent", "entry_active", "entry_zone_active", "entry_touched"].includes(state)) return "confirmed";
        if (["entry_zone_missed", "entry_missed", "suppressed", "blocked"].includes(state)) return "near_miss";
        if (["data_unavailable"].includes(state)) return "provider_data_error";
        return state;
      };
      const syncLifecycleCards = () => {
        if (!lifecycleStack || !lifecycleCards.length) return;
        const requestedState = radarState?.value || "";
        const requestedSort = radarSort?.value || "readiness";
        const ordered = [...lifecycleCards].sort((left, right) => {
          if (requestedSort === "newest_change") {
            return new Date(right.dataset.lifecycleChanged || 0)
              - new Date(left.dataset.lifecycleChanged || 0);
          }
          if (requestedSort === "symbol") {
            return String(left.querySelector(".coin h3")?.textContent || "").localeCompare(
              String(right.querySelector(".coin h3")?.textContent || ""),
            );
          }
          if (requestedSort === "blocker") {
            const blocker = (card) => card.querySelector(
              "[data-lifecycle-details] > div:nth-child(2) .method-result strong",
            )?.textContent || "";
            return blocker(left).localeCompare(blocker(right));
          }
          return safeNumber(right.dataset.lifecycleScore) - safeNumber(left.dataset.lifecycleScore);
        });
        let visibleCount = 0;
        ordered.forEach((card) => {
          const visible = !requestedState
            || groupedLifecycleState(card.dataset.lifecycleState) === requestedState;
          card.hidden = !visible;
          if (visible) visibleCount += 1;
          lifecycleStack.append(card);
        });
        if (lifecycleEmpty) lifecycleEmpty.hidden = visibleCount !== 0;
      };
      radarState?.addEventListener("change", () => { radarPage = 1; updateObservabilityUrl(); syncLifecycleCards(); loadRadar(); });
      radarSort?.addEventListener("change", () => { radarPage = 1; updateObservabilityUrl(); syncLifecycleCards(); loadRadar(); });
      radarView?.addEventListener("click", () => { const compact = radarList.classList.toggle("compact"); radarView.setAttribute("aria-pressed", String(compact)); radarView.querySelector("span").textContent = compact ? "Expanded" : "Compact"; });
      bottleneckRequired?.addEventListener("change", loadBottlenecks);
      syncLifecycleCards();
      // A rejected promise here is invisible: no error appears, and every placeholder
      // stays in its loading state for good. Report it instead.
      Promise.all([loadRadar(), loadHealth(), loadBottlenecks()]).catch((error) => {
        console.error("[dashboard] readiness panels could not load", error);
      });
      const interval = Math.max(5, safeNumber(observabilityRoot.dataset.pollSeconds, 15)) * 1000;
      window.setInterval(() => {
        if (document.hidden) return;
        Promise.all([loadRadar({ quiet: true }), loadHealth()]).catch((error) => {
          console.error("[dashboard] readiness refresh failed", error);
        });
      }, interval);
    }

    const filterTrigger = document.querySelector("[data-monitor-filter-trigger]");
    const filterMenu = document.querySelector("[data-monitor-filter-menu]");
    filterTrigger?.addEventListener("click", () => { const open = filterTrigger.getAttribute("aria-expanded") === "true"; filterTrigger.setAttribute("aria-expanded", String(!open)); filterMenu.hidden = open; if (!open) filterMenu.querySelector("input")?.focus(); });
    document.querySelector("[data-monitor-filter-search]")?.addEventListener("input", (event) => { const term = event.target.value.toLowerCase(); filterMenu.querySelectorAll("[data-monitor-name]").forEach((option) => { option.hidden = !option.dataset.monitorName.includes(term); }); });
    document.addEventListener("click", (event) => { if (filterMenu && !event.target.closest("[data-monitor-filter]")) { filterMenu.hidden = true; filterTrigger?.setAttribute("aria-expanded", "false"); } });
    drawerBackdrop?.addEventListener("click", closeDrawer);
    document.querySelectorAll("[data-observability-drawer-close]").forEach((button) => button.addEventListener("click", closeDrawer));
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && drawer && !drawer.hidden) { event.preventDefault(); closeDrawer(); } });
    document.querySelectorAll("[data-lifecycle-investigate]").forEach((button) => button.addEventListener("click", () => openInvestigation(button.dataset.lifecycleInvestigate, button)));
    document.querySelectorAll("[data-lifecycle-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const details = document.querySelector(
          `[data-lifecycle-details="${button.dataset.lifecycleToggle}"]`,
        );
        if (!details) return;
        const expanded = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!expanded));
        details.hidden = expanded;
      });
    });
    document.querySelectorAll("[data-lifecycle-mute]").forEach((button) => {
      button.addEventListener("click", async () => {
        const setupId = button.dataset.lifecycleMute;
        if (!setupId) return;
        try {
          await api(`/lifecycles/${setupId}/mute`, { method: "POST" });
          document.querySelector(`[data-lifecycle-card="${setupId}"]`)?.remove();
          showToast("Lifecycle muted and removed from this view.");
        } catch (error) {
          showToast(error.message, "error");
        }
      });
    });
    if (!dialog) return;

    const widgetHost = document.getElementById("lifecycle-tradingview-widget");
    const title = document.getElementById("lifecycle-chart-title");
    const subtitle = document.getElementById("lifecycle-chart-subtitle");
    const status = document.getElementById("lifecycle-chart-status");
    const missingConditions = document.getElementById("lifecycle-missing-conditions");
    const completedConditions = document.getElementById("lifecycle-completed-conditions");
    const timeframeSelect = { innerHTML: "", value: "" };
    const workspace = {
      setupId: null,
      timeframe: null,
      currentExchange: null,
      currentSymbol: null,
      widget: null,
      payloadCache: new Map(),
    };

    function setStatus(message, dirty = false) {
      if (!status) return;
      status.textContent = message;
      status.classList.toggle("dirty", dirty);
    }

    function destroyChart() {
      if (workspace.widget?.remove) {
        try {
          workspace.widget.remove();
        } catch (_) {
          // TradingView cleanup should not block reopening the evidence view.
        }
      }
      workspace.widget = null;
      workspace.payloadCache.clear();
      if (widgetHost) widgetHost.replaceChildren();
    }

    function renderConditionList(container, conditions, emptyText) {
      if (!container) return;
      const rows = safeArray(conditions);
      if (!rows.length) {
        container.innerHTML = `<p class="dash-muted">${escapeHtml(emptyText)}</p>`;
        return;
      }
      container.innerHTML = `<div class="lifecycle-condition-list">${rows
        .map((condition) => {
          const actual = condition.actual ?? "unavailable";
          const required = condition.required ?? "defined by rule";
          const candleTime = condition.candle_timestamp ? new Date(condition.candle_timestamp) : null;
          const evaluatedAt = condition.evaluated_at ? new Date(condition.evaluated_at) : null;
          const candleLabel = candleTime && !Number.isNaN(candleTime.getTime())
            ? candleTime.toLocaleString(undefined, {
              month: "short",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })
            : "No candle timestamp";
          const evaluatedLabel = evaluatedAt && !Number.isNaN(evaluatedAt.getTime())
            ? evaluatedAt.toLocaleString(undefined, {
              month: "short",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })
            : "No evaluation timestamp";
          return `
            <div class="lifecycle-condition-item">
              <strong>${escapeHtml(condition.name || condition.key)}</strong>
              <span>${escapeHtml(condition.timeframe || workspace.timeframe)} - ${escapeHtml(condition.state)}</span>
              <span>Actual: ${escapeHtml(actual)} - Required: ${escapeHtml(required)}</span>
              <small>Candle: ${escapeHtml(candleLabel)} - Evaluated: ${escapeHtml(evaluatedLabel)}</small>
            </div>
          `;
        })
        .join("")}</div>`;
    }

    function ensureTradingViewChartingLibrary() {
      const missingLibraryMessage = "TradingView Charting Library is not installed at /static/charting_library/charting_library.js. Install the official private TradingView package to enable the vertical drawing toolbar, header symbol search, and saved drawings.";
      if (window.__traceedgeChartingLibraryLoaded && window.TradingView?.widget) {
        return Promise.resolve();
      }
      const existing = document.querySelector("script[data-tradingview-charting-library]");
      if (existing) {
        if (existing.dataset.loaded === "true" && window.TradingView?.widget) {
          return Promise.resolve();
        }
        return new Promise((resolve, reject) => {
          existing.addEventListener("load", () => {
            window.__traceedgeChartingLibraryLoaded = true;
            existing.dataset.loaded = "true";
            if (window.TradingView?.widget) resolve();
            else reject(new Error("TradingView Charting Library loaded, but window.TradingView.widget was not available. Check that the official charting_library package was copied completely, including bundles."));
          }, { once: true });
          existing.addEventListener("error", reject, { once: true });
        });
      }
      return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/charting_library/charting_library.js";
        script.async = true;
        script.dataset.tradingviewChartingLibrary = "true";
        script.addEventListener("load", () => {
          window.__traceedgeChartingLibraryLoaded = true;
          script.dataset.loaded = "true";
          if (window.TradingView?.widget) resolve();
          else reject(new Error("TradingView Charting Library loaded, but window.TradingView.widget was not available. Check that the official charting_library package was copied completely, including bundles."));
        }, { once: true });
        script.addEventListener("error", () => reject(new Error(missingLibraryMessage)), { once: true });
        document.head.appendChild(script);
      });
    }

    function ensureTradingViewLightweightCharts() {
      if (window.LightweightCharts?.createChart) {
        return Promise.resolve();
      }
      const existing = document.querySelector("script[data-tradingview-lightweight]");
      if (existing) {
        if (existing.dataset.loaded === "true" && window.LightweightCharts?.createChart) {
          return Promise.resolve();
        }
        return new Promise((resolve, reject) => {
          existing.addEventListener("load", () => {
            existing.dataset.loaded = "true";
            if (window.LightweightCharts?.createChart) resolve();
            else reject(new Error("TradingView Lightweight Charts loaded, but createChart was unavailable."));
          }, { once: true });
          existing.addEventListener("error", reject, { once: true });
        });
      }
      const cdn = root?.dataset.chartLibraryCdn || "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js";
      if (!cdn) {
        return Promise.reject(new Error("TradingView Lightweight Charts CDN is not configured."));
      }
      return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = cdn;
        script.async = true;
        script.dataset.tradingviewLightweight = "true";
        script.addEventListener("load", () => {
          script.dataset.loaded = "true";
          if (window.LightweightCharts?.createChart) resolve();
          else reject(new Error("TradingView Lightweight Charts loaded, but createChart was unavailable."));
        }, { once: true });
        script.addEventListener("error", () => reject(new Error("TradingView Lightweight Charts could not be loaded.")), { once: true });
        document.head.appendChild(script);
      });
    }

    function tradingViewInterval(timeframe) {
      const normalized = String(timeframe || "1m").toLowerCase();
      const mapping = {
        "1m": "1",
        "3m": "3",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "2h": "120",
        "4h": "240",
        "1d": "D",
        "1w": "W",
      };
      return mapping[normalized] || "1";
    }

    function resolutionToTimeframe(resolution) {
      const normalized = String(resolution || "1").toUpperCase();
      const mapping = {
        "1": "1m",
        "3": "3m",
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "120": "2h",
        "240": "4h",
        "D": "1d",
        "1D": "1d",
      };
      return mapping[normalized] || "1m";
    }

    function tradingViewSymbol(exchange, symbol) {
      const provider = String(exchange || "binance").toUpperCase().replace(/[^A-Z0-9]/g, "");
      const pair = String(symbol || "BTC/USDT").toUpperCase().replace(/[^A-Z0-9]/g, "");
      return `${provider || "BINANCE"}:${pair || "BTCUSDT"}`;
    }

    function pairFromCompact(value) {
      const compact = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
      const quotes = ["USDT", "USDC", "BUSD", "USD", "BTC", "ETH"];
      const quote = quotes.find((item) => compact.endsWith(item) && compact.length > item.length);
      if (!quote) return compact.includes("/") ? compact : `${compact}/USDT`;
      return `${compact.slice(0, -quote.length)}/${quote}`;
    }

    function parseTradingViewSymbol(value, fallbackExchange, fallbackSymbol) {
      const raw = String(value || "").trim();
      const [exchangePart, symbolPart] = raw.includes(":")
        ? raw.split(":", 2)
        : [fallbackExchange || "binance", raw || fallbackSymbol || "BTC/USDT"];
      const exchange = String(exchangePart || fallbackExchange || "binance").toLowerCase().replace(/[^a-z0-9]/g, "") || "binance";
      const symbol = pairFromCompact(symbolPart || fallbackSymbol || "BTC/USDT");
      return { exchange, symbol };
    }

    function symbolSearchResults(userInput, fallbackExchange) {
      const exchange = String(fallbackExchange || "binance").toUpperCase();
      const query = String(userInput || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
      const symbols = [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "LINK/USDT",
        "BNB/USDT",
        "XRP/USDT",
        "AVAX/USDT",
        "ADA/USDT",
        "DOGE/USDT",
        "MATIC/USDT",
      ];
      return symbols
        .filter((symbol) => !query || symbol.replace(/[^A-Z0-9]/g, "").includes(query))
        .map((symbol) => ({
          symbol: tradingViewSymbol(exchange, symbol),
          full_name: tradingViewSymbol(exchange, symbol),
          description: `${symbol} spot`,
          exchange,
          ticker: tradingViewSymbol(exchange, symbol),
          type: "crypto",
        }));
    }

    function candleSeconds(value) {
      const date = value ? new Date(value) : null;
      if (!date || Number.isNaN(date.getTime())) return 0;
      return Math.floor(date.getTime() / 1000);
    }

    function chartingLibraryDatafeed(initialPayload) {
      const supportedResolutions = ["1", "3", "5", "15", "30", "60", "120", "240", "D"];
      const lifecycleSymbolName = tradingViewSymbol(initialPayload.setup.exchange, initialPayload.setup.symbol);
      workspace.currentExchange = initialPayload.setup.exchange;
      workspace.currentSymbol = initialPayload.setup.symbol;
      workspace.payloadCache.set(`${lifecycleSymbolName}|${workspace.timeframe}`, initialPayload);

      async function payloadForResolution(symbolInfo, resolution) {
        const timeframe = resolutionToTimeframe(resolution);
        const parsed = parseTradingViewSymbol(
          symbolInfo?.ticker || symbolInfo?.name || lifecycleSymbolName,
          initialPayload.setup.exchange,
          initialPayload.setup.symbol,
        );
        const cacheKey = `${tradingViewSymbol(parsed.exchange, parsed.symbol)}|${timeframe}`;
        if (workspace.payloadCache.has(cacheKey)) return workspace.payloadCache.get(cacheKey);
        const lifecycleSymbol = parsed.exchange === String(initialPayload.setup.exchange).toLowerCase()
          && parsed.symbol === String(initialPayload.setup.symbol).toUpperCase();
        let payload;
        if (lifecycleSymbol) {
          payload = await api(
            `/lifecycles/${workspace.setupId}/chart?timeframe=${encodeURIComponent(timeframe)}`,
          );
        } else {
          const candlesPayload = await api(
            `/charts/candles?exchange=${encodeURIComponent(parsed.exchange)}&symbol=${encodeURIComponent(parsed.symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=1000`,
          );
          payload = {
            setup: {
              ...initialPayload.setup,
              exchange: parsed.exchange,
              symbol: parsed.symbol,
              selected_timeframe: timeframe,
            },
            candles: safeArray(candlesPayload.items),
            markers: [],
            overlays: {},
            completed_conditions: [],
            missing_conditions: [],
            generic_symbol: true,
          };
        }
        workspace.payloadCache.set(cacheKey, payload);
        return payload;
      }

      function applySideEvidence(payload) {
        workspace.timeframe = payload.setup.selected_timeframe || workspace.timeframe || "1m";
        workspace.currentExchange = payload.setup.exchange;
        workspace.currentSymbol = payload.setup.symbol;
        if (payload.generic_symbol) {
          if (missingConditions) {
            missingConditions.innerHTML = `<p class="dash-muted">Symbol changed to ${escapeHtml(payload.setup.symbol)}. Lifecycle conditions remain attached to ${escapeHtml(initialPayload.setup.symbol)}.</p>`;
          }
          if (completedConditions) {
            completedConditions.innerHTML = `<p class="dash-muted">Switch back to ${escapeHtml(initialPayload.setup.symbol)} to see deterministic condition completion marks.</p>`;
          }
          return;
        }
        renderConditionList(missingConditions, payload.missing_conditions, "No unmet deterministic conditions remain.");
        renderConditionList(completedConditions, payload.completed_conditions, "No condition has passed yet.");
      }

      function barsFromPayload(payload, from, to) {
        return safeArray(payload.candles)
          .map((candle) => ({
            time: candleSeconds(candle.timestamp) * 1000,
            open: safeNumber(candle.open),
            high: safeNumber(candle.high),
            low: safeNumber(candle.low),
            close: safeNumber(candle.close),
            volume: safeNumber(candle.volume),
          }))
          .filter((bar) => {
            const seconds = Math.floor(bar.time / 1000);
            return (!from || seconds >= from) && (!to || seconds <= to);
          });
      }

      return {
        onReady(callback) {
          window.setTimeout(() => callback({
            supports_marks: true,
            supports_time: true,
            supports_timescale_marks: false,
            supported_resolutions: supportedResolutions,
          }), 0);
        },
        searchSymbols(userInput, exchange, _symbolType, onResultReadyCallback) {
          onResultReadyCallback(symbolSearchResults(userInput, exchange || initialPayload.setup.exchange));
        },
        resolveSymbol(symbolName, onSymbolResolvedCallback) {
          const parsed = parseTradingViewSymbol(
            symbolName,
            initialPayload.setup.exchange,
            initialPayload.setup.symbol,
          );
          const resolvedName = tradingViewSymbol(parsed.exchange, parsed.symbol);
          window.setTimeout(() => onSymbolResolvedCallback({
            name: resolvedName,
            ticker: resolvedName,
            full_name: resolvedName,
            description: `${parsed.symbol} spot market`,
            type: "crypto",
            session: "24x7",
            exchange: parsed.exchange.toUpperCase(),
            listed_exchange: parsed.exchange.toUpperCase(),
            timezone: "Etc/UTC",
            minmov: 1,
            pricescale: 100000,
            has_intraday: true,
            has_daily: true,
            volume_precision: 2,
            data_status: "streaming",
            supported_resolutions: supportedResolutions,
          }), 0);
        },
        async getBars(symbolInfo, resolution, periodParams, onHistoryCallback, onErrorCallback) {
          try {
            const payload = await payloadForResolution(symbolInfo, resolution);
            applySideEvidence(payload);
            const bars = barsFromPayload(payload, periodParams?.from, periodParams?.to);
            onHistoryCallback(bars, { noData: bars.length === 0 });
          } catch (error) {
            onErrorCallback(error.message);
          }
        },
        async getMarks(_symbolInfo, from, to, onDataCallback, resolution) {
          try {
            const payload = await payloadForResolution(_symbolInfo, resolution);
            if (payload.generic_symbol) {
              onDataCallback([]);
              return;
            }
            const marks = safeArray(payload.markers)
              .map((marker, index) => {
                const time = candleSeconds(marker.time);
                if (!time || time < from || time > to) return null;
                const condition = marker.kind === "condition";
                return {
                  id: `${marker.kind || "event"}-${index}-${time}`,
                  time,
                  color: condition ? "#55712a" : "#8a6316",
                  text: marker.text || marker.label || "Lifecycle event",
                  label: condition ? "C" : "L",
                  labelFontColor: condition ? "#ffffff" : "#2b2e35",
                  minSize: 22,
                };
              })
              .filter(Boolean);
            onDataCallback(marks);
          } catch (_) {
            onDataCallback([]);
          }
        },
        subscribeBars() {},
        unsubscribeBars() {},
        getServerTime(callback) {
          callback(Math.floor(Date.now() / 1000));
        },
      };
    }

    function serializeTradingViewState(value) {
      if (value instanceof Map) {
        return {
          __traceedge_type: "Map",
          entries: Array.from(value.entries()).map(([key, item]) => [
            key,
            serializeTradingViewState(item),
          ]),
        };
      }
      if (value instanceof Set) {
        return {
          __traceedge_type: "Set",
          values: Array.from(value.values()).map(serializeTradingViewState),
        };
      }
      if (Array.isArray(value)) return value.map(serializeTradingViewState);
      if (value && typeof value === "object") {
        return Object.fromEntries(
          Object.entries(value).map(([key, item]) => [key, serializeTradingViewState(item)]),
        );
      }
      return value;
    }

    function deserializeTradingViewState(value) {
      if (Array.isArray(value)) return value.map(deserializeTradingViewState);
      if (value && typeof value === "object") {
        if (value.__traceedge_type === "Map") {
          return new Map(
            safeArray(value.entries).map(([key, item]) => [key, deserializeTradingViewState(item)]),
          );
        }
        if (value.__traceedge_type === "Set") {
          return new Set(safeArray(value.values).map(deserializeTradingViewState));
        }
        return Object.fromEntries(
          Object.entries(value)
            .filter(([key]) => key !== "__traceedge_type")
            .map(([key, item]) => [key, deserializeTradingViewState(item)]),
        );
      }
      return value;
    }

    function tradingViewStorageAdapter() {
      const layoutName = () => `${workspace.currentSymbol || "Lifecycle"} saved chart`;
      const storageParams = () => {
        const symbol = workspace.currentSymbol || "BTC/USDT";
        const timeframe = workspace.timeframe || "1m";
        const query = `timeframe=${encodeURIComponent(timeframe)}&symbol=${encodeURIComponent(symbol)}`;
        return { symbol, timeframe, query };
      };
      return {
        async getAllCharts() {
          const { query } = storageParams();
          const payload = await api(`/lifecycles/${workspace.setupId}/tradingview-layout?${query}`);
          return safeArray(payload.charts).map((chart) => ({
            id: chart.id,
            name: chart.name || layoutName(),
            symbol: chart.symbol || workspace.currentSymbol,
            resolution: tradingViewInterval(chart.resolution || workspace.timeframe),
            timestamp: chart.timestamp ? Math.floor(new Date(chart.timestamp).getTime() / 1000) : Math.floor(Date.now() / 1000),
          }));
        },
        async saveChart(chartData) {
          const { symbol, timeframe } = storageParams();
          const chartId = chartData?.id || `lifecycle-${workspace.setupId}-${symbol}-${timeframe}`;
          const response = await api(`/lifecycles/${workspace.setupId}/tradingview-layout`, {
            method: "PUT",
            body: JSON.stringify({
              timeframe,
              symbol,
              chart_id: chartId,
              layout_id: chartId,
              name: chartData?.name || layoutName(),
              chart_data: serializeTradingViewState(chartData),
            }),
          });
          setStatus("TradingView layout saved with your dashboard profile.");
          return response.chart_id || chartId;
        },
        async getChartContent(chartId) {
          const { query } = storageParams();
          const payload = await api(`/lifecycles/${workspace.setupId}/tradingview-layout?${query}`);
          if (!payload.chart_data) return null;
          return deserializeTradingViewState(payload.chart_data);
        },
        async removeChart(_chartId) {
          return true;
        },
        async saveLineToolsAndGroups(layoutId, chartId, state) {
          const { symbol, timeframe } = storageParams();
          await api(`/lifecycles/${workspace.setupId}/tradingview-drawings`, {
            method: "PUT",
            body: JSON.stringify({
              timeframe,
              symbol,
              layout_id: layoutId || `lifecycle-${workspace.setupId}-${symbol}-${timeframe}`,
              chart_id: chartId || `lifecycle-${workspace.setupId}-${symbol}-${timeframe}`,
              line_tools_state: serializeTradingViewState(state),
            }),
          });
          setStatus("TradingView drawings saved. They will be restored for this symbol and timeframe.");
          return true;
        },
        async loadLineToolsAndGroups(_layoutId, _chartId) {
          const { query } = storageParams();
          const payload = await api(`/lifecycles/${workspace.setupId}/tradingview-drawings?${query}`);
          return payload.line_tools_state
            ? deserializeTradingViewState(payload.line_tools_state)
            : null;
        },
        async getAllStudyTemplates() { return []; },
        async getStudyTemplateContent() { return null; },
        async saveStudyTemplate() { return null; },
        async removeStudyTemplate() { return true; },
        async getDrawingTemplates() { return []; },
        async loadDrawingTemplate() { return null; },
        async saveDrawingTemplate() { return null; },
        async removeDrawingTemplate() { return true; },
      };
    }

    async function renderTradingView(payload) {
      if (!widgetHost) return;
      await ensureTradingViewChartingLibrary();
      widgetHost.replaceChildren();
      const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
      const background = theme === "light" ? "#f5f8fb" : "#202329";
      workspace.currentExchange = payload.setup.exchange;
      workspace.currentSymbol = payload.setup.symbol;
      workspace.widget = new window.TradingView.widget({
        autosize: true,
        symbol: tradingViewSymbol(payload.setup.exchange, payload.setup.symbol),
        interval: tradingViewInterval(workspace.timeframe || "1m"),
        datafeed: chartingLibraryDatafeed(payload),
        library_path: "/static/charting_library/",
        timezone: "Etc/UTC",
        theme,
        style: "1",
        locale: "en",
        toolbar_bg: theme === "light" ? "#f5f8fb" : "#202329",
        enable_publishing: false,
        allow_symbol_change: true,
        hide_side_toolbar: false,
        withdateranges: true,
        save_image: true,
        auto_save_delay: 3,
        load_last_chart: true,
        save_load_adapter: tradingViewStorageAdapter(),
        drawings_access: { type: "black", tools: [] },
        favorites: {
          intervals: ["1", "3", "5", "15", "30", "60", "240", "D"],
          chartTypes: ["Candles"],
        },
        studies: [],
        enabled_features: [
          "study_templates",
          "chart_property_page_trading",
          "saveload_separate_drawings_storage",
          "header_resolutions",
          "symbol_search_hot_key",
          "left_toolbar",
        ],
        disabled_features: [],
        overrides: {
          "paneProperties.background": background,
          "paneProperties.backgroundType": "solid",
          "paneProperties.vertGridProperties.color": theme === "light" ? "#e1e5ea" : "#50555e",
          "paneProperties.horzGridProperties.color": theme === "light" ? "#e1e5ea" : "#50555e",
          "scalesProperties.textColor": theme === "light" ? "#2b2e35" : "#ffffff",
          "mainSeriesProperties.candleStyle.upColor": "#55712a",
          "mainSeriesProperties.candleStyle.downColor": "#8d3029",
          "mainSeriesProperties.candleStyle.borderUpColor": "#55712a",
          "mainSeriesProperties.candleStyle.borderDownColor": "#8d3029",
          "mainSeriesProperties.candleStyle.wickUpColor": "#55712a",
          "mainSeriesProperties.candleStyle.wickDownColor": "#8d3029",
        },
        container_id: "lifecycle-tradingview-widget",
      });
      if (workspace.widget?.onChartReady) {
        workspace.widget.onChartReady(() => {
          setStatus("TradingView tools are ready. Drawings and layouts autosave for this symbol and timeframe.");
          if (workspace.widget?.subscribe) {
            workspace.widget.subscribe("onAutoSaveNeeded", () => {
              if (typeof workspace.widget.saveChartToServer === "function") {
                workspace.widget.saveChartToServer();
              }
            });
          }
        });
      }
    }

    async function renderTradingViewLightweight(payload) {
      if (!widgetHost) return;
      await ensureTradingViewLightweightCharts();
      widgetHost.innerHTML = '<div id="lifecycle-lightweight-chart" class="lifecycle-lightweight-chart"></div>';
      const container = document.getElementById("lifecycle-lightweight-chart");
      if (!container) return;
      const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
      const chart = window.LightweightCharts.createChart(container, {
        autoSize: true,
        layout: {
          background: { color: theme === "light" ? "#fafbfc" : "#202329" },
          textColor: theme === "light" ? "#2b2e35" : "#ffffff",
          fontFamily: "Onest, Arial, sans-serif",
        },
        grid: {
          vertLines: { color: theme === "light" ? "rgba(99,113,108,.14)" : "rgba(225,229,234,.12)" },
          horzLines: { color: theme === "light" ? "rgba(99,113,108,.14)" : "rgba(225,229,234,.12)" },
        },
        rightPriceScale: { borderColor: "rgba(99,113,108,.28)" },
        timeScale: { borderColor: "rgba(99,113,108,.28)", timeVisible: true },
        crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
      });
      const series = chart.addCandlestickSeries({
        upColor: "#55712a",
        downColor: "#8d3029",
        borderUpColor: "#55712a",
        borderDownColor: "#8d3029",
        wickUpColor: "#55712a",
        wickDownColor: "#8d3029",
      });
      const data = safeArray(payload.candles)
        .map((candle) => ({
          time: Math.floor(new Date(candle.timestamp).getTime() / 1000),
          open: safeNumber(candle.open),
          high: safeNumber(candle.high),
          low: safeNumber(candle.low),
          close: safeNumber(candle.close),
        }))
        .filter((candle) => Number.isFinite(candle.time))
        .sort((left, right) => left.time - right.time);
      series.setData(data);
      const markerPayload = safeArray(payload.markers)
        .map((marker) => ({
          time: Math.floor(new Date(marker.time).getTime() / 1000),
          position: marker.position === "aboveBar" ? "aboveBar" : "belowBar",
          color: marker.kind === "lifecycle" ? "#8a6316" : "#55712a",
          shape: marker.kind === "lifecycle" ? "arrowDown" : "circle",
          text: String(marker.text || marker.label || "event").slice(0, 20),
        }))
        .filter((marker) => Number.isFinite(marker.time));
      if (series.setMarkers) series.setMarkers(markerPayload);
      overlayPrices(payload.overlays).forEach((line) => {
        series.createPriceLine({
          price: line.value,
          color: line.color,
          lineStyle: window.LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: line.label,
        });
      });
      chart.timeScale().fitContent();
      workspace.widget = {
        remove: () => chart.remove(),
        chart,
        series,
      };
    }

    function renderLifecycleNativeChart(payload, error = null) {
      if (!widgetHost) return;
      const candles = safeArray(payload.candles);
      const markers = safeArray(payload.markers).map((marker) => ({
        ...marker,
        text: marker.text || marker.label || marker.kind || "event",
      }));
      widgetHost.innerHTML = `
        <div class="lifecycle-native-chart">
          <div class="lifecycle-native-chart-head">
            <span>
              ${window.icon?.("chart", "icon") || ""}
              Native evidence chart
            </span>
            <small>${escapeHtml(candles.length)} candles | ${escapeHtml(markers.length)} evidence marks</small>
          </div>
          <div class="lifecycle-native-chart-frame">
            <canvas id="lifecycle-native-canvas" aria-label="Lifecycle candle evidence chart"></canvas>
          </div>
          ${error ? `<p class="lifecycle-native-chart-note">Using the built-in Hilal Markets chart for this session.</p>` : ""}
        </div>
      `;
      const canvas = document.getElementById("lifecycle-native-canvas");
      if (canvas) {
        drawCandles(canvas, candles, payload.overlays || null, markers);
      }
    }

    async function loadLifecycleChart(setupId, timeframe) {
      destroyChart();
      workspace.setupId = setupId;
      workspace.timeframe = timeframe || "1m";
      setStatus("Loading TradingView chart and deterministic evidence...");
      try {
        const payload = await api(
          `/lifecycles/${setupId}/chart?timeframe=${encodeURIComponent(workspace.timeframe)}`,
        );
        workspace.timeframe = payload.setup.selected_timeframe;
        title.textContent = `${payload.setup.symbol} - ${payload.setup.state_label}`;
        subtitle.textContent =
          `${payload.setup.exchange} - ${payload.setup.direction} - ${payload.setup.completion_score.toFixed(0)}% complete`;
        timeframeSelect.innerHTML = safeArray(payload.timeframes)
          .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
          .join("");
        timeframeSelect.value = workspace.timeframe;
        renderConditionList(
          missingConditions,
          payload.missing_conditions,
          "No unmet deterministic conditions remain.",
        );
        renderConditionList(
          completedConditions,
          payload.completed_conditions,
          "No condition has passed yet.",
        );
        try {
          await renderTradingView(payload);
          setStatus("TradingView Charting Library is using deterministic lifecycle candles, header controls, drawing tools, and saved drawings.");
        } catch (chartError) {
          try {
            await renderTradingViewLightweight(payload);
            setStatus(`${chartError.message} Showing the Lightweight Charts fallback, which does not include TradingView's vertical drawing toolbar or full header controls.`);
          } catch (fallbackError) {
            renderLifecycleNativeChart(payload, fallbackError);
            setStatus(`${chartError.message} Native lifecycle chart rendered from deterministic candles and condition marks.`);
          }
        }
      } catch (error) {
        setStatus(`Chart unavailable: ${error.message}`);
        showToast(error.message, "error");
      }
    }

    document.querySelectorAll("[data-lifecycle-chart]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!dialog) return;
        dialog.showModal();
        loadLifecycleChart(button.dataset.lifecycleChart, "1m");
      });
    });
    async function closeDialog() {
      if (!dialog) return;
      dialog.close();
      destroyChart();
    }
    document.querySelector("[data-lifecycle-dialog-close]")?.addEventListener("click", closeDialog);
    dialog?.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeDialog();
    });
  }

  function initSettings() {
    const form = document.getElementById("dashboard-settings-form");
    const saveButton = form?.querySelector("[data-settings-save]");
    const markDirty = () => {
      if (!saveButton) return;
      saveButton.disabled = false;
      saveButton.classList.add("visible");
    };
    form?.addEventListener("input", markDirty);
    form?.addEventListener("change", markDirty);
    const nearMissEnabled = form?.querySelector("[data-near-miss-enabled]");
    const nearMissThreshold = form?.querySelector("[data-near-miss-threshold]");
    const syncNearMiss = () => {
      if (nearMissThreshold) nearMissThreshold.hidden = nearMissEnabled?.value === "false";
    };
    nearMissEnabled?.addEventListener("change", syncNearMiss);
    syncNearMiss();

    const dayOptions = [...document.querySelectorAll('[data-schedule-options="days"] input')];
    const hourOptions = [...document.querySelectorAll('[data-schedule-options="hours"] input')];
    dayOptions.forEach((input) => input.addEventListener("change", () => {
      if (!input.checked) return;
      if (input.value === "Every Day") {
        dayOptions.forEach((candidate) => { if (candidate !== input) candidate.checked = false; });
      } else {
        const everyDay = dayOptions.find((candidate) => candidate.value === "Every Day");
        if (everyDay) everyDay.checked = false;
      }
    }));
    document.querySelectorAll("[data-schedule-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.scheduleAction;
        if (action?.startsWith("days")) {
          dayOptions.forEach((option) => {
            if (action === "days-all") option.checked = option.value === "Every Day";
            if (action === "days-weekdays") {
              option.checked = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"].includes(option.value);
            }
            if (action === "days-clear") option.checked = false;
          });
          form?.dispatchEvent(new Event("change", { bubbles: true }));
        }
        if (action?.startsWith("hours")) {
          hourOptions.forEach((option) => {
            const hour = Number(option.value.slice(0, 2));
            if (action === "hours-all") option.checked = true;
            if (action === "hours-business") option.checked = hour >= 8 && hour <= 18;
            if (action === "hours-clear") option.checked = false;
          });
          form?.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });
  }

  function initReferralCopy() {
    const button = document.querySelector("[data-copy-referral]");
    const link = document.querySelector("[data-referral-link]");
    if (!button || !link) return;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(link.textContent.trim());
        button.classList.remove("copied");
        void button.offsetWidth;
        button.classList.add("copied");
        showToast("Referral link copied.");
      } catch {
        showToast("The referral link could not be copied.", "error");
      }
    });
  }

  function initIntegrations() {
    const rootElement = document.querySelector("[data-integrations-auto-refresh]");
    if (!rootElement) return;
    const status = rootElement.querySelector("[data-telegram-status]");
    const summaryStatus = document.querySelector("[data-telegram-summary-status]");
    const username = rootElement.querySelector("[data-telegram-username]");
    const delivery = rootElement.querySelector("[data-telegram-delivery]");
    const connectLink = rootElement.querySelector("[data-telegram-connect-link]");
    const connectedButton = rootElement.querySelector("[data-telegram-connected-button]");
    const disconnectButton = rootElement.querySelector("[data-telegram-disconnect]");
    const webFallback = rootElement.querySelector("[data-telegram-web-fallback]");
    const whatsappEnabled = rootElement.dataset.whatsappEnabled === "true";
    const whatsapp = {
      status: rootElement.querySelector("[data-whatsapp-status]"),
      summaryStatus: document.querySelector("[data-whatsapp-summary-status]"),
      number: rootElement.querySelector("[data-whatsapp-number]"),
      optIn: rootElement.querySelector("[data-whatsapp-opt-in]"),
      delivery: rootElement.querySelector("[data-whatsapp-delivery]"),
      testState: rootElement.querySelector("[data-whatsapp-test-state]"),
      error: rootElement.querySelector("[data-whatsapp-error]"),
      errorRow: rootElement.querySelector("[data-whatsapp-error-row]"),
      connectForm: rootElement.querySelector("[data-whatsapp-connect-form]"),
      phone: rootElement.querySelector("[data-whatsapp-phone]"),
      consent: rootElement.querySelector("[data-whatsapp-consent]"),
      locale: rootElement.querySelector("[data-whatsapp-locale]"),
      preferenceLocale: rootElement.querySelector("[data-whatsapp-preference-locale]"),
      linkOutput: rootElement.querySelector("[data-whatsapp-link-output]"),
      linkAnchor: rootElement.querySelector("[data-whatsapp-link-anchor]"),
      linkExpiry: rootElement.querySelector("[data-whatsapp-link-expiry]"),
      connectedControls: rootElement.querySelector("[data-whatsapp-connected-controls]"),
      test: rootElement.querySelector("[data-whatsapp-test]"),
      pause: rootElement.querySelector("[data-whatsapp-pause]"),
      resume: rootElement.querySelector("[data-whatsapp-resume]"),
      save: rootElement.querySelector("[data-whatsapp-save]"),
      clearError: rootElement.querySelector("[data-whatsapp-clear-error]"),
      disconnect: rootElement.querySelector("[data-whatsapp-disconnect]"),
      categories: Array.from(rootElement.querySelectorAll("[data-whatsapp-category]")),
      preferenceCategories: Array.from(
        rootElement.querySelectorAll("[data-whatsapp-preference-category]"),
      ),
    };
    let pendingPublishInFlight = false;
    let whatsappPreferencesDirty = false;
    let previousWhatsAppActive = null;

    function titleize(value) {
      return String(value || "not connected")
        .replace(/_/g, " ")
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
    }

    function setConnectionBadge(element, text, connected) {
      if (!element) return;
      element.textContent = text;
      element.classList.toggle("badge-eligible", connected);
      element.classList.toggle("badge-neutral", !connected);
      element.classList.toggle("connected", connected);
      element.classList.toggle("pending", !connected);
    }

    function renderTelegram(telegram) {
      const connected = connectedNotificationChannel({ telegram });
      const statusText = connected ? titleize(telegram.status) : "Not Connected";
      setConnectionBadge(status, statusText, connected);
      setConnectionBadge(summaryStatus, statusText, connected);
      if (username) username.textContent = telegram?.username ? `@${telegram.username}` : "Username not set";
      if (delivery) {
        delivery.textContent = telegram?.last_delivery_at
          ? "Delivery recorded"
          : "No delivery recorded yet";
      }
      if (connectLink) {
        connectLink.hidden = connected;
        connectLink.textContent = "Link Telegram";
        connectLink.classList.remove("button-secondary");
        connectLink.removeAttribute("aria-disabled");
      }
      if (connectedButton) {
        connectedButton.hidden = !connected;
      }
      if (disconnectButton) {
        disconnectButton.hidden = !connected;
      }
      if (webFallback) {
        webFallback.hidden = connected;
      }
    }

    function whatsappBound(connection) {
      return Boolean(
        connection
        && connection.verified
        && connection.status !== "revoked"
        && !connection.revoked_at,
      );
    }

    function whatsappOptedIn(connection) {
      return Boolean(whatsappBound(connection) && connection.opted_in);
    }

    function whatsappActive(connection) {
      return Boolean(
        whatsappOptedIn(connection)
        && connection.status === "active"
        && connection.alerts_enabled !== false,
      );
    }

    function renderWhatsApp(connection, recentTests = []) {
      const bound = whatsappBound(connection);
      const optedIn = whatsappOptedIn(connection);
      const active = whatsappEnabled && whatsappActive(connection);
      const stateText = !whatsappEnabled
        ? "Unavailable"
        : active
          ? "Connected"
          : bound && optedIn
            ? "Paused"
            : bound
              ? "Consent required"
              : "Not connected";
      setConnectionBadge(whatsapp.status, stateText, active);
      setConnectionBadge(whatsapp.summaryStatus, stateText, active);
      if (whatsapp.number) whatsapp.number.textContent = bound ? connection.phone : "Not linked";
      if (whatsapp.optIn) whatsapp.optIn.textContent = optedIn ? "Active" : "Required";
      if (whatsapp.delivery) {
        whatsapp.delivery.textContent = connection?.last_delivery_at
          ? new Date(connection.last_delivery_at).toLocaleString()
          : "No delivery recorded";
      }
      const latestTest = safeArray(recentTests).find((item) => item.integration === "whatsapp");
      if (whatsapp.testState) {
        whatsapp.testState.textContent = latestTest
          ? `${titleize(latestTest.status)}${latestTest.created_at ? ` - ${new Date(latestTest.created_at).toLocaleString()}` : ""}`
          : "No test recorded";
      }
      const errorCode = connection?.last_error_code || latestTest?.error_code || "";
      if (whatsapp.error) whatsapp.error.textContent = errorCode ? titleize(errorCode) : "None";
      if (whatsapp.errorRow) whatsapp.errorRow.hidden = !errorCode;
      if (whatsapp.clearError) whatsapp.clearError.hidden = !connection?.last_error_code;
      if (whatsapp.connectForm) whatsapp.connectForm.hidden = !whatsappEnabled || (bound && optedIn);
      if (whatsapp.connectedControls) whatsapp.connectedControls.hidden = !(bound && optedIn);
      if (whatsapp.test) whatsapp.test.disabled = !active;
      if (whatsapp.pause) whatsapp.pause.hidden = !active;
      if (whatsapp.resume) whatsapp.resume.hidden = !(bound && optedIn && !active);
      if (!whatsappPreferencesDirty && bound) {
        const categories = new Set(safeArray(connection.opt_in_categories));
        whatsapp.preferenceCategories.forEach((input) => {
          if (!input.disabled) input.checked = categories.has(input.value);
        });
        if (whatsapp.preferenceLocale && connection.preferred_locale) {
          whatsapp.preferenceLocale.value = connection.preferred_locale;
        }
      }
      if (previousWhatsAppActive === false && active) {
        showToast("WhatsApp connected and verified.");
      }
      previousWhatsAppActive = active;
    }

    function render(payload) {
      renderTelegram(payload?.telegram);
      renderWhatsApp(payload?.whatsapp, payload?.recent_tests);
    }

    async function maybeCompletePendingPublish(payload) {
      if (pendingPublishInFlight || !connectedNotificationChannel(payload)) return;
      const raw = window.localStorage.getItem(pendingMonitorPublishKey);
      if (!raw) return;
      let pending = null;
      try {
        pending = JSON.parse(raw);
      } catch {
        window.localStorage.removeItem(pendingMonitorPublishKey);
        return;
      }
      if (!pending?.strategy_id || !pending?.strategy_version_id) {
        window.localStorage.removeItem(pendingMonitorPublishKey);
        return;
      }
      pendingPublishInFlight = true;
      showToast("Notification channel connected. Activating your monitor...");
      try {
        await publishStrategyVersion(pending.strategy_id, {
          id: pending.strategy_version_id,
          schema_hash: pending.expected_schema_hash,
        });
        window.localStorage.removeItem(pendingMonitorPublishKey);
        showToast("Monitor is active.");
        window.location.href = `/dashboard/monitors?message=monitor_published&t=${Date.now()}`;
      } catch (error) {
        if (!/Telegram|notification channel/i.test(error.message)) {
          window.localStorage.removeItem(pendingMonitorPublishKey);
        }
        showToast(error.message, "error");
      } finally {
        pendingPublishInFlight = false;
      }
    }

    async function poll() {
      if (document.hidden) return;
      try {
        const payload = await api("/integrations");
        render(payload);
        await maybeCompletePendingPublish(payload);
      } catch {
        return;
      }
    }

    poll();
    window.setInterval(poll, 5000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void poll();
    });
    window.addEventListener("focus", () => {
      void poll();
    });
    disconnectButton?.addEventListener("click", async () => {
      if (!window.confirm("Remove the Telegram connection from this dashboard account? Telegram alerts will stop until you link it again.")) {
        return;
      }
      disconnectButton.disabled = true;
      try {
        await api("/integrations/telegram", { method: "DELETE" });
        renderTelegram(null);
        showToast("Telegram connection removed.");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        disconnectButton.disabled = false;
      }
    });

    whatsapp.preferenceCategories.forEach((input) => {
      input.addEventListener("change", () => {
        whatsappPreferencesDirty = true;
      });
    });
    whatsapp.preferenceLocale?.addEventListener("change", () => {
      whatsappPreferencesDirty = true;
    });

    whatsapp.connectForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const categories = whatsapp.categories
        .filter((input) => input.checked && !input.disabled)
        .map((input) => input.value);
      if (!categories.length) {
        showToast("Choose at least one WhatsApp message category.", "error");
        return;
      }
      if (!whatsapp.consent?.checked) {
        showToast("Confirm WhatsApp consent before creating the link.", "error");
        whatsapp.consent?.focus();
        return;
      }
      const submit = whatsapp.connectForm.querySelector("[data-whatsapp-create-link]");
      if (submit) submit.disabled = true;
      try {
        const result = await whatsappApi("/link", {
          method: "POST",
          body: JSON.stringify({
            phone_e164: whatsapp.phone?.value.trim(),
            consent: true,
            categories,
            locale: whatsapp.locale?.value || "en_US",
          }),
        });
        if (whatsapp.linkAnchor) whatsapp.linkAnchor.href = result.link_url;
        if (whatsapp.linkExpiry) {
          whatsapp.linkExpiry.textContent = result.expires_at
            ? `This one-time link expires ${new Date(result.expires_at).toLocaleString()}.`
            : "This one-time link expires shortly.";
        }
        if (whatsapp.linkOutput) {
          whatsapp.linkOutput.hidden = false;
          whatsapp.linkOutput.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
        showToast("Secure link created. Send the prefilled message from WhatsApp.");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        if (submit) submit.disabled = false;
      }
    });

    async function mutateWhatsApp(button, path, successMessage, options = {}) {
      if (button) button.disabled = true;
      try {
        const result = await whatsappApi(path, { method: "POST", ...options });
        renderWhatsApp(result.connection, []);
        showToast(successMessage);
        return result;
      } catch (error) {
        showToast(error.message, "error");
        return null;
      } finally {
        if (button) button.disabled = false;
      }
    }

    whatsapp.test?.addEventListener("click", async () => {
      whatsapp.test.disabled = true;
      try {
        const result = await whatsappApi("/test", { method: "POST" });
        if (whatsapp.testState) whatsapp.testState.textContent = titleize(result.test?.status || "sent");
        showToast("WhatsApp test accepted. Delivery status will update from Meta's webhook.");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        whatsapp.test.disabled = false;
      }
    });
    whatsapp.pause?.addEventListener("click", () => {
      void mutateWhatsApp(whatsapp.pause, "/pause", "WhatsApp alerts paused.");
    });
    whatsapp.resume?.addEventListener("click", () => {
      void mutateWhatsApp(whatsapp.resume, "/resume", "WhatsApp alerts resumed.");
    });
    whatsapp.clearError?.addEventListener("click", () => {
      void mutateWhatsApp(whatsapp.clearError, "/clear-error", "WhatsApp error cleared.");
    });
    whatsapp.save?.addEventListener("click", async () => {
      const categories = whatsapp.preferenceCategories
        .filter((input) => input.checked && !input.disabled)
        .map((input) => input.value);
      if (!categories.length) {
        showToast("Keep at least one WhatsApp message category selected.", "error");
        return;
      }
      whatsapp.save.disabled = true;
      try {
        const result = await whatsappApi("/preferences", {
          method: "PATCH",
          body: JSON.stringify({
            categories,
            locale: whatsapp.preferenceLocale?.value || "en_US",
          }),
        });
        whatsappPreferencesDirty = false;
        renderWhatsApp(result.connection, []);
        showToast("WhatsApp categories updated.");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        whatsapp.save.disabled = false;
      }
    });
    whatsapp.disconnect?.addEventListener("click", async () => {
      if (!window.confirm("Disconnect WhatsApp from this account? Unsent WhatsApp alerts will be canceled, and reconnecting will require fresh consent.")) return;
      whatsapp.disconnect.disabled = true;
      try {
        const result = await whatsappApi("/connection", { method: "DELETE" });
        whatsappPreferencesDirty = false;
        renderWhatsApp(result.connection, []);
        if (whatsapp.phone) whatsapp.phone.value = "";
        if (whatsapp.consent) whatsapp.consent.checked = false;
        whatsapp.categories.forEach((input) => { input.checked = false; });
        if (whatsapp.linkOutput) whatsapp.linkOutput.hidden = true;
        showToast("WhatsApp disconnected.");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        whatsapp.disconnect.disabled = false;
      }
    });
  }

  function initInboxFilter() {
    const filter = document.querySelector("[data-inbox-filter]");
    if (!filter) return;
    const items = Array.from(document.querySelectorAll("[data-inbox-item]"));
    const empty = document.createElement("div");
    empty.className = "dash-empty inbox-filter-empty";
    empty.hidden = true;
    empty.textContent = "No review cards match this category.";
    document.querySelector(".inbox-list")?.after(empty);
    function applyFilter() {
      const selected = filter.value;
      let visible = 0;
      items.forEach((item) => {
        const match = !selected || item.dataset.itemType === selected;
        item.hidden = !match;
        if (match) visible += 1;
      });
      empty.hidden = visible > 0;
    }
    filter.addEventListener("change", applyFilter);
    applyFilter();
  }

  function initOverviewChannelStatus() {
    const rootElement = document.querySelector("[data-overview-channel-status]");
    if (!rootElement) return;
    const buttons = {
      telegram: rootElement.querySelector('[data-overview-channel="telegram"]'),
    };

    function updateButton(channel, connected) {
      const button = buttons[channel];
      if (!button) return;
      button.classList.toggle("connected", connected);
      const detail = button.querySelector("small");
      const badge = button.querySelector(".badge");
      if (detail) detail.textContent = connected ? "Connected" : "Not connected";
      if (badge) {
        badge.textContent = connected ? "Ready" : "Set up";
        badge.classList.toggle("badge-eligible", connected);
        badge.classList.toggle("badge-neutral", !connected);
      }
    }

    async function poll() {
      if (document.hidden) return;
      try {
        const payload = await api("/integrations");
        updateButton("telegram", connectedNotificationChannel({ telegram: payload.telegram }));
      } catch {
        return;
      }
    }

    poll();
    window.setInterval(poll, 5000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void poll();
    });
    window.addEventListener("focus", () => {
      void poll();
    });
  }

  function initVerifiedStrategyWorkspace() {
    const workspace = document.querySelector("[data-verified-workspace]");
    if (!workspace) return;
    const strategyId = workspace.dataset.strategyId;
    let versionId = workspace.dataset.versionId;
    const content = workspace.querySelector("[data-verified-content]");
    const loading = workspace.querySelector("[data-verified-loading]");
    const notice = workspace.querySelector("[data-verified-notice]");
    let state = null;
    let contractCache = null;
    let historyTab = "matches";

    const setNotice = (message, tone = "") => {
      if (!notice) return;
      notice.textContent = message;
      notice.className = `verified-notice ${tone}`.trim();
    };

    const setBusy = (button, busy, label = "Working...") => {
      if (!button) return;
      if (busy) {
        button.dataset.originalLabel = button.textContent;
        button.textContent = label;
        button.disabled = true;
      } else {
        button.textContent = button.dataset.originalLabel || button.textContent;
        button.disabled = false;
      }
    };

    const readable = (value) => String(value || "unknown").replaceAll("_", " ");
    const dateValue = (value) => {
      if (!value) return "Not recorded";
      const parsed = new Date(value);
      return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
    };
    const toIso = (value) => {
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) throw new Error("Choose a valid date and time.");
      return parsed.toISOString();
    };

    function renderInterpretation(items, verification) {
      const list = workspace.querySelector("[data-interpretation-list]");
      const status = workspace.querySelector("[data-interpretation-state]");
      if (status) status.textContent = readable(verification.interpretation_status);
      if (!list) return;
      if (!items.length) {
        list.innerHTML = '<div class="verified-empty">No phrase-to-rule mapping is available.</div>';
        return;
      }
      list.innerHTML = items.map((item) => {
        const mechanics = safeArray(item.mechanics?.rules).map((rule) => `
          <div><span>Rule</span><strong>${escapeHtml(rule.interpretation)}</strong></div>
          <div><span>Timeframe</span><strong>${escapeHtml(rule.timeframe || "Inherited")}</strong></div>
          <div><span>Operator</span><strong>${escapeHtml(readable(rule.operator))}</strong></div>
          <div><span>Threshold</span><strong>${escapeHtml(rule.threshold ?? "Rule-defined")}</strong></div>
          <div><span>Data source</span><strong>${escapeHtml(rule.data_source)}</strong></div>
          <div><span>Candle close</span><strong>${rule.candle_close_required ? "Required" : "Intrabar allowed"}</strong></div>
        `).join("");
        const assumptions = safeArray(item.assumptions).length
          ? `<p><strong>Assumption:</strong> ${safeArray(item.assumptions).map(escapeHtml).join("; ")}</p>`
          : "";
        let action = "";
        if (item.resolution_status === "unresolved" && item.status === "assumed") {
          action = `<button class="button button-secondary" type="button" data-accept-statement="${escapeHtml(item.id)}">Accept assumption</button>`;
        } else if (item.resolution_status === "unresolved" && item.status === "ambiguous") {
          action = `<label>Clarification<input data-statement-answer="${escapeHtml(item.id)}" placeholder="Explain what this phrase means"></label><button class="button button-secondary" type="button" data-answer-statement="${escapeHtml(item.id)}">Answer</button>`;
        } else if (["unsupported", "contradictory"].includes(item.status)) {
          action = `<a class="button button-secondary" href="/dashboard/create-monitor?monitor=${strategyId}">Edit or remove instruction</a>`;
        }
        return `<article class="interpretation-rule-card" data-status="${escapeHtml(item.status)}">
          <div class="interpretation-card-head"><span class="interpretation-status">${escapeHtml(readable(item.status))}</span><small>${escapeHtml(readable(item.resolution_status))}</small></div>
          <p class="interpretation-phrase">“${escapeHtml(item.original_phrase)}”</p>
          <p>${escapeHtml(item.structured_interpretation)}</p>
          ${assumptions}<div class="mechanic-grid">${mechanics}</div><div class="button-row">${action}</div>
        </article>`;
      }).join("");
    }

    function renderTests(items) {
      const list = workspace.querySelector("[data-test-list]");
      if (!list) return;
      if (!items.length) {
        list.innerHTML = '<div class="verified-empty">No saved examples yet. Add one moment that should trigger and one that should not.</div>';
        return;
      }
      list.innerHTML = items.map((item) => {
        const run = item.latest_run;
        const conditions = safeArray(run?.condition_results).map((condition) => `
          <div><span>${escapeHtml(condition.name || condition.condition_id || "Condition")}</span><strong>${escapeHtml(readable(condition.state))}</strong><small>Actual: ${escapeHtml(condition.actual_value ?? "Unavailable")} · Required: ${escapeHtml(condition.required_value ?? "Rule-defined")}</small></div>
        `).join("");
        return `<article class="verified-test-card">
          <div class="verified-test-head"><strong>${escapeHtml(item.title)}</strong><span class="test-result-badge">${escapeHtml(readable(run?.status || "not_run"))}</span></div>
          <small>${escapeHtml(item.symbol)} · ${escapeHtml(item.timeframe)} · ${dateValue(item.evaluation_time)} · ${escapeHtml(readable(item.expected_result))}</small>
          ${run?.mismatch_reason ? `<p>${escapeHtml(run.mismatch_reason)}</p>` : ""}
          <details><summary>Condition QA report</summary><div class="verified-metric-row">${conditions || "No retained condition values."}</div></details>
          <button class="button button-secondary" type="button" data-rerun-test="${escapeHtml(item.id)}">Rerun</button>
        </article>`;
      }).join("");
    }

    function renderHistory(summary) {
      const metrics = workspace.querySelector("[data-history-summary]");
      if (!metrics) return;
      if (!summary || !summary.evaluations) {
        metrics.innerHTML = '<div class="verified-empty">Historical validation has not run for this version.</div>';
      } else {
        metrics.innerHTML = [
          ["Evaluations", summary.evaluations], ["Matches", summary.matches],
          ["Near matches", summary.near_matches], ["Invalidated", summary.invalidated],
          ["Non-matches", summary.non_matches], ["Breadth", readable(summary.breadth)],
          ["Main blocker", summary.most_common_failed_condition || "No repeated blocker"],
          ["Estimated weekly alerts", summary.estimated_frequency?.per_week ?? "Insufficient sample"],
        ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      }
      renderHistoryChart(summary);
      renderHistoryExamples(summary);
    }

    function renderHistoryChart(summary) {
      const target = workspace.querySelector("[data-history-chart]");
      if (!target) return;
      const candles = safeArray(summary?.chart?.candles).slice(-140);
      const closes = candles.map((item) => Number(item.close)).filter(Number.isFinite);
      if (closes.length < 2) {
        target.innerHTML = '<div class="verified-empty">A chart will appear after historical candle evidence is available.</div>';
        return;
      }
      const low = Math.min(...closes);
      const high = Math.max(...closes);
      const spread = high - low || 1;
      const points = closes.map((value, index) => {
        const x = (index / Math.max(1, closes.length - 1)) * 100;
        const y = 38 - ((value - low) / spread) * 34;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      }).join(" ");
      const markers = safeArray(summary?.chart?.markers).slice(-8);
      target.innerHTML = `<div class="historical-chart-head"><strong>${escapeHtml(summary.chart.symbol || "Historical market")}</strong><span>${escapeHtml(summary.chart.timeframe || "")}</span></div><svg viewBox="0 0 100 42" role="img" aria-label="Historical close-price path for retained validation candles"><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke"></polyline></svg><div class="historical-chart-markers">${markers.map((item) => `<span>${escapeHtml(readable(item.outcome))} · ${escapeHtml(item.score)}% · ${dateValue(item.time)}</span>`).join("") || "<span>No match markers in the selected window.</span>"}</div>`;
    }

    function renderHistoryExamples(summary) {
      const target = workspace.querySelector("[data-history-examples]");
      if (!target) return;
      const examples = safeArray(summary?.examples_by_outcome?.[historyTab]);
      target.innerHTML = examples.length ? examples.map((item) => `
        <article class="historical-example"><div class="verified-test-head"><strong>${escapeHtml(item.symbol)}</strong><span class="test-result-badge">${escapeHtml(readable(item.outcome))}</span></div><small>${dateValue(item.timestamp)} · ${escapeHtml(item.score)}% condition completion</small><p>${item.primary_blocker ? `Blocked by ${escapeHtml(item.primary_blocker)}.` : "All required monitored conditions passed."}</p><details><summary>Rule values</summary><div class="verified-metric-row">${safeArray(item.conditions).map((condition) => `<div><span>${escapeHtml(condition.name)}</span><strong>${escapeHtml(readable(condition.state))}</strong><small>${escapeHtml(condition.actual_value ?? "Unavailable")} / ${escapeHtml(condition.required_value ?? "Rule-defined")}</small></div>`).join("")}</div></details></article>
      `).join("") : `<div class="verified-empty">No retained ${escapeHtml(readable(historyTab))} examples in this preview.</div>`;
    }

    function renderQuality(report) {
      const target = workspace.querySelector("[data-quality-dimensions]");
      const compatible = workspace.querySelector("[data-monitor-compatible]");
      if (compatible) compatible.textContent = report?.monitor_compatible ? "Ready for monitoring" : "Review required";
      if (target) {
        target.innerHTML = Object.entries(report?.dimensions || {}).map(([key, item]) => `
          <article class="quality-dimension" data-status="${escapeHtml(item.status)}"><strong>${escapeHtml(readable(key))}</strong><span>${escapeHtml(readable(item.status))}</span><p>${escapeHtml(item.explanation)}</p></article>
        `).join("") || '<div class="verified-empty">Quality dimensions are not available.</div>';
      }
      const findings = workspace.querySelector("[data-quality-findings]");
      if (findings) {
        const influence = report?.condition_influence || {};
        const influenceCopy = influence.evidence_available
          ? `<div class="quality-influence"><p><span>Most influential blocker</span><strong>${escapeHtml(influence.most?.condition || "Unavailable")}</strong><small>${escapeHtml(influence.most?.failures ?? 0)} failures across ${escapeHtml(influence.most?.evaluations ?? 0)} evaluations</small></p><p><span>Least influential in this sample</span><strong>${escapeHtml(influence.least?.condition || "Unavailable")}</strong><small>${escapeHtml(influence.least?.failures ?? 0)} failures across ${escapeHtml(influence.least?.evaluations ?? 0)} evaluations</small></p><small>${escapeHtml(influence.method)}</small></div>`
          : `<p>${escapeHtml(influence.method || "Run historical validation to measure condition influence.")}</p>`;
        const risks = safeArray(report?.remaining_risks);
        findings.innerHTML = `${influenceCopy}<h3>Remaining risks</h3>${risks.length ? risks.map((item) => `<p>${escapeHtml(item)}</p>`).join("") : "<p>No deterministic validation risks are currently recorded.</p>"}`;
      }
    }

    function renderVersions(items, current, diff) {
      const target = workspace.querySelector("[data-version-list]");
      if (target) {
        target.innerHTML = safeArray(items).map((item) => `
          <article class="verified-version-card ${item.id === current.id ? "current" : ""}"><div class="verified-version-row"><strong>Version ${escapeHtml(item.number)}</strong><span class="verified-state">${escapeHtml(readable(item.status))}</span>${item.active ? '<span class="verified-state">Active</span>' : ""}</div><p>${escapeHtml(item.change_summary || "Initial strategy version.")}</p><small>${dateValue(item.created_at)} · ${escapeHtml(String(item.schema_hash || "").slice(0, 12))}</small>${item.id !== current.id ? `<button class="button button-secondary" type="button" data-restore-version="${escapeHtml(item.id)}">Restore as new draft</button>` : ""}</article>
        `).join("");
      }
      const diffTarget = workspace.querySelector("[data-semantic-diff]");
      if (diffTarget) {
        diffTarget.innerHTML = safeArray(diff).length
          ? `<h3>Changes in Version ${escapeHtml(current.number)}</h3>${safeArray(diff).map((item) => `<p><strong>${escapeHtml(readable(item.path))}</strong>: ${escapeHtml(item.before ?? "not present")} → ${escapeHtml(item.after ?? "removed")}</p>`).join("")}`
          : "<p>This is the first version, or its mechanics match the parent version.</p>";
      }
    }

    function renderHealth(health) {
      const target = workspace.querySelector("[data-verified-health]");
      if (!target) return;
      target.innerHTML = `<div class="verified-metric-row"><div><span>Technical health</span><strong>${escapeHtml(readable(health?.technical))}</strong></div><div><span>Strategy health</span><strong>${escapeHtml(readable(health?.strategy))}</strong></div></div>${safeArray(health?.causes).map((cause) => `<p>${escapeHtml(cause.message || cause)}</p>`).join("") || "<p>More scan history may be needed for a detailed health cause.</p>"}`;
    }

    function outcomePath(points) {
      const closes = safeArray(points).map((item) => Number(item.close)).filter(Number.isFinite);
      if (closes.length < 2) return "";
      const low = Math.min(...closes);
      const high = Math.max(...closes);
      const spread = high - low || 1;
      const coordinates = closes.map((value, index) => {
        const x = (index / Math.max(1, closes.length - 1)) * 100;
        const y = 28 - ((value - low) / spread) * 24;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      }).join(" ");
      return `<svg class="outcome-price-path" viewBox="0 0 100 32" role="img" aria-label="Retained price path over the selected review horizon"><polyline points="${coordinates}" fill="none" stroke="currentColor" stroke-width="1.6" vector-effect="non-scaling-stroke"></polyline></svg>`;
    }

    function renderOutcomes(items) {
      const target = workspace.querySelector("[data-outcome-list]");
      if (!target) return;
      if (!items.length) {
        target.innerHTML = '<div class="verified-empty">No confirmed alerts are ready for outcome review. Historical preview results are not treated as live outcomes.</div>';
        return;
      }
      target.innerHTML = items.map((item) => {
        const latest = safeArray(item.reviews)[0];
        const metrics = latest?.outcome_metrics || {};
        const path = latest ? outcomePath(latest.price_path) : "";
        const reviewSummary = latest
          ? `<p>User result: <strong>${escapeHtml(readable(latest.classification))}</strong> after ${escapeHtml(latest.horizon_minutes)} minutes.</p><div class="verified-metric-row"><div><span>Market path</span><strong>${metrics.evidence_available ? `${escapeHtml(metrics.change_percent)}%` : "Unavailable"}</strong></div><div><span>Classification source</span><strong>User</strong></div></div>${path}${safeArray(latest.tags).length ? `<p class="outcome-tags">${safeArray(latest.tags).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</p>` : ""}`
          : "<p>This alert has not been reviewed at an outcome horizon.</p>";
        return `<article class="outcome-review-card"><div class="verified-test-head"><strong>${escapeHtml(item.symbol || item.title)}</strong><span class="verified-state">Version ${escapeHtml(item.strategy_version)}</span></div><small>${escapeHtml(item.exchange || "unknown")} &middot; ${escapeHtml(item.timeframe || "unknown")} &middot; ${dateValue(item.confirmed_at)}</small>${reviewSummary}<form class="verified-form compact" data-outcome-form="${escapeHtml(item.alert_id)}"><label>Horizon<select name="horizon_minutes" data-outcome-horizon><option value="60">1 hour</option><option value="240">4 hours</option><option value="1440">24 hours</option><option value="10080">7 days</option><option value="custom">Custom</option></select></label><label data-custom-horizon hidden>Custom minutes<input name="custom_horizon_minutes" type="number" min="1" max="525600" inputmode="numeric"></label><label>Your result<select name="classification"><option value="positive">Positive</option><option value="negative">Negative</option><option value="neutral">Neutral</option><option value="invalid">Invalid or irrelevant</option></select></label><label class="verified-form-wide">Your definition or notes<textarea name="notes" rows="2" placeholder="Explain what this outcome means to you"></textarea></label><label class="verified-form-wide">Tags<input name="tags" maxlength="300" placeholder="reviewed, trend, unusual data"></label><button class="button button-primary verified-form-wide" type="submit">Review outcome</button></form><a href="${escapeHtml(item.proof_url)}" target="_blank" rel="noopener">View immutable alert proof</a></article>`;
      }).join("");
    }

    function renderSuggestion(suggestion) {
      const target = workspace.querySelector("[data-improvement-result]");
      if (!target) return;
      const effect = suggestion.historical_effect || {};
      target.innerHTML = `<article class="improvement-card"><span class="verified-state">${escapeHtml(readable(suggestion.confidence))} evidence confidence</span><h3>${escapeHtml(readable(suggestion.action))}</h3><p>${escapeHtml(suggestion.reason)}</p><div class="verified-metric-row"><div><span>Reviewed outcomes</span><strong>${escapeHtml(suggestion.outcome_evidence?.sample_count ?? 0)}</strong></div><div><span>Historical effect</span><strong>${escapeHtml(readable(effect.status))}</strong></div><div><span>Alerts retained</span><strong>${escapeHtml(effect.alerts_retained ?? "Run preview first")}</strong></div><div><span>Alerts removed</span><strong>${escapeHtml(effect.alerts_removed ?? "Run preview first")}</strong></div><div><span>Strong outcomes lost</span><strong>${escapeHtml(effect.strong_outcomes_lost ?? "Insufficient linked evidence")}</strong></div><div><span>Weak outcomes removed</span><strong>${escapeHtml(effect.weak_outcomes_removed ?? "Insufficient linked evidence")}</strong></div></div>${safeArray(suggestion.limitations).map((item) => `<p>${escapeHtml(item)}</p>`).join("")}<div class="button-row"><button class="button button-primary" type="button" data-apply-suggestion="${escapeHtml(suggestion.id)}">Test this change as a draft</button><button class="button button-secondary" type="button" data-dismiss-suggestion>Dismiss</button></div></article>`;
    }

    function renderWorkspace(payload) {
      state = payload;
      versionId = payload.version.id;
      renderInterpretation(safeArray(payload.interpretation), payload.verification || {});
      renderTests(safeArray(payload.test_cases));
      renderHistory(payload.verification?.historical_summary || {});
      renderQuality(payload.verification?.quality_report || {});
      renderVersions(payload.versions, payload.version, payload.verification?.semantic_diff);
      renderHealth(payload.health || {});
      const blockers = safeArray(payload.activation_blockers);
      const approvalBlockers = blockers.filter((item) => item.code !== "interpretation_review");
      const approve = workspace.querySelector("[data-approve-version]");
      const activate = workspace.querySelector("[data-activate-version]");
      if (approve) {
        approve.hidden = Boolean(payload.version.approved_at);
        approve.disabled = approvalBlockers.length > 0;
      }
      if (activate) {
        activate.hidden = Boolean(payload.version.active);
        activate.disabled = blockers.length > 0 || !payload.version.approved_at;
      }
      setNotice(
        blockers.length
          ? blockers.map((item) => item.message).join(" ")
          : "This version has no verification blocker. You remain in control of approval and activation.",
        blockers.length ? "warning" : "success",
      );
    }

    async function loadWorkspace() {
      try {
        const payload = await api(`/strategies/${strategyId}/verification?version_id=${versionId}`);
        renderWorkspace(payload);
        const outcomes = await api(`/strategies/${strategyId}/outcomes`);
        renderOutcomes(safeArray(outcomes.items));
        if (loading) loading.hidden = true;
        if (content) content.hidden = false;
      } catch (error) {
        if (loading) loading.hidden = true;
        setNotice(error.message, "error");
      }
    }

    workspace.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      try {
        if (button.matches("[data-toggle-test-form]")) {
          workspace.querySelector("[data-test-form]").hidden = false;
          workspace.querySelector("[data-test-form] input")?.focus();
        } else if (button.matches("[data-cancel-test]")) {
          workspace.querySelector("[data-test-form]").hidden = true;
        } else if (button.matches("[data-toggle-history-form]")) {
          workspace.querySelector("[data-history-form]").hidden = false;
        } else if (button.matches("[data-accept-statement]")) {
          setBusy(button, true, "Accepting...");
          await api(`/strategies/${strategyId}/interpretation/${button.dataset.acceptStatement}/resolve`, { method: "POST", body: JSON.stringify({ action: "accept" }) });
          await loadWorkspace();
        } else if (button.matches("[data-answer-statement]")) {
          const id = button.dataset.answerStatement;
          const input = workspace.querySelector(`[data-statement-answer="${CSS.escape(id)}"]`);
          if (!input?.value.trim()) throw new Error("Enter your clarification first.");
          setBusy(button, true, "Saving...");
          await api(`/strategies/${strategyId}/interpretation/${id}/resolve`, { method: "POST", body: JSON.stringify({ action: "answer", resolution_text: input.value.trim() }) });
          await loadWorkspace();
        } else if (button.matches("[data-rerun-test]")) {
          setBusy(button, true, "Running...");
          await api(`/strategies/${strategyId}/tests/${button.dataset.rerunTest}/run?version_id=${versionId}`, { method: "POST", body: "{}" });
          await loadWorkspace();
        } else if (button.matches("[data-history-tab]")) {
          historyTab = button.dataset.historyTab;
          workspace.querySelectorAll("[data-history-tab]").forEach((item) => item.setAttribute("aria-selected", String(item === button)));
          renderHistoryExamples(state?.verification?.historical_summary || {});
        } else if (button.matches("[data-restore-version]")) {
          if (!window.confirm("Restore this version as a new editable draft? The live version will not change.")) return;
          setBusy(button, true, "Restoring...");
          const result = await api(`/strategies/${strategyId}/versions/${button.dataset.restoreVersion}/restore`, { method: "POST", body: "{}" });
          window.location.assign(`/dashboard/strategies/${strategyId}/verify?version=${result.version.id}`);
        } else if (button.matches("[data-compare-versions]")) {
          const versions = safeArray(state?.versions);
          if (versions.length < 2) throw new Error("Create a second version before comparing behavior.");
          setBusy(button, true, "Comparing...");
          const comparison = await api("/strategies/compare", { method: "POST", body: JSON.stringify({ left_version_id: versions[1].id, right_version_id: versions[0].id }) });
          const leftBehavior = comparison.behavior?.left || {};
          const rightBehavior = comparison.behavior?.right || {};
          const leftVerification = comparison.verification_effects?.left || {};
          const rightVerification = comparison.verification_effects?.right || {};
          const behaviorSummary = `<div class="verified-metric-row"><div><span>Confirmed matches</span><strong>${escapeHtml(leftBehavior.confirmed_matches ?? 0)} → ${escapeHtml(rightBehavior.confirmed_matches ?? 0)}</strong></div><div><span>Historical matches</span><strong>${escapeHtml(leftVerification.historical_matches ?? 0)} → ${escapeHtml(rightVerification.historical_matches ?? 0)}</strong></div><div><span>Saved-test status</span><strong>${escapeHtml(readable(leftVerification.test_status))} → ${escapeHtml(readable(rightVerification.test_status))}</strong></div><div><span>Changed test results</span><strong>${escapeHtml(safeArray(rightVerification.changed_test_results).length)}</strong></div></div>${safeArray(comparison.behavior?.comparison_notes).map((item) => `<p>${escapeHtml(item)}</p>`).join("")}`;
          workspace.querySelector("[data-semantic-diff]").innerHTML = `<h3>Version ${escapeHtml(comparison.left.version_number || versions[1].number)} to Version ${escapeHtml(comparison.right.version_number || versions[0].number)}</h3>${safeArray(comparison.diff).map((item) => `<p>${escapeHtml(item.label || item.field || "Rule change")}: ${escapeHtml(item.before ?? "not present")} → ${escapeHtml(item.after ?? "removed")}</p>`).join("") || "<p>No structured rule difference was found.</p>"}${behaviorSummary}`;
          setBusy(button, false);
        } else if (button.matches("[data-save-draft]")) {
          setBusy(button, true, "Saving...");
          await api(`/strategies/${strategyId}/versions/${versionId}/save-draft`, {
            method: "POST",
            body: "{}",
          });
          showToast("Draft saved and recorded in the strategy audit trail.");
          setBusy(button, false);
        } else if (button.matches("[data-approve-version]")) {
          setBusy(button, true, "Approving...");
          await api(`/strategies/${strategyId}/approve`, { method: "POST", body: JSON.stringify({ strategy_version_id: versionId, expected_schema_hash: state.version.schema_hash }) });
          showToast("Exact visible version and its interpretation approved. No monitor was activated.");
          await loadWorkspace();
        } else if (button.matches("[data-activate-version]")) {
          if (!window.confirm("Activate this exact approved strategy version for continuous monitoring?")) return;
          setBusy(button, true, "Activating...");
          try {
            await publishStrategyVersion(strategyId, state.version);
            showToast("Monitor activated with the reviewed strategy version.");
            window.setTimeout(() => window.location.assign("/dashboard/opportunities"), 500);
          } catch (error) {
            if (error.message.includes("Connect Telegram")) {
              savePendingMonitorPublish(strategyId, state.version);
              window.location.assign(pendingMonitorPublishUrl());
              return;
            }
            throw error;
          }
        } else if (button.matches("[data-improvement]")) {
          setBusy(button, true, "Analysing evidence...");
          const suggestion = await api(`/cockpit/strategies/${strategyId}/suggestions`, {
            method: "POST",
            body: JSON.stringify({ action: button.dataset.improvement }),
          });
          renderSuggestion(suggestion);
          setBusy(button, false);
        } else if (button.matches("[data-apply-suggestion]")) {
          if (!window.confirm("Create and test this as a new draft version? The active monitor will not change.")) return;
          setBusy(button, true, "Creating and testing draft...");
          const result = await api(`/cockpit/suggestions/${button.dataset.applySuggestion}/apply`, {
            method: "POST",
            body: "{}",
          });
          showToast("Suggested change was tested and saved as a draft. Review it before approval.");
          window.location.assign(`/dashboard/strategies/${strategyId}/verify?version=${result.draft_version.id}`);
        } else if (button.matches("[data-dismiss-suggestion]")) {
          workspace.querySelector("[data-improvement-result]").innerHTML = "";
        } else if (button.matches("[data-export-contract], [data-copy-contract]")) {
          contractCache = contractCache || await api(`/strategies/${strategyId}/versions/${versionId}/contract`);
          if (button.matches("[data-copy-contract]")) {
            const text = `${contractCache.strategy.name} · Version ${contractCache.version.number}\n${contractCache.notice}\nIntegrity: ${contractCache.integrity_hash}`;
            await navigator.clipboard.writeText(text);
            showToast("Strategy contract summary copied.");
          } else {
            const blob = new Blob([JSON.stringify(contractCache, null, 2)], { type: "application/json" });
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = `${contractCache.strategy.name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}-v${contractCache.version.number}-contract.json`;
            link.click();
            URL.revokeObjectURL(link.href);
          }
          workspace.querySelector("[data-contract-summary]").innerHTML = `<div class="verified-metric-row"><div><span>Version</span><strong>${escapeHtml(contractCache.version.number)}</strong></div><div><span>Integrity hash</span><strong>${escapeHtml(contractCache.integrity_hash.slice(0, 16))}...</strong></div><div><span>Saved examples</span><strong>${escapeHtml(contractCache.unit_tests.length)}</strong></div></div>`;
        }
      } catch (error) {
        setBusy(button, false);
        setNotice(error.message, "error");
      }
    });

    workspace.querySelector("[data-test-form]")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const submit = form.querySelector("button[type='submit']");
      const values = Object.fromEntries(new FormData(form));
      try {
        setBusy(submit, true, "Running example...");
        values.evaluation_time = toIso(values.evaluation_time);
        await api(`/strategies/${strategyId}/tests?version_id=${versionId}`, { method: "POST", body: JSON.stringify(values) });
        form.reset(); form.hidden = true;
        showToast("Test case saved and run against the selected historical moment.");
        await loadWorkspace();
      } catch (error) { setNotice(error.message, "error"); setBusy(submit, false); }
    });

    workspace.querySelector("[data-history-form]")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const submit = form.querySelector("button[type='submit']");
      const values = Object.fromEntries(new FormData(form));
      try {
        setBusy(submit, true, "Evaluating history...");
        values.symbols = values.symbols.split(",").map((item) => item.trim()).filter(Boolean);
        values.started_at = toIso(values.started_at); values.ended_at = toIso(values.ended_at);
        const result = await api(`/strategies/${strategyId}/versions/${versionId}/historical-validation`, { method: "POST", body: JSON.stringify(values) });
        form.hidden = true;
        showToast("Historical preview completed with deterministic evaluator results.");
        await loadWorkspace();
        renderHistory(result.summary);
      } catch (error) { setNotice(error.message, "error"); setBusy(submit, false); }
    });

    workspace.querySelector("[data-forensic-form]")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const submit = form.querySelector("button[type='submit']");
      const values = Object.fromEntries(new FormData(form));
      try {
        setBusy(submit, true, "Reconstructing...");
        values.strategy_id = strategyId; values.requested_time = toIso(values.requested_time);
        const result = await api("/forensic-investigations", { method: "POST", body: JSON.stringify(values) });
        workspace.querySelector("[data-forensic-result]").innerHTML = `<article class="forensic-conclusion"><span class="verified-state">${escapeHtml(readable(result.primary_category))}</span><h3>${escapeHtml(result.conclusion)}</h3><p>Evidence: ${escapeHtml(readable(result.evidence_availability))}</p><div class="verified-metric-row">${safeArray(result.rule_results).map((item) => `<div><span>${escapeHtml(item.condition_key)}</span><strong>${escapeHtml(readable(item.status))}</strong><small>Actual: ${escapeHtml(item.actual ?? "Unavailable")} · Required: ${escapeHtml(item.required ?? "Rule-defined")}</small></div>`).join("") || "No retained condition snapshot was available."}</div></article>`;
        setBusy(submit, false);
      } catch (error) { setNotice(error.message, "error"); setBusy(submit, false); }
    });

    workspace.addEventListener("change", (event) => {
      const select = event.target.closest("[data-outcome-horizon]");
      if (!select) return;
      const custom = select.closest("form")?.querySelector("[data-custom-horizon]");
      if (!custom) return;
      custom.hidden = select.value !== "custom";
      const input = custom.querySelector("input");
      if (input) input.required = select.value === "custom";
    });

    workspace.addEventListener("submit", async (event) => {
      const form = event.target.closest("[data-outcome-form]");
      if (!form) return;
      event.preventDefault();
      const submit = form.querySelector("button[type='submit']");
      const values = Object.fromEntries(new FormData(form));
      values.horizon_minutes = Number(
        values.horizon_minutes === "custom"
          ? values.custom_horizon_minutes
          : values.horizon_minutes,
      );
      if (!Number.isInteger(values.horizon_minutes) || values.horizon_minutes < 1) {
        setNotice("Enter a valid custom review horizon in minutes.", "error");
        return;
      }
      delete values.custom_horizon_minutes;
      values.classification_rules = {
        definition: values.notes || "Classification selected by the user.",
      };
      values.tags = String(values.tags || "").split(",").map((item) => item.trim()).filter(Boolean).slice(0, 20);
      try {
        setBusy(submit, true, "Saving review...");
        await api(`/alerts/${form.dataset.outcomeForm}/outcomes`, {
          method: "POST",
          body: JSON.stringify(values),
        });
        showToast("Outcome saved with its real market path and user-defined label.");
        const outcomes = await api(`/strategies/${strategyId}/outcomes`);
        renderOutcomes(safeArray(outcomes.items));
      } catch (error) {
        setNotice(error.message, "error");
        setBusy(submit, false);
      }
    });

    const now = new Date();
    const monthAgo = new Date(now.getTime() - 30 * 86400000);
    const localInput = (date) => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    const historyForm = workspace.querySelector("[data-history-form]");
    if (historyForm) { historyForm.elements.started_at.value = localInput(monthAgo); historyForm.elements.ended_at.value = localInput(now); }
    const forensicForm = workspace.querySelector("[data-forensic-form]");
    if (forensicForm) forensicForm.elements.requested_time.value = localInput(now);
    void loadWorkspace();
  }

  function initAlertProofReceipt() {
    const root = document.querySelector("[data-alert-proof]");
    const source = root?.querySelector("[data-alert-proof-json]");
    if (!root || !source) return;
    let payload = null;
    try {
      payload = JSON.parse(source.textContent || "{}");
    } catch (_error) {
      return;
    }
    root.querySelector("[data-copy-alert-proof]")?.addEventListener("click", async () => {
      const proof = payload.proof || {};
      const conditions = safeArray(proof.conditions)
        .map((item) => `${item.name || item.condition_id || "Condition"}: ${item.state || "unknown"}`)
        .join("\n");
      const summary = [
        `${proof.strategy_name || "Monitor"} - Version ${proof.strategy_version || "n/a"}`,
        `${proof.symbol || "Market"} ${proof.timeframe || ""}`.trim(),
        conditions,
        `Proof integrity: ${payload.proof_hash}`,
      ].filter(Boolean).join("\n");
      await navigator.clipboard.writeText(summary);
      showToast("Immutable proof summary copied.");
    });
    root.querySelector("[data-export-alert-proof]")?.addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `hilalmarkets-alert-proof-${payload.alert_id}.json`;
      link.click();
      URL.revokeObjectURL(link.href);
    });
  }

  function initWebNotifications() {
    let stack = document.getElementById("web-notification-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "web-notification-stack";
      stack.className = "web-notification-stack";
      document.body.appendChild(stack);
    }

    function pushNotification(item) {
      const card = document.createElement("article");
      card.className = "web-notification-popup";
      const completion =
        item.completion_rate === null || item.completion_rate === undefined
          ? ""
          : `<span>${escapeHtml(item.completion_rate)}%</span>`;
      card.innerHTML = `
        <button type="button" aria-label="Dismiss notification">x</button>
        <strong>${escapeHtml(item.title || "Market update")}</strong>
        <p>${escapeHtml(item.symbol || "Market")}</p>
        ${completion}
      `;
      card.querySelector("button")?.addEventListener("click", () => card.remove());
      stack.prepend(card);
      window.setTimeout(() => card.remove(), 9000);
    }

    async function poll() {
      if (document.hidden) return;
      try {
        const payload = await api("/notifications/web");
        safeArray(payload.items).reverse().forEach(pushNotification);
      } catch {
        return;
      }
    }

    poll();
    window.setInterval(poll, 15000);
  }

  // `initSidebar` used to live here. It was the second of three controllers for one side
  // menu: it wrote the same `sidebar-collapsed` class on `<body>` that `hilalmarkets.js`
  // wrote, but read a different stored value (`amm-sidebar-collapsed`), and it pointed at
  // `[data-sidebar-toggle]` and `.dash-nav`, neither of which any shipped template has
  // carried for a long time. So it did nothing a person asked for and one thing nobody
  // asked for: on every page load it re-decided whether the menu was minimized, from a
  // key the button never wrote. The menu has one owner now, `hm-shell.js`.

  // Every dashboard feature used to start inside one handler, one after another. A single
  // failure in any of them stopped the whole list, so an unrelated bug silently disabled
  // every feature below it — most visibly the Opportunities panels, which then sat in
  // their loading state for ever with nothing to say why. Each part now starts on its
  // own: a failure is reported by name and the rest of the page still works.
  function startFeature(name, start) {
    try {
      const result = start();
      if (result && typeof result.catch === "function") {
        result.catch((error) => console.error(`[dashboard] ${name} failed`, error));
      }
    } catch (error) {
      console.error(`[dashboard] ${name} failed`, error);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const features = {
      initExports,
      initSupport,
      initChart,
      initLifecycles,
      initSettings,
      initReferralCopy,
      initIntegrations,
      initOverviewChannelStatus,
      initInboxFilter,
      initVerifiedStrategyWorkspace,
      initAlertProofReceipt,
    };
    for (const [name, start] of Object.entries(features)) startFeature(name, start);
  });
})();
