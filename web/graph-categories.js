/* Display-only knowledge areas derived from available names and explicit metadata. */
const GraphCategories = (() => {
  const definitions = [
    ['ai', 'AI & Models', ['ai', 'llm', 'rag', 'gpt', 'chatgpt', 'claude', 'gemma', 'vllm', 'codex',
      'machine learning', 'deep learning', 'neural', 'hugging face', 'robotics', '인공지능', '생성형', '머신러닝', '딥러닝', '언어모델', '로봇']],
    ['data', 'Data & Analytics', ['data', 'analytics', 'database', 'sql', 'sqlite', 'duckdb', 'snowflake',
      'databricks', 'palantir', 'vector', 'warehouse', 'dbt', 'bigquery', '데이터', '데이터베이스', '벡터', '통계']],
    ['engineering', 'Software & Infra', ['docker', 'kubernetes', 'ssh', 'gpu', 'vscode', 'vs code', 'git',
      'github', 'api', 'server', 'linux', 'rust', 'python', 'software', 'engineering', 'development', 'infra', 'cloud', 'aws', 'azure',
      '개발', '코딩', '서버', '배포', '클라우드', '인프라', '컨테이너', '소프트웨어']],
    ['work', 'Work & Projects', ['project', 'projects', 'work', 'meeting', 'meetings', 'client', 'customer', 'team', 'roadmap',
      'product', 'business', 'engagement', 'venture', '사업', '프로젝트', '업무', '회의', '고객', '조직',
      '계약', '주간보고', '기획', '제품', '채용']],
    ['investing', 'Investing & Markets', ['investing', 'investment', 'investments', 'finance', 'stock', 'stocks', 'equity', 'portfolio', 'etf', 'tqqq', 'qld',
      'qqq', 'sso', 'upro', 'spxl', 'nasdaq', 'nasdaq100', 'kospi', 'kosdaq', 'backtest', 'dividend', 'trading',
      'target price', '투자', '주식', '매수', '매도', '종목', '매매', '배당', '백테스트', '적립식', '증권', '수익률']],
    ['economy', 'Economy & Policy', ['economy', 'economic', 'inflation', 'interest rate', 'treasury', 'tax',
      'policy', 'energy', 'electricity', 'lcoe', 'gdp', '경제', '금리', '국채', '세금', '소득공제', '세액공제',
      '정책', '관세', '환율', '물가', '증여', '상속', '전력', '발전단가']],
    ['life', 'Life & Health', ['health', 'fitness', 'exercise', 'family', 'housing', 'travel', 'habit',
      'daily routine', '생활', '건강', '운동', '가족', '육아', '주거', '주택', '전세', '아파트', '노후',
      '수면', '식단', '루틴', '연금', '영어']],
    ['knowledge', 'Ideas & Learning', ['knowledge', 'ontology', 'obsidian', 'learning', 'education',
      'research', 'paper', 'book', 'reading', 'idea', 'principle', 'philosophy', 'category theory',
      '지식', '온톨로지', '학습', '공부', '독서', '철학', '생각', '원칙', '아이디어', '논문']],
    ['journal', 'Journal', []], ['other', 'Uncategorized', []]
  ];
  const items = definitions.map(([id, label]) => ({id, label}));
  const normalize = text => String(text || '').normalize('NFKC').toLowerCase().replace(/[_/-]+/g, ' ');
  const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rules = definitions.filter(([, , terms]) => terms.length).map(([id, , terms]) => ({id,
    patterns: terms.map(term => /^[a-z0-9 ]+$/.test(term) ? new RegExp('(?:^|[^a-z0-9])' + escape(term) + '(?=$|[^a-z0-9])') : new RegExp(escape(term)))
  }));
  const quoted = text => '“' + String(text).slice(0, 90) + '”';

  function classify(fields) {
    let best;
    for (const rule of rules) {
      const hits = fields.filter(field => rule.patterns.some(pattern => pattern.test(normalize(field.text))));
      const score = hits.reduce((sum, field) => sum + field.weight, 0);
      if (score && (!best || score > best.score)) best = {category: rule.id, score, hits};
    }
    if (!best) return null;
    const reasons = [...best.hits].sort((a, b) => b.weight - a.weight || a.text.localeCompare(b.text, 'en'));
    return {category: best.category, categoryReason: 'Based on ' + reasons.slice(0, 2)
      .map(field => field.basis + ' ' + quoted(field.text)).join(' and ') + '.'};
  }
  function decorate(data) {
    const byId = new Map(data.nodes.map(node => [node.id, node]));
    const metadata = new Map(), parents = new Map();
    const addMetadata = (id, target) => {
      if (!metadata.has(id)) metadata.set(id, new Map());
      metadata.get(id).set(target.id, {text: target.label, basis: target.kind === 'Tag' ? 'tag' : 'topic', weight: 8});
    };
    for (const edge of data.edges) {
      if (['contains', 'records'].includes(edge.kind) && byId.has(edge.source) && byId.has(edge.target)) parents.set(edge.target, edge.source);
      const target = byId.get(edge.target);
      if (['taggedWith', 'about'].includes(edge.kind) && target && ['Tag', 'Topic'].includes(target.kind)) addMetadata(edge.source, target);
    }
    const notesByPath = new Map(data.nodes.filter(node => node.kind === 'Note' && node.path).map(node => [node.path, node]));
    // A note can also have explicit metadata attached to its passages.
    for (const node of data.nodes) {
      const parent = notesByPath.get(node.path);
      if (node.kind !== 'Note' && parent) for (const id of metadata.get(node.id)?.keys() || []) addMetadata(parent.id, byId.get(id));
    }
    const assigned = new Map();
    function area(node, visiting = new Set()) {
      if (assigned.has(node.id)) return assigned.get(node.id);
      if (visiting.has(node.id)) return null;
      visiting.add(node.id);
      const fields = [...(metadata.get(node.id)?.values() || []),
        {text: node.label, basis: node.kind === 'Note' ? 'title' : 'name', weight: 5}];
      const folder = node.path?.split('/').slice(0, -1).join('/');
      if (folder) fields.push({text: folder, basis: 'folder', weight: 2});
      let result = classify(fields);
      if (!result && !['Note', 'Tag', 'Topic'].includes(node.kind)) {
        const parent = notesByPath.get(node.path) || byId.get(parents.get(node.id));
        const inherited = parent && area(parent, visiting);
        if (inherited && inherited.category !== 'other') result = {...inherited,
          categoryReason: 'From source note ' + quoted(parent.label) + '. ' + inherited.categoryReason};
      }
      if (!result && node.kind === 'Note' && (/^\d{4}[-/ ]\d{2}[-/ ]\d{2}(?:\b|$)/.test(node.label) ||
        /(?:^|[/ ])(?:daily|journal|journals|diary)(?:[/ ]|$)|일지|일상/.test(normalize(node.path)))) {
        result = {category: 'journal', categoryReason: 'Based on a dated note title or journal folder.'};
      }
      result ||= {category: 'other', categoryReason: 'No knowledge area matched the available names, tags or topics.'};
      assigned.set(node.id, result); return result;
    }
    return {...data, nodes: data.nodes.map(node => ({...node, ...area(node)}))};
  }
  return {items, decorate};
})();
if (typeof module !== 'undefined') module.exports = GraphCategories;
