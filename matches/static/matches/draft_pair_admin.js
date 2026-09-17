(function () {
  function configureCandidateLists() {
    const draft = document.getElementById("id_draft");
    const fields = [document.getElementById("id_match_a"), document.getElementById("id_match_b")].filter(Boolean);
    const dayNumber = document.getElementById("id_day_number");
    if (!draft || !fields.length) return;

    async function refreshCandidates() {
      if (!draft.value) return;
      const url = new URL(fields[0].dataset.candidateUrl, window.location.origin);
      url.searchParams.set("draft", draft.value);
      if (fields[0].dataset.currentPair) url.searchParams.set("current_pair", fields[0].dataset.currentPair);
      try {
        const response = await fetch(url, { credentials: "same-origin" });
        if (!response.ok) throw new Error("Candidate request failed");
        const { candidates, next_day: nextDay } = await response.json();
        fields.forEach((field) => {
          const selectedValue = field.value;
          field.replaceChildren(new Option("---------", ""));
          candidates.forEach(({ id, text }) => field.add(new Option(text, String(id), false, String(id) === selectedValue)));
        });
        if (dayNumber && dayNumber.dataset.autoDay === "true") dayNumber.value = nextDay;
      } catch (_) {
        // Server-side validation still prevents an invalid pair if the request fails.
      }
    }

    draft.addEventListener("change", refreshCandidates);
    refreshCandidates();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", configureCandidateLists);
  else configureCandidateLists();
})();
