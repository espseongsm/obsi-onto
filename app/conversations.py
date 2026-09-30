"""Local conversation views and bounded follow-up context over saved runs."""

import json
import re


def turns(store, conversation_id, limit=50):
    rows = store.rows(
        "SELECT payload FROM runs WHERE COALESCE(json_extract(payload, '$.conversation_id'), id)=? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (conversation_id, limit),
    )
    return list(reversed([json.loads(row["payload"]) for row in rows]))


def recent(store):
    rows = store.rows(
        "SELECT id, created_at, question, "
        "COALESCE(json_extract(payload, '$.conversation_id'), id) AS conversation_id "
        "FROM runs ORDER BY created_at DESC, id DESC"
    )
    groups = {}
    for row in rows:
        cid = row["conversation_id"]
        if cid not in groups:
            groups[cid] = {
                "id": cid,
                "question": row["question"],
                "updated_at": row["created_at"],
                "turns": 0,
            }
        groups[cid]["question"] = row["question"]  # Oldest question is the title.
        groups[cid]["turns"] += 1
    return list(groups.values())[:50]


def context(store, conversation_id, vault_epoch):
    if not conversation_id:
        return []
    return [
        {
            "question": run["question"][:300],
            "answer": " ".join(s["text"] for s in run.get("sentences", []))[:600],
        }
        for run in turns(store, conversation_id, limit=20)
        if run.get("vault_epoch") == vault_epoch
    ]


_REFERENCE = re.compile(
    r"^(?:그때|그 후|그후|그건|그것|그게|그 회사|그 프로젝트|이 결정|이어서|앞서|방금|"
    r"왜|더 자세히|그럼|그래서|and |what about |why |then |that |it )",
    re.IGNORECASE,
)


def retrieval_question(question, previous):
    """Add the previous subject only for an explicit referential follow-up."""
    if previous and _REFERENCE.search(question.strip()):
        return f"{question} {previous[-1]['question'][:200]}"
    return question
