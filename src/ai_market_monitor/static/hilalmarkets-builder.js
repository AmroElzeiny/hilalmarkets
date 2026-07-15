(() => {
  "use strict";
  const input = document.querySelector("[data-ai-chat-input]");
  document.querySelectorAll("[data-guided-starter]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!input) return;
      input.value = button.dataset.guidedStarter || "";
      input.dispatchEvent(new Event("input", {bubbles: true}));
      input.focus();
    });
  });
})();
