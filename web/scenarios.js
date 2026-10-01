const questionScenarios = [
  {title: 'Behind a work decision', domain: 'work', icon: '↗',
    question: 'Why did Project B change direction?',
    map: 'Meeting notes → project → decision sources'},
  {title: 'Plans and progress', domain: 'work', icon: '✓',
    question: 'Compare the planned and completed work for Project B.',
    map: 'Project → planned work · completion records'},
  {title: 'How an investment thesis evolved', domain: 'investment', icon: '⌁',
    question: 'How has my investment thesis for Company A changed?',
    map: 'Company → analysis and decisions over time'},
  {title: 'Review a trading decision', domain: 'investment', icon: '◷',
    question: 'Find why I considered buying Company A and the actual trade records.',
    map: 'Company → reasons considered · trade records'},
  {title: 'Connect principles and decisions', domain: 'all', icon: '✧',
    question: 'Find analyses connected to my investment principles.',
    map: 'Principles → related analysis and journals'},
  {title: 'Connect ideas to work', domain: 'all', icon: '◎',
    question: 'Find work notes related to the idea of recording the reasons behind decisions.',
    map: 'Personal ideas → shared topics · work passages'}
];

let scenarioVersion;
function renderScenarios(data) {
  const version = JSON.stringify([!!data.vault, data.suggestions]);
  if (version === scenarioVersion) return;
  scenarioVersion = version;
  const suggestions = data.suggestions || {state: 'pending', items: []};
  const items = data.vault ? suggestions.items : questionScenarios;
  $('#scenario-title').textContent = data.vault ? `Questions from your vault${items.length ? ' · ' + items.length : ''}` : 'Six ways to explore your notes';
  $('#scenario-info').textContent = data.vault ? (uiText(suggestions.message) || 'Suggested questions will be created after indexing.') +
    (suggestions.generated_at ? ` · Generated ${new Date(suggestions.generated_at).toLocaleString('en')}` : '') :
    'These examples use fictional names. Replace them with companies, investments, or projects from your own notes.';
  $('#scenario-cards').replaceChildren();
  for (const [index, scenario] of items.entries()) {
    const style = {work: 'work', investment: 'invest', personal: 'thought', all: 'thought'}[scenario.domain];
    const button = el('button'); button.type = 'button';
    button.append(el('span', scenario.icon || '↗', 'card-icon ' + style), el('small', `SCENARIO ${String(index + 1).padStart(2, '0')}`),
      el('strong', scenario.title), el('p', scenario.question, 'scenario-question'),
      el('span', scenario.sources ? 'Sources: ' + scenario.sources.map(source => `${source.path}:${source.start}`).join(' · ') : scenario.map, 'scenario-map'));
    button.onclick = () => {
      $('#question').value = scenario.question; $('#domain').value = scenario.domain;
      $('#start').value = ''; $('#end').value = ''; $('#question').focus();
    };
    $('#scenario-cards').append(button);
  }
}
