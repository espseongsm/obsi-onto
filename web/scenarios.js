const questionScenarios = [
  {title: '업무 결정의 배경', domain: 'work', icon: '↗',
    question: '프로젝트 B의 방향을 바꾼 이유는 무엇인가?',
    map: '회의 기록 → 프로젝트 → 결정 근거'},
  {title: '계획과 실행 돌아보기', domain: 'work', icon: '✓',
    question: '프로젝트 B에서 계획한 일과 완료한 일을 비교해줘',
    map: '프로젝트 → 계획 문단 · 완료 기록'},
  {title: '투자 판단의 변화', domain: 'investment', icon: '⌁',
    question: 'A기업에 대한 투자 판단은 어떻게 달라졌나? 비교해줘',
    map: '같은 기업 → 날짜별 분석 · 판단'},
  {title: '매매의 이유 복기', domain: 'investment', icon: '◷',
    question: 'A기업 매수를 검토한 이유와 실제 거래 기록을 찾아줘',
    map: '기업 → 검토한 이유 · 거래 원문'},
  {title: '나의 원칙과 판단 연결', domain: 'all', icon: '✧',
    question: '내 투자 원칙과 연결된 분석을 찾아줘',
    map: '원칙 노트 → 연결한 분석 · 일지'},
  {title: '생각을 업무와 연결', domain: 'all', icon: '◎',
    question: '결정의 이유를 기록하자는 생각과 연결된 업무 기록을 찾아줘',
    map: '개인 생각 → 같은 주제 · 업무 문단'}
];

let scenarioVersion;
function renderScenarios(data) {
  const version = JSON.stringify([!!data.vault, data.suggestions]);
  if (version === scenarioVersion) return;
  scenarioVersion = version;
  const suggestions = data.suggestions || {state: 'pending', items: []};
  const items = data.vault ? suggestions.items : questionScenarios;
  $('#scenario-title').textContent = data.vault ? `볼트에서 뽑은 예상 질문${items.length ? ' ' + items.length + '개' : ''}` : '질문 시나리오 6가지';
  $('#scenario-info').textContent = data.vault ? (suggestions.message || '색인 완료 후 예상 질문을 만듭니다.') +
    (suggestions.generated_at ? ` · ${new Date(suggestions.generated_at).toLocaleString()} 생성` : '') :
    '가상 샘플의 이름입니다. 실제 회사·종목·프로젝트 이름으로 바꿔 질문하세요.';
  $('#scenario-cards').replaceChildren();
  for (const [index, scenario] of items.entries()) {
    const style = {work: 'work', investment: 'invest', personal: 'thought', all: 'thought'}[scenario.domain];
    const button = el('button'); button.type = 'button';
    button.append(el('span', scenario.icon || '↗', 'card-icon ' + style), el('small', `SCENARIO ${String(index + 1).padStart(2, '0')}`),
      el('strong', scenario.title), el('p', scenario.question, 'scenario-question'),
      el('span', scenario.sources ? '참고: ' + scenario.sources.map(source => `${source.path}:${source.start}`).join(' · ') : scenario.map, 'scenario-map'));
    button.onclick = () => {
      $('#question').value = scenario.question; $('#domain').value = scenario.domain;
      $('#start').value = ''; $('#end').value = ''; $('#question').focus();
    };
    $('#scenario-cards').append(button);
  }
}
