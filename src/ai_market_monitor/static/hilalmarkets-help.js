document.addEventListener("DOMContentLoaded", () => {
  const input = document.querySelector("[data-help-search]");
  const categories = [...document.querySelectorAll("[data-help-category]")];
  const noResults = document.querySelector("[data-help-no-results]");
  if (!input || !categories.length) return;

  const filter = () => {
    const query = input.value.trim().toLocaleLowerCase();
    let visibleCount = 0;
    categories.forEach((category) => {
      let categoryCount = 0;
      category.querySelectorAll("[data-help-article]").forEach((article) => {
        const visible = !query || (article.dataset.searchText || "").includes(query);
        article.hidden = !visible;
        if (visible) categoryCount += 1;
      });
      category.hidden = categoryCount === 0;
      visibleCount += categoryCount;
    });
    if (noResults) noResults.hidden = visibleCount !== 0;
  };

  input.addEventListener("input", filter);
});
