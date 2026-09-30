/* Conversation rendering; saved answers remain source snapshots. */
const ChatUI = (() => {
  let activeId = sessionStorage.getItem('obsi-conversation'), renderVersion = 0;
  const thread = () => document.querySelector('#chat-thread');
  const setActive = id => {
    activeId = id;
    sessionStorage.setItem('obsi-conversation', id);
    document.querySelectorAll('#history button').forEach(button =>
      button.classList.toggle('active', button.dataset.id === id));
  };
  const setLayout = hasMessages => $('#page-ask').classList.toggle('has-chat', hasMessages);
  function bubble(question) {
    const row = el('article', undefined, 'chat-message chat-user');
    row.append(el('div', 'You', 'chat-role'), el('p', question, 'chat-bubble'));
    thread().append(row);
    setLayout(true);
  }
  function clearThread() {
    GraphView.dispose(thread());
    thread().replaceChildren();
    setLayout(false);
  }
  function startId() {
    renderVersion++;
    if (!activeId) setActive(crypto.randomUUID().replaceAll('-', ''));
    return activeId;
  }
  async function open(id, freshResultId = null) {
    const ticket = ++renderVersion;
    setActive(id);
    const results = await api('/conversations/' + id);
    if (activeId !== id || ticket !== renderVersion) return;
    clearThread();
    for (const result of results) {
      bubble(result.question);
      const row = el('article', undefined, 'chat-message chat-assistant');
      row.append(el('div', 'Obsi Onto', 'chat-role'));
      const answer = el('div', undefined, 'chat-answer'); row.append(answer); thread().append(row);
      renderAnswer(result, result.id !== freshResultId, answer);
    }
    if (!results.at(-1)?.vault_epoch) thread().append(el('p',
      'Questions from an earlier version are not automatically included in follow-up searches. Ask about the topic again or start a new chat.', 'hint'));
    const latest = results.at(-1);
    ChatGraph.rememberAnswer(latest);
    if (freshResultId && latest?.id === freshResultId) ChatGraph.showAnswer(latest);
    thread().lastElementChild?.scrollIntoView({block: 'start'});
  }
  function pending(job) {
    if (activeId !== job.conversation_id) {
      setActive(job.conversation_id);
      clearThread();
    }
    if (!thread().querySelector('.chat-pending')) {
      bubble(job.question);
      thread().lastElementChild.classList.add('chat-pending');
    }
  }
  async function complete(result) {
    const id = result.conversation_id || result.id;
    if (activeId !== id) return;
    await open(id, result.id);
    await history();
  }
  async function select(id) {
    setActive(id);
    try { await open(id); }
    catch (error) { if (error.status !== 404) throw error; clearThread(); }
  }
  async function history() {
    const items = await api('/conversations');
    const target = $('#history'); target.replaceChildren();
    if (!items.length) target.append(el('p', 'No conversations yet.', 'hint'));
    for (const item of items) {
      const button = el('button', item.question);
      button.dataset.id = item.id;
      button.title = `${item.question} · ${item.turns} ${item.turns === 1 ? 'question' : 'questions'}`;
      button.classList.toggle('active', item.id === activeId);
      button.onclick = () => action(button, async () => {
        QueryUI.clear(); ChatGraph.overview(true); page('ask'); await open(item.id);
      });
      target.append(button);
    }
  }
  function fresh() {
    renderVersion++;
    QueryUI.clear();
    sessionStorage.removeItem('obsi-conversation');
    activeId = null;
    clearThread();
    ChatGraph.overview(true);
    document.querySelectorAll('#history button').forEach(button => button.classList.remove('active'));
    $('#question').value = '';
    page('ask'); $('#question').focus();
  }
  async function restore() {
    if (!activeId) return;
    try { await open(activeId); }
    catch (error) { if (error.status !== 404) throw error; }
  }
  return {startId, pending, complete, select, history, fresh, restore};
})();
