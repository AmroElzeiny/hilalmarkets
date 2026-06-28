const proofData = {
  sol: {
    symbol: "SOL/USDT", status: "Near confirmation",
    rules: [
      ["4h close above EMA 200", "$146.82 > $142.10", true],
      ["Bullish liquidity sweep", "Prior low reclaimed", true],
      ["Volume at least 1.5x", "1.36x / 1.50x", false]
    ]
  },
  link: {
    symbol: "LINK/USDT", status: "Forming",
    rules: [
      ["4h close above EMA 200", "$14.21 > $13.88", true],
      ["Bullish liquidity sweep", "Reclaim not closed", false],
      ["Volume at least 1.5x", "1.62x / 1.50x", true]
    ]
  },
  eth: {
    symbol: "ETH/USDT", status: "Weakening",
    rules: [
      ["4h close above EMA 200", "$2,482 > $2,410", true],
      ["Bullish liquidity sweep", "No sweep detected", false],
      ["Volume at least 1.5x", "0.91x / 1.50x", false]
    ]
  }
};

function showProof(key) {
  const data = proofData[key];
  document.querySelector("#proof-symbol").textContent = data.symbol;
  document.querySelector("#proof-status").textContent = data.status;
  document.querySelector("#proof-rules").innerHTML = data.rules.map(([label, value, passed]) => `
    <div class="proof-rule"><span>${label}<br><small>${value}</small></span><b class="${passed ? "" : "fail"}">${passed ? "PASS" : "MISSING"}</b></div>
  `).join("");
}

document.querySelectorAll(".market-row").forEach(row => row.addEventListener("click", () => {
  document.querySelectorAll(".market-row").forEach(item => item.classList.remove("active"));
  row.classList.add("active");
  showProof(row.dataset.market);
}));

const observer = new IntersectionObserver(entries => entries.forEach(entry => {
  if (entry.isIntersecting) {
    entry.target.classList.add("visible");
    observer.unobserve(entry.target);
  }
}), { threshold: .12 });
document.querySelectorAll(".reveal").forEach(element => observer.observe(element));

const params = new URLSearchParams(window.location.search);
const attribution = ["utm_source", "utm_medium", "utm_campaign", "ref", "entry_channel"]
  .reduce((result, key) => params.get(key) ? { ...result, [key]: params.get(key) } : result, {});
if (Object.keys(attribution).length) {
  sessionStorage.setItem("amm_attribution", JSON.stringify(attribution));
}

showProof("sol");

