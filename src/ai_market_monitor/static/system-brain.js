document.querySelectorAll("[data-filter-target]").forEach((input) => {
  input.addEventListener("input", () => {
    const table = document.getElementById(input.dataset.filterTarget);
    const query = input.value.trim().toLowerCase();
    table?.querySelectorAll("tbody tr").forEach((row) => {
      row.hidden = Boolean(query) && !row.textContent.toLowerCase().includes(query);
    });
  });
});

const sections = [...document.querySelectorAll(".brain-section")];
const links = [...document.querySelectorAll(".brain-sidebar nav a")];
if (sections.length && "IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    links.forEach((link) => link.classList.toggle("active", link.hash === `#${visible.target.id}`));
  }, { rootMargin: "-20% 0px -65%", threshold: [0.05, 0.25] });
  sections.forEach((section) => observer.observe(section));
}

document.querySelectorAll(".brain-review-case").forEach((reviewCase) => {
  reviewCase.addEventListener("toggle", () => {
    if (reviewCase.open) {
      reviewCase.querySelector(".brain-review-close")?.focus();
    }
  });
  reviewCase.querySelector("[data-close-review]")?.addEventListener("click", () => {
    reviewCase.open = false;
    reviewCase.querySelector(":scope > summary")?.focus();
  });
  reviewCase.querySelector(".brain-review-backdrop")?.addEventListener("click", () => {
    reviewCase.open = false;
    reviewCase.querySelector(":scope > summary")?.focus();
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const openReview = document.querySelector(".brain-review-case[open]");
  if (openReview) {
    openReview.open = false;
    openReview.querySelector(":scope > summary")?.focus();
  }
});
