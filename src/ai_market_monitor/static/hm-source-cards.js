/* The preview cards under an assistant answer, drawn once for every chat.
 *
 * The public assistant and Hilal both call `window.HilalSourceCards.render(sources)`
 * with the `sources` list their server sent, and put what it returns under the answer.
 * Every field on a card was built by the server from its own page catalog
 * (`services/source_previews.py`); nothing here is written by a model, and every value
 * is set as text or as an attribute, never as markup.
 *
 * A card opens its page in a new tab, so the conversation stays where it was.
 */
(() => {
  "use strict";

  const EXTERNAL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>';

  /** Only addresses on this site or the product's own hostname, and only over http(s). */
  function safeUrl(value) {
    try {
      const address = new URL(String(value || ""), window.location.origin);
      return ["http:", "https:"].includes(address.protocol) ? address.href : null;
    } catch {
      return null;
    }
  }

  function card(source) {
    const url = safeUrl(source.url);
    if (!url) return null;
    const link = document.createElement("a");
    link.className = "hm-source";
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.setAttribute("aria-label", `${source.title}. ${source.description} Opens in a new tab.`);

    const shot = document.createElement("span");
    shot.className = "hm-source-shot";
    const image = source.image_url ? safeUrl(source.image_url) : null;
    if (image) {
      const picture = document.createElement("img");
      picture.src = image;
      picture.alt = "";
      picture.loading = "lazy";
      picture.decoding = "async";
      picture.width = 224;
      picture.height = 140;
      shot.append(picture);
    }

    const body = document.createElement("span");
    body.className = "hm-source-body";
    const title = document.createElement("span");
    title.className = "hm-source-title";
    title.textContent = source.title;
    const text = document.createElement("span");
    text.className = "hm-source-text";
    text.textContent = source.description;
    const address = document.createElement("span");
    address.className = "hm-source-address";
    address.innerHTML = EXTERNAL;
    const where = document.createElement("span");
    where.textContent = source.address || "";
    address.append(where);
    body.append(title, text, address);

    link.append(shot, body);
    return link;
  }

  function render(sources) {
    const list = (Array.isArray(sources) ? sources : []).map(card).filter(Boolean);
    if (!list.length) return null;
    const holder = document.createElement("nav");
    holder.className = "hm-sources";
    holder.setAttribute("aria-label", "Where this answer comes from");
    const label = document.createElement("p");
    label.className = "hm-sources-label";
    label.textContent = "Where this comes from";
    holder.append(label, ...list);
    return holder;
  }

  /** The two buttons of a "sign in first" answer, from the server's own addresses. */
  function accountPrompt(prompt) {
    if (!prompt) return null;
    const signup = safeUrl(prompt.signup_href);
    const signin = safeUrl(prompt.signin_href);
    if (!signup || !signin) return null;
    const holder = document.createElement("div");
    holder.className = "hm-account-prompt";
    const start = document.createElement("a");
    start.className = "is-primary";
    start.href = signup;
    start.textContent = prompt.signup_label || "Start free";
    const back = document.createElement("a");
    back.href = signin;
    back.textContent = "Sign in";
    holder.append(start, back);
    return holder;
  }

  window.HilalSourceCards = { render, accountPrompt };
})();
