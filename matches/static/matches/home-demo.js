/* Session-only demo: no network, storage, cookies or production save handlers. */
(() => {
  'use strict';
  const facts = JSON.parse(document.getElementById('demo-facts').textContent);
  const cards = [...document.querySelectorAll('[data-demo-match]')];
  const fresh = () => cards.map(() => ({picks: [], goals: '', chip: ''}));
  let state = fresh(), revealed = false;
  const message = document.getElementById('demo-message');
  const goalsDialog = document.getElementById('demo-goals-dialog');
  let activeGoalsIndex = null;
  function render() {
    const goalsUsed = state.filter(s => s.goals !== '').length;
    const chipsUsed = state.filter(s => s.chip !== '').length;
    cards.forEach((card, i) => {
      const current = state[i];
      card.querySelectorAll('[data-pick]').forEach(button => {
        button.setAttribute('aria-pressed', current.picks.includes(button.dataset.pick));
        button.disabled = revealed;
      });
      card.querySelectorAll('[data-chip]').forEach(button => {
        button.setAttribute('aria-pressed', current.chip === button.dataset.chip);
        button.disabled = revealed || state.some((s, j) => j !== i && s.chip === button.dataset.chip);
      });
      const goals = card.querySelector('.demo-goal-picker');
      goals.textContent = current.goals === '' ? 'OBSTAW GOLE' : current.goals;
      goals.disabled = revealed || (current.goals === '' && goalsUsed >= 2);
      goals.title = current.goals === '' && goalsUsed >= 2 ? 'Limit GOLE 2/2 został wykorzystany.' : '';
      goals.setAttribute('aria-label', current.goals === '' ? 'Obstaw GOLE' : `GOLE: ${current.goals}`);
    });
    document.getElementById('demo-counter').textContent = `${state.filter(s => s.picks.length).length} / 3 TYPY`;
    document.getElementById('demo-goals-counter').textContent = `${goalsUsed} / 2 GOLE`;
    document.getElementById('demo-chips-counter').textContent = `${chipsUsed} / 2 CHIPY`;
  }
  cards.forEach((card, i) => {
    card.addEventListener('click', event => {
      if (revealed) return;
      const button = event.target.closest('button');
      if (!button || button.disabled) return;
      const s = state[i];
      if (button.hasAttribute('data-disabled-chip')) {
        card.classList.add('show-locked-chip-hint');
        window.setTimeout(() => card.classList.remove('show-locked-chip-hint'), 2400);
        return;
      }
      if (button.classList.contains('demo-goal-picker')) {
        activeGoalsIndex = i;
        goalsDialog.querySelectorAll('[data-demo-goals]').forEach(option => option.setAttribute('aria-pressed', String(option.dataset.demoGoals === s.goals)));
        goalsDialog.showModal();
        return;
      }
      if (button.dataset.pick) {
        const pick = button.dataset.pick;
        if (s.picks.includes(pick)) s.picks = s.picks.filter(p => p !== pick);
        else if (s.chip === 'DOUBLE_PICK') s.picks = [...s.picks.slice(-1), pick];
        else s.picks = [pick];
      }
      if (button.dataset.chip) {
        s.chip = s.chip === button.dataset.chip ? '' : button.dataset.chip;
        if (s.chip !== 'DOUBLE_PICK') s.picks = s.picks.slice(0, 1);
        message.textContent = s.chip === 'DOUBLE_PICK' ? 'DOUBLE PICK aktywny. Możesz zaznaczyć dwa wyniki tego meczu.' : 'Chip wybrany? Teraz postaw na swoje typy.';
      }
      render();
    });
  });
  goalsDialog.querySelector('.goals-grid').addEventListener('click', event => {
    const option = event.target.closest('[data-demo-goals]');
    if (!option || activeGoalsIndex === null) return;
    state[activeGoalsIndex].goals = option.dataset.demoGoals;
    goalsDialog.close(); activeGoalsIndex = null;
    message.textContent = 'Typ GOLE wybrany. W demo możesz obstawić GOLE w maksymalnie dwóch meczach.';
    render();
  });
  document.getElementById('demo-goals-remove').addEventListener('click', () => {
    if (activeGoalsIndex !== null) state[activeGoalsIndex].goals = '';
    goalsDialog.close(); activeGoalsIndex = null; render();
  });
  document.getElementById('demo-goals-close').addEventListener('click', () => { goalsDialog.close(); activeGoalsIndex = null; });
  document.getElementById('demo-check').addEventListener('click', () => {
    const missing = state.findIndex(s => !s.picks.length);
    if (missing !== -1) {
      message.textContent = 'Wybierz TYP w każdym z trzech meczów. GOLE są opcjonalne.';
      cards[missing].querySelector('[data-pick]').focus();
      return;
    }
    if (revealed) return;
    const totals = {TYPY: 0, GOLE: 0, BONUS: 0, TOTAL: 0};
    state.forEach((s, i) => {
      const f = facts[i], typ = Number(s.picks.includes(f.outcome));
      const goals = Number(s.goals !== '' && Number(s.goals) === f.home + f.away);
      // Only two simple chips are supported. DOUBLE PICK uses the same normal
      // outcome rule; BANKER adds +2 for a hit and -1 for a miss (production rule).
      const banker = s.chip === 'BANKER' ? (typ ? 2 : -1) : 0;
      const modifier = typ ? f.modifier : 0, bonus = banker + modifier;
      totals.TYPY += typ; totals.GOLE += goals; totals.BONUS += bonus;
      const reveal = cards[i].querySelector('.demo-reveal');
      reveal.replaceChildren();
      const matchup = cards[i].querySelector('.demo-dash');
      matchup.textContent = `${f.home} : ${f.away}`;
      matchup.classList.add('resolved-score');
      cards[i].querySelectorAll('[data-pick]').forEach(button => {
        if (s.picks.includes(button.dataset.pick)) {
          const stateName = s.chip === 'DOUBLE_PICK' && typ && button.dataset.pick !== f.outcome ? 'covered' : typ ? 'hit' : 'miss';
          button.dataset.resolvedState = stateName;
          button.setAttribute('aria-label', `${button.dataset.pick}: ${stateName === 'hit' ? 'trafiony' : stateName === 'covered' ? 'dodatkowy typ DOUBLE PICK' : 'nietrafiony'}`);
        }
      });
      const goalControl = cards[i].querySelector('.demo-goal-picker');
      if (s.goals !== '') goalControl.dataset.resolvedState = goals ? 'hit' : 'miss';
      const earned = document.createElement('strong'); earned.className = 'resolved-earned';
      const points = typ + goals + bonus;
      earned.textContent = `${points >= 0 ? '+' : ''}${points} PKT`;
      earned.title = `GOAL FEST +${modifier}${s.chip === 'BANKER' ? ` · BANKER ${banker > 0 ? '+' : ''}${banker}` : ''}`;
      reveal.append(earned);
      if (modifier > 0) {
        const seal = document.createElement('span');
        seal.className = 'demo-gm-seal'; seal.textContent = 'GM';
        seal.setAttribute('role', 'img');
        seal.setAttribute('aria-label', `Global Modifier przyznał +${modifier} punkt`);
        seal.title = `GOAL FEST +${modifier}`;
        reveal.append(seal);
      }
      reveal.hidden = false;
      cards[i].classList.toggle('demo-hit', Boolean(typ));
    });
    totals.TOTAL = totals.TYPY + totals.GOLE + totals.BONUS;
    const summary = document.getElementById('demo-totals'); summary.replaceChildren();
    Object.entries(totals).forEach(([label, value]) => {
      const item = document.createElement('div'), name = document.createElement('span'), score = document.createElement('strong');
      name.textContent = label; score.textContent = value; item.append(name, score); summary.append(item);
    });
    revealed = true; render();
    document.querySelector('.demo-action').hidden = true;
    const result = document.getElementById('demo-result'); result.hidden = false; result.focus();
  });
  document.getElementById('demo-retry').addEventListener('click', () => {
    state = fresh(); revealed = false;
    cards.forEach(card => {
      card.querySelector('.demo-reveal').hidden = true;
      card.classList.remove('demo-hit');
      card.querySelector('.demo-dash').textContent = '—';
      card.querySelector('.demo-dash').classList.remove('resolved-score');
      card.querySelectorAll('[data-resolved-state]').forEach(control => {
        delete control.dataset.resolvedState;
        if (control.matches('[data-pick]')) control.removeAttribute('aria-label');
      });
    });
    document.getElementById('demo-result').hidden = true;
    document.querySelector('.demo-action').hidden = false;
    message.textContent = 'Czysta karta. Wypróbuj inną strategię.';
    render(); cards[0].querySelector('[data-pick]').focus();
  });
  render();
})();
