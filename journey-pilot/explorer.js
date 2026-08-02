(() => {
  const form = document.querySelector("#evidence-filters");
  const site = document.querySelector("#site-filter");
  const principle = document.querySelector("#principle-filter");
  const status = document.querySelector("#status-filter");
  const count = document.querySelector("#filter-count");
  const rows = [...document.querySelectorAll(".check-row")];
  const principleGroups = [...document.querySelectorAll(".principle-checks")];
  const siteGroups = [...document.querySelectorAll(".site-checks")];

  if (!form || !site || !principle || !status || !count || rows.length !== 580) return;

  const apply = () => {
    let visible = 0;
    for (const row of rows) {
      const matches = (!site.value || row.dataset.site === site.value) &&
        (!principle.value || row.dataset.principle === principle.value) &&
        (!status.value || row.dataset.status === status.value);
      row.hidden = !matches;
      if (matches) visible += 1;
    }
    for (const group of principleGroups) {
      group.hidden = !group.querySelector(".check-row:not([hidden])");
      if (!group.hidden && (principle.value || status.value)) group.open = true;
    }
    for (const group of siteGroups) {
      group.hidden = !group.querySelector(".check-row:not([hidden])");
      if (!group.hidden && (site.value || principle.value || status.value)) group.open = true;
    }
    count.textContent = `Showing ${visible} of 580 site-check slots.`;
  };

  form.addEventListener("input", apply);
  form.addEventListener("reset", () => setTimeout(apply));
})();
