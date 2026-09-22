(function () {
  function configureCandidateLists() {
    const draft = document.getElementById("id_draft");
    const fields = [document.getElementById("id_match_a"), document.getElementById("id_match_b")].filter(Boolean);
    const dayNumber = document.getElementById("id_day_number");
    const week = document.getElementById("id_kw_filter");
    const competition = document.getElementById("id_competition_filter");
    const query = document.getElementById("id_fixture_search");
    if (!draft || fields.length !== 2 || !week || !competition || !query) return;
    const status = document.createElement("span");
    status.className = "draft-pair-picker__status";
    status.setAttribute("aria-live", "polite");
    query.insertAdjacentElement("afterend", status);
    let timer;
    function replaceMetadata(select, items, placeholder, key) {
      const value = select.value;
      select.replaceChildren(new Option(placeholder, ""));
      items.forEach((item) => select.add(new Option(item.text, String(item[key]))));
      select.value = value;
    }
    function preventDuplicateSelection() {
      fields.forEach((field, index) => {
        const otherValue = fields[1 - index].value;
        Array.from(field.options).forEach((option) => {
          option.disabled = Boolean(option.value && option.value === otherValue && option.value !== field.value);
        });
      });
    }
    async function refreshCandidates() {
      if (!draft.value) { status.textContent = "Choose a Draft first."; return; }
      const url = new URL(fields[0].dataset.candidateUrl, window.location.origin);
      url.searchParams.set("draft", draft.value);
      if (fields[0].dataset.currentPair) url.searchParams.set("current_pair", fields[0].dataset.currentPair);
      if (week.value) url.searchParams.set("week", week.value);
      if (competition.value) url.searchParams.set("competition", competition.value);
      if (query.value.trim()) url.searchParams.set("q", query.value.trim());
      status.textContent = "Loading…";
      try {
        const response = await fetch(url, { credentials: "same-origin" });
        if (!response.ok) throw new Error("Candidate request failed");
        const data = await response.json();
        replaceMetadata(week, data.weeks, "Choose KW…", "value");
        replaceMetadata(competition, data.competitions, "All competitions", "id");
        fields.forEach((field) => {
          const selectedValue = field.value;
          const selectedText = field.selectedOptions[0] && field.selectedOptions[0].text;
          field.replaceChildren(new Option("— Select a fixture —", ""));
          data.candidates.forEach(({ id, text }) => field.add(new Option(text, String(id))));
          if (selectedValue && !Array.from(field.options).some((option) => option.value === selectedValue)) field.add(new Option(selectedText, selectedValue));
          field.value = selectedValue;
        });
        preventDuplicateSelection();
        status.textContent = `${data.candidates.length} fixture${data.candidates.length === 1 ? "" : "s"}${data.truncated ? " (refine filters)" : ""}`;
        if (dayNumber && dayNumber.dataset.autoDay === "true") dayNumber.value = data.next_day;
      } catch (_) { status.textContent = "Could not load fixtures. Server validation remains active."; }
    }
    draft.addEventListener("change", refreshCandidates);
    week.addEventListener("change", refreshCandidates);
    competition.addEventListener("change", refreshCandidates);
    query.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refreshCandidates, 250); });
    fields.forEach((field) => field.addEventListener("change", preventDuplicateSelection));
    refreshCandidates();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", configureCandidateLists);
  else configureCandidateLists();
})();

