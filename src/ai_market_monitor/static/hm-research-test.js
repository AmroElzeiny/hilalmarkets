/* The research pages at /dashboard/research: what the machine read, in its own words.
 *
 * Nothing here decides a fact. The server rendered every row, every verdict and every
 * sentence; this file only hands the page's own words to the assistant, so "what did
 * you find about this coin" can be answered about the list in front of the person.
 */

import { publish } from "./hm-page-context.js";
import { pageNote } from "./hm-page-notes.js";

/* The list of researched coins: each coin with the verdict the page carries. Only
 * where the list table is, so the documents table on the detail page never reads
 * as coins. */
if (document.querySelector(".t-table .t-asset-symbol")) {
  publish("research", () => {
    const rows = [...document.querySelectorAll(".t-table tbody tr")];
    const lines = rows.map((row) => {
      const symbol = (row.querySelector(".t-asset-symbol") || {}).textContent || "";
      const verdict = (row.querySelector(".t-pill") || {}).textContent || "";
      const line = `${symbol.trim()}: ${verdict.trim().replace(/\s+/g, " ")}`.trim();
      return line.replace(/\s+/g, " ");
    }).filter((line) => line && line !== ":");
    const count = `${rows.length} ${rows.length === 1 ? "coin" : "coins"} researched`;
    return pageNote({ summary: `Coins we researched: ${count}`, points: lines });
  });
}

/* One coin's reading: the verdict and the sections the page lays out. Only where
 * there is no coin list, so the list description above always wins on the list. */
if (!document.querySelector(".t-table .t-asset-symbol")) {
  publish("research", () => {
    const verdict = document.querySelector(".t-pill");
    const sections = [...document.querySelectorAll("section h2")].map((heading) =>
      heading.textContent.trim().replace(/\s+/g, " "),
    ).filter(Boolean);
    return pageNote({
      summary: verdict ? verdict.textContent.trim().replace(/\s+/g, " ") : null,
      points: sections,
    });
  });
}
