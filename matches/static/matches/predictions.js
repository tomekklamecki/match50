(() => {
  'use strict';
  const form = document.getElementById('prediction-form');
  const state = JSON.parse(document.getElementById('prediction-state').textContent || 'null');
  if (!form || !state) return;
  const cards = [...form.querySelectorAll('[data-prediction-card]')];
  const allSlots = [...form.querySelectorAll('[data-match-id]')];
  const feedback = document.getElementById('prediction-feedback');
  const navigation = document.getElementById('card-navigation');
  const previous = document.getElementById('card-previous');
  const next = document.getElementById('card-next');
  const toolbar = document.getElementById('prediction-toolbar');
  const goalsDialog = document.getElementById('goals-dialog');
  const partialDialog = document.getElementById('partial-dialog');
  let view = 'list', index = 0, busy = false, partialAccepted = false;
  let goalsInput = null;
  const pendingChips = new Map();
  const slot = card => state.slots[card.dataset.matchId];
  const checks = card => [...card.querySelectorAll('.choice input')];
  const selected = card => checks(card).filter(input => input.checked).map(input => input.value);
  const chipInput = card => card.querySelector('[name^="chip_"]');
  const chipValue = card => chipInput(card)?.value ?? slot(card).chip;
  const rule = card => slot(card).chips[pendingChips.get(card)?.chip || chipValue(card)] || {limit: 1, allowed: ['1', 'X', '2']};
  const snapshot = card => JSON.stringify({outcomes: selected(card).sort(), goals: card.querySelector('.goal-input')?.value ?? '', chip: chipValue(card)});
  const savedSnapshots = new Map(cards.map(card => [card, snapshot(card)]));
  const isDirty = card => pendingChips.has(card) || snapshot(card) !== savedSnapshots.get(card);
  const hasChanges = () => cards.some(isDirty);
  const incomplete = (card, edit = null) => {
    const picks = edit ? edit.outcomes : selected(card), goals = card.querySelector('.goal-input')?.value ?? '';
    const chip = edit ? edit.chip : pendingChips.get(card)?.chip || chipValue(card);
    if (!picks.length && (goals !== '' || chip)) return 'Wybierz typ 1 / X / 2 przed zapisaniem meczu.';
    if (slot(card).chips[chip]?.limit === 2 && picks.length !== 2) return 'DOUBLE PICK — wybierz dokładnie dwa wyniki.';
    return '';
  };
  const chipDialog = document.createElement('dialog');
  chipDialog.id = 'chip-dialog';
  chipDialog.setAttribute('aria-label', 'Wybierz chip');
  document.body.append(chipDialog);

  function openPicker(dialog, anchor) {
    dialog.classList.toggle('anchored-picker', view === 'list' && matchMedia('(min-width: 701px)').matches);
    dialog.style.removeProperty('left'); dialog.style.removeProperty('top');
    dialog.showModal();
    if (dialog.classList.contains('anchored-picker')) {
      const rect = anchor.getBoundingClientRect();
      dialog.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - dialog.offsetWidth - 12))}px`;
      dialog.style.top = `${Math.max(12, Math.min(rect.bottom + 8, innerHeight - dialog.offsetHeight - 12))}px`;
    }
  }
  [goalsDialog, chipDialog].forEach(dialog => dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  }));

  function validate(card, edit = null) {
    const message = incomplete(card, edit), error = card.querySelector('.prediction-error');
    error.textContent = message; error.hidden = !message;
    card.classList.toggle('needs-attention', !!message);
    checks(card).forEach(input => input.setAttribute('aria-invalid', String(!!message)));
    if (message) checks(card).find(input => !input.disabled)?.focus();
    return !message;
  }
  const tell = (message, error = false) => {
    feedback.textContent = message;
    feedback.classList.toggle('error', error);
    if (error) feedback.focus();
  };

  function leagueLabel(element, league) {
    // SVG comes only from the centralized server presentation mapping.
    element.innerHTML = state.leagueFlags?.[league]?.svg || '';
    element.append(document.createTextNode(`${element.childNodes.length ? ' ' : ''}${league}`));
  }

  function progress() {
    if (!toolbar) return;
    const values = Object.values(state.slots), predicted = values.filter(s => s.predicted).length;
    document.getElementById('prediction-progress').textContent = state.future
      ? `Dostępne: ${values.length}/${state.total} · Wytypowane: ${predicted}/${values.length}`
      : `Wytypowane: ${predicted} / ${state.total}`;
    const leagues = new Map();
    values.forEach(s => {
      const count = leagues.get(s.league) || [0, 0];
      count[0] += Number(s.predicted); count[1]++;
      leagues.set(s.league, count);
    });
    document.getElementById('league-progress').replaceChildren(...[...leagues].map(([name, counts]) => {
      const label = document.createElement('span'); leagueLabel(label, name);
      label.append(document.createTextNode(` ${counts[0]}/${counts[1]}`)); return label;
    }));
    const rules = values[0]?.chips || {};
    document.getElementById('chip-progress').replaceChildren(...Object.entries(rules).map(([chip, r]) => {
      const label = document.createElement('span'), used = values.find(s => s.chip === chip);
      label.dataset.chip = chip;
      label.textContent = `${used ? '✓' : '○'} ${r.label}`;
      label.classList.toggle('used', !!used); label.title = used ? `Użyty: ${used.teams}` : 'Dostępny'; return label;
    }));
    const chipsCounter = document.getElementById('chips-counter');
    if (chipsCounter) chipsCounter.textContent = `CHIPS ${values.filter(s => s.chip).length}/5`;
  }

  function refresh() {
    cards.forEach(card => {
      const current = chipValue(card), rules = rule(card), picks = selected(card);
      card.classList.toggle('chip-match', !!current);
      checks(card).forEach(input => {
        const full = rules.limit === 2 && picks.length === 2 && !input.checked;
        input.disabled = !rules.allowed.includes(input.value) || full;
        input.closest('.choice').classList.toggle('double-available', rules.limit === 2 && picks.length < 2 && !input.checked);
      });
      card.querySelector('.pick-hint').textContent = rules.limit === 2 ? 'DOUBLE PICK — WYBIERZ 2' : 'Wybierz wynik meczu';
      const display = rules.replacement ? slot(card).replacement : slot(card).original;
      if (display) {
        card.querySelector('.teams').textContent = display.teams;
        leagueLabel(card.querySelector('.match-league'), display.league);
        card.querySelector('.league-kickoff').textContent = display.kickoff;
        card.querySelector('.match-time').textContent = display.kickoff;
      }
      card.querySelectorAll('[data-chip]').forEach(button => {
        const name = button.dataset.chip, config = slot(card).chips[name];
        const active = name === current;
        const used = allSlots.find(other => other !== card && (view === 'list' ? slot(other).chip : chipValue(other)) === name);
        let reason = active ? config.lockReason : config.reason;
        if (used) reason = `Użyty: ${used.querySelector('.teams').textContent}`;
        else if (current && !active) reason = 'Usuń aktualny chip';
        else if (!active && picks.some(pick => !config.allowed.includes(pick))) reason = 'Niedostępny dla wybranego wyniku';
        else if (!active && config.requiresPick && !picks.length) reason = 'Najpierw wybierz wynik';
        button.disabled = !!reason;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
        button.title = reason || config.label;
        button.setAttribute('aria-label', `${config.label}: ${reason || (active ? 'Aktywny' : 'Dostępny')}`);
        button.querySelector('.chip-state').textContent = reason ? (used ? 'Użyty ↗' : 'Niedostępny') : (active ? '✓ Aktywny' : 'Dostępny');
      });
      const goals = card.querySelector('.goal-input'), trigger = card.querySelector('.goal-picker');
      if (trigger) {
        trigger.textContent = view === 'list' ? (goals.value === '' ? '+ GOLE' : goals.value) : (goals.value === '' ? 'OBSTAW GOLE' : `GOLE: ${goals.value} · ZMIEŃ`);
        trigger.setAttribute('aria-label', goals.value === '' ? 'Obstaw gole' : `GOLE: ${goals.value} — zmień`);
      }
      card.querySelector('.teams').title = card.querySelector('.teams').textContent;
      card.querySelector('.match-league').title = `${display?.league || ''} · ${display?.kickoff || ''}`;
      const compact = card.querySelector('.chip-picker');
      const activeButton = card.querySelector(`[data-chip="${current}"]`);
      compact.textContent = current ? `${activeButton?.firstChild.textContent.trim() || '◇'} ${slot(card).chips[current].label}` : '◇ CHIP';
      compact.title = current ? slot(card).chips[current].label : 'Wybierz chip';
      if (pendingChips.has(card)) {
        compact.textContent = `${slot(card).chips[pendingChips.get(card).chip].label} · wybierz ${rules.limit}`;
        compact.title = 'Wybierz wyniki w wierszu. Chip nie jest jeszcze zapisany. Kliknij ponownie, aby anulować.';
      }
      const error = incomplete(card), changed = isDirty(card), status = card.querySelector('.row-status');
      status.textContent = error ? '!' : changed ? '•' : picks.length ? '✓' : '○';
      status.title = error || (changed ? 'Niezapisane zmiany' : picks.length ? 'Zapisany typ' : 'Brak typu');
      status.setAttribute('aria-label', status.title);
      status.classList.toggle('incomplete', !!error);
      if (!error && card.classList.contains('needs-attention')) validate(card);
    });
    // Summary always uses server-confirmed state, never the unsaved row inputs.
    progress();
  }

  function display() {
    form.classList.toggle('card-view', view === 'card');
    cards.forEach((card, i) => { card.hidden = view === 'card' && i !== index; });
    navigation.hidden = view !== 'card' || !cards.length;
    previous.disabled = index === 0;
    next.textContent = index === cards.length - 1 ? '✓ ZAPISZ I ZAKOŃCZ' : 'ZAPISZ I NASTĘPNY →';
    document.getElementById('card-position').textContent = `${index + 1} / ${cards.length}${state.future ? ' dostępnych' : ''}`;
    document.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === view)));
    refresh();
  }

  function confirmPartial() {
    return new Promise(resolve => {
      partialDialog.showModal();
      const finish = answer => { partialDialog.close(); resolve(answer); };
      document.getElementById('partial-back').onclick = () => finish(false);
      document.getElementById('partial-save').onclick = () => finish(true);
      partialDialog.oncancel = event => { event.preventDefault(); finish(false); };
    });
  }

  async function save(card = null, chipEdit = null, navigation = false) {
    const scope = card || form;
    const changedCards = chipEdit ? [card] : (card ? [card] : cards).filter(isDirty);
    if (!changedCards.length) return true;
    if (!changedCards.every(changed => validate(changed, chipEdit))) return false;
    if (!scope.querySelectorAll('input').length) return true;
    for (const input of scope.querySelectorAll('input')) {
      if (!input.disabled && !input.checkValidity()) { input.reportValidity(); return false; }
    }
    const data = new FormData();
    data.set('csrfmiddlewaretoken', form.querySelector('[name=csrfmiddlewaretoken]').value);
    data.set('round_id', state.round); data.set('ui_save', '1');
    if (card) data.set('card_match', card.dataset.matchId);
    scope.querySelectorAll('input[name]').forEach(input => {
      if (!input.disabled && (input.type !== 'checkbox' || input.checked)) data.append(input.name, input.value);
    });
    if (chipEdit) {
      data.set(`chip_${card.dataset.matchId}`, chipEdit.chip);
      data.delete(`result_${card.dataset.matchId}`);
      chipEdit.outcomes.forEach(outcome => data.append(`result_${card.dataset.matchId}`, outcome));
      // Clicking a chip explicitly commits this match, including a partial Round.
      // The backend still enforces the GOLE maximum and all chip invariants.
      data.set('confirm_less_than_ten', '1');
    }
    if (partialAccepted || form.querySelector('[name=confirm_less_than_ten]')) data.set('confirm_less_than_ten', '1');
    if (navigation) data.set('confirm_less_than_ten', '1');
    tell('ZAPISYWANIE...');
    form.setAttribute('aria-busy', 'true');
    // Freeze controls while the request is pending, preserving original disabled states.
    const controls = [...form.querySelectorAll('input,button'), ...document.querySelectorAll('[data-view]')];
    const disabled = controls.map(control => control.disabled);
    controls.forEach(control => { control.disabled = true; });
    const nextLabel = next.textContent;
    next.textContent = 'ZAPISYWANIE...';
    try {
      let response, result;
      while (true) {
        response = await fetch(window.location.href, {method: 'POST', body: data, credentials: 'same-origin', headers: {'Accept': 'application/json'}});
        const isJson = response.headers.get('content-type')?.includes('application/json');
        result = isJson ? await response.json() : {error: (await response.text()).replace(/<[^>]*>/g, '').slice(0, 400)};
        if (result.needs_confirmation && await confirmPartial()) {
          partialAccepted = true; data.set('confirm_less_than_ten', '1'); continue;
        }
        break;
      }
      if (!response.ok || result.saved !== true) {
        tell(result.error || 'Nie udało się zapisać. Twoje wybory pozostają na karcie.', true); return false;
      }
      Object.entries(result.slots).forEach(([id, saved]) => Object.assign(state.slots[id], saved));
      if (chipEdit) cards.forEach(other => {
        if (chipInput(other)) chipInput(other).value = slot(other).chip;
        const baseline = JSON.parse(savedSnapshots.get(other));
        baseline.chip = slot(other).chip;
        savedSnapshots.set(other, JSON.stringify(baseline));
      });
      (card ? [card] : cards).forEach(saved => {
        const s = slot(saved);
        checks(saved).forEach(input => { input.checked = s.outcomes.includes(input.value); });
        if (chipInput(saved)) chipInput(saved).value = s.chip;
        const goals = saved.querySelector('.goal-input');
        if (goals) goals.value = s.goals;
        savedSnapshots.set(saved, snapshot(saved));
      });
      tell('✓ Typy zapisane.'); return true;
    } catch (error) {
      tell('Nie udało się potwierdzić zapisu. Sprawdź połączenie i spróbuj ponownie. Twoje wybory pozostają na karcie.', true); return false;
    } finally {
      controls.forEach((control, i) => { control.disabled = disabled[i]; });
      next.textContent = nextLabel;
      form.removeAttribute('aria-busy');
      refresh();
    }
  }

  async function leave(action, whole = false, navigation = false) {
    if (busy) return;
    busy = true;
    try { if (await save(whole ? null : cards[index], null, navigation)) { action(); display(); } }
    finally { busy = false; }
  }

  async function commitPreparedChip(card, edit) {
    if (busy) return;
    busy = true;
    try { return await save(card, edit); }
    finally { busy = false; }
  }

  function cancelPendingChip(card) {
    const pending = pendingChips.get(card);
    if (!pending) return;
    checks(card).forEach(input => { input.checked = pending.outcomes.includes(input.value); });
    pendingChips.delete(card);
  }

  cards.forEach(card => {
    const league = card.querySelector('.match-league');
    const identity = document.createElement('div'); identity.className = 'league-identity';
    league.before(identity); identity.append(league);
    const kickoff = document.createElement('span'); kickoff.className = 'league-kickoff'; identity.append(kickoff);
    const openCard = document.createElement('button'); openCard.type = 'button'; openCard.className = 'open-card';
    openCard.title = 'Otwórz kartę meczu'; openCard.setAttribute('aria-label', openCard.title); openCard.textContent = '▣';
    openCard.addEventListener('click', () => leave(() => { index = cards.indexOf(card); view = 'card'; }, true, true));
    card.append(openCard);
    const error = document.createElement('p'); error.className = 'prediction-error'; error.hidden = true;
    error.id = `prediction-error-${card.dataset.matchId}`; error.setAttribute('role', 'alert'); card.querySelector('.choices').after(error);
    checks(card).forEach(input => input.setAttribute('aria-describedby', error.id));
    const status = document.createElement('span'); status.className = 'row-status'; card.append(status);
    const compact = document.createElement('button'); compact.type = 'button'; compact.className = 'chip-picker';
    compact.setAttribute('aria-haspopup', 'dialog'); card.append(compact);
    compact.addEventListener('click', () => {
      refresh();
      const title = document.createElement('h2'); title.textContent = 'Wybierz chip';
      const buttons = [...card.querySelectorAll('[data-chip]')].map(original => {
        const clone = original.cloneNode(true);
        clone.querySelector('.chip-state').textContent = original.title;
        const config = slot(card).chips[original.dataset.chip];
        const removing = chipValue(card) === original.dataset.chip;
        clone.addEventListener('click', async () => {
          if (busy) return;
          chipDialog.close();
          const cancelled = pendingChips.get(card)?.chip === original.dataset.chip;
          cancelPendingChip(card);
          if (cancelled) { refresh(); return; }
          const chip = removing ? '' : original.dataset.chip;
          const outcomes = config.limit > 1 && !removing ? selected(card) : selected(card).slice(0, 1);
          if (!removing && config.limit > 1 && outcomes.length !== config.limit) {
            pendingChips.set(card, {chip, outcomes});
            refresh();
            checks(card).find(input => !input.checked && !input.disabled)?.focus();
            return;
          }
          await commitPreparedChip(card, {chip, outcomes});
        });
        return clone;
      });
      const close = document.createElement('button'); close.type = 'button'; close.className = 'button secondary'; close.textContent = 'ZAMKNIJ'; close.onclick = () => chipDialog.close();
      chipDialog.replaceChildren(title, ...buttons, close);
      openPicker(chipDialog, compact);
    });
    card.querySelectorAll('[data-chip]').forEach(button => {
      const label = document.createElement('small'); label.textContent = slot(card).chips[button.dataset.chip].label;
      const status = document.createElement('span'); status.className = 'chip-state'; button.append(label, status);
      button.addEventListener('click', () => {
        const input = chipInput(card); input.value = input.value === button.dataset.chip ? '' : button.dataset.chip;
        // Removing DOUBLE PICK deterministically keeps the first selected outcome.
        selected(card).slice(rule(card).limit).forEach(value => { checks(card).find(c => c.value === value).checked = false; });
        refresh();
      });
    });
    const input = card.querySelector('.goal-input');
    if (input) {
      const trigger = document.createElement('button'); trigger.type = 'button'; trigger.className = 'goal-picker';
      trigger.disabled = input.disabled;
      trigger.setAttribute('aria-haspopup', 'dialog'); input.closest('label').hidden = true; input.closest('label').after(trigger);
      trigger.addEventListener('click', () => {
        goalsInput = input;
        goalsDialog.querySelectorAll('[data-goals]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.goals === input.value)));
        openPicker(goalsDialog, trigger);
      });
    }
  });
  goalsDialog.querySelectorAll('[data-goals]').forEach(button => button.addEventListener('click', () => {
    goalsInput.value = button.dataset.goals; refresh(); goalsDialog.close();
  }));
  document.getElementById('goals-remove').onclick = () => { goalsInput.value = ''; refresh(); goalsDialog.close(); };
  document.getElementById('goals-close').onclick = () => goalsDialog.close();
  form.addEventListener('change', async event => {
    const card = event.target.closest('[data-prediction-card]');
    if (!card) return;
    if (event.target.type === 'checkbox') {
      const limit = rule(card).limit;
      if (limit === 1 && event.target.checked) checks(card).forEach(input => { if (input !== event.target) input.checked = false; });
      if (selected(card).length > limit) event.target.checked = false;
    }
    const pending = pendingChips.get(card);
    if (pending && selected(card).length === rule(card).limit) {
      const outcomes = selected(card);
      refresh();
      pendingChips.delete(card);
      if (!await commitPreparedChip(card, {chip: pending.chip, outcomes})) {
        checks(card).forEach(input => { input.checked = pending.outcomes.includes(input.value); });
      }
    }
    refresh();
  });
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.view === view) return;
    const changeView = () => {
      view = button.dataset.view;
      try { localStorage.setItem('match50.predictionView', view); } catch (_) { /* Preferences may be blocked. */ }
    };
    leave(changeView, view === 'list', true);
  }));
  previous.onclick = () => leave(() => { index = Math.max(0, index - 1); });
  next.onclick = () => leave(() => {
    if (index < cards.length - 1) index++;
    else {
      view = 'list';
      tell('✓ Zakończono przegląd kart. Sprawdź typy na liście.');
      // Finishing is a review transition, not a change to the explicit view preference.
    }
  });
  form.addEventListener('submit', event => { event.preventDefault(); leave(() => {}, view !== 'card'); });
  document.getElementById('clear-predictions')?.addEventListener('click', () => {
    pendingChips.clear();
    clearPredictionChecks(form);
    cards.forEach(card => {
      const input = card.querySelector('.goal-input'); if (input && !input.disabled) input.value = '';
      if (chipInput(card)) chipInput(card).value = '';
    });
    refresh(); tell('Wyczyszczono wybory w formularzu. Zapisz typy, aby zatwierdzić.');
  });
  document.getElementById('save-anyway')?.addEventListener('click', event => {
    partialAccepted = true; event.target.closest('.modal').remove(); leave(() => {}, true);
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || !cards.length || event.defaultPrevented || link.getAttribute('href').startsWith('#')) return;
    event.preventDefault(); leave(() => { window.location.assign(link.href); }, view === 'list');
  });
  document.addEventListener('submit', event => {
    if (event.target === form || !cards.length) return;
    event.preventDefault(); const otherForm = event.target;
    leave(() => HTMLFormElement.prototype.submit.call(otherForm), view === 'list');
  });
  window.addEventListener('beforeunload', event => {
    if (hasChanges() || busy) { event.preventDefault(); event.returnValue = ''; }
  });
  if (toolbar) {
    toolbar.hidden = false;
    try { view = localStorage.getItem('match50.predictionView') || (matchMedia('(max-width: 700px)').matches ? 'card' : 'list'); }
    catch (_) { view = matchMedia('(max-width: 700px)').matches ? 'card' : 'list'; }
    if (!['list', 'card'].includes(view)) view = 'list';
    // A saved Card View preference must never hide a results-only Current Round.
    if (!cards.length) view = 'list';
  }
  refresh(); display();
})();
