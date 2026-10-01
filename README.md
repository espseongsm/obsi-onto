# Obsi Onto

**Ask your notes. See the evidence. Explore the connections.**

Obsi Onto turns your local Obsidian vault into a searchable knowledge map. Ask questions, inspect the original passages behind an answer, and follow the evidence through an interactive 3D graph.

Your original notes stay read-only. An LLM is optional: evidence search and knowledge exploration work without an API key.

> Early development preview. Currently tested on macOS. The interface and new generated answers default to English; source passages and saved content keep their original language.

![Explore the knowledge map and inspect original sources](https://raw.githubusercontent.com/espseongsm/obsi-onto/5897a1846c19d949fada7802115c3f7fd669123a/docs/media/obsi-onto-preview.gif)

[Watch the video](https://github.com/espseongsm/obsi-onto/blob/5897a1846c19d949fada7802115c3f7fd669123a/docs/media/obsi-onto-preview.mp4)

The demo uses fictional notes and evidence search without an LLM.

## Main features

- **Ask your vault:** find relevant notes or generate cited summaries when an LLM is connected.
- **Inspect the evidence:** open source tiles to see the original passage, note path, date, and line numbers.
- **Explore in 3D:** start with the whole vault, follow a question's evidence, and inspect connected nodes.
- **Continue conversations:** reopen saved chats and ask follow-up questions.
- **Review differences:** compare potentially conflicting passages and clarify the context before continuing.
- **Make it yours:** choose Light, Dark, or AI, and resize the chat and graph panes.

## Get started

You need Git, [uv](https://docs.astral.sh/uv/), and Python 3.12–3.13. The commands below install Python 3.12 through uv. The first dependency or local search-model download needs internet access.

```sh
git clone https://github.com/espseongsm/obsi-onto.git
cd obsi-onto
uv sync --locked --python 3.12
uv run main.py
```

The app opens in your browser at [http://127.0.0.1:8765](http://127.0.0.1:8765). Keep the terminal running while using it. Open this address instead of opening `web/index.html` directly. Press `Ctrl+C` in the terminal to stop the app.

### Connect your notes

1. Click **Choose in Finder** or enter your vault folder path, then **Connect vault**. You can also select **Explore sample notes first**.
2. To exclude folders before indexing, connect through **Vault settings** and save the path and exclusions together.
3. For semantic search, use **Vault settings → Prepare local search model**. Basic text and relationship search work before the model is ready.
4. Wait for indexing, then enter a question or select one from **Questions from your vault**.

Local and iCloud vaults are supported. For iCloud notes, use **Keep Downloaded** in Finder if files are not available locally.

## How to use it

### Ask and check the sources

Open **Ask your notes**, enter a question, and click **Ask question** or press `⌘/Ctrl + Enter`. Choose **All notes**, **Work**, **Investment**, or **Personal**, and optionally set a recording-date range.

Try questions using names from your own notes:

- “Why did Project B change direction?”
- “How has my investment thesis for Company A changed?”
- “What did I record about this project last week?”

Read the answer first. Expand **Sources** to reveal the evidence tiles, then select a tile or citation to inspect the original passage. **Open in Obsidian** opens the note; **View in graph** highlights the evidence.

The answer's **3D evidence graph** starts open and can be collapsed. **Sources** and **Search & verification** start collapsed. Use **New chat** to start another conversation or **Recent chats** to reopen one.

With no usable LLM, **Evidence search mode** returns source excerpts instead of generated summaries. The graph and source details remain available.

New generated answers default to English even when you ask in another language. Request another answer language explicitly when needed.

### Explore the knowledge graph

The map starts with the whole vault. When you ask a question, the camera follows relevant evidence and ends with that answer's evidence in view.

| Action | Control |
|---|---|
| Rotate / zoom / pan | Drag / scroll / right-button drag |
| Inspect a connection | Select a node, label, or entry in the node selector |
| Replay the evidence route | **Follow evidence** |
| Fit this answer's evidence | **All evidence** |
| Explore manually | **Stop tour**, or move the camera yourself |

Use **Whole vault** and **Answer evidence** to switch views. **Knowledge search** helps you find notes and connections directly. Colors distinguish ten knowledge areas; they are navigation labels, not proof that two notes mean the same thing.

On wide screens, chat and graph start at **1:2**. Drag the divider to resize them; **Reset split** restores the default. On smaller screens, the graph appears below the chat. Select **Light**, **Dark**, or **AI** to change the interface and graph together.

### Clarify a difference

When the app finds a possible conflict, compare the two passages and choose a source, keep both contexts, or explain the difference. You can also defer and receive a partial answer based on source excerpts. Your choice applies to that answer and does not edit your notes.

## Optional: connect an LLM

An LLM enables generated summaries, comparisons, and relationship suggestions. It is not required to connect a vault or search your notes.

1. Stop the app. Create a private `.env` file without overwriting an existing one:

   ```sh
   cp -n .env.example .env
   chmod 600 .env
   ```

2. Open `.env` in a text editor and update the existing entries:

   ```dotenv
   OBSI_LLM_URL=https://your-provider.example/v1
   OBSI_LLM_API_KEY=replace-with-your-api-key
   OBSI_LLM_MODEL=your-provider-model-id
   OBSI_ALLOW_EXTERNAL_GENERATION=1
   OBSI_ALLOW_EXTERNAL_SUGGESTIONS=0
   ```

3. Use your provider's OpenAI-compatible API base URL and an available model ID. Leave `/chat/completions` off the base URL. Restart with `uv run main.py`, then check the model status in **Vault settings**.

External generation sends your question, selected evidence, clarification, and recent conversation context to that provider. Automatic external question suggestions require the separate `OBSI_ALLOW_EXTERNAL_SUGGESTIONS=1` setting. Keep your key in `.env`; never commit it.

If the model cannot connect, the app continues in evidence search mode. Local model setup and all configuration options are in the [technical specification](prd.md#9-model-configuration).

## Notes and troubleshooting

Your vault is read-only. The app saves its index and conversations locally and updates the index as notes change. Saved answers preserve their original evidence, so they may differ from the current note.

Only one vault can be connected at a time. To switch from sample notes or another vault, use **Vault settings → Delete local index and history**, then connect the new folder. This removes app history and the index, so save anything you want to retain first; it does not delete your original notes.

| Issue | Try this |
|---|---|
| The page does not open | Keep `uv run main.py` running and visit the local address above. |
| The port is already in use | Stop the previous server, or run `uv run main.py --port 8766`. |
| Search finds no evidence | Use names in your notes and broaden the scope or date range. |
| Changes are missing | Open **Notes & index → Refresh now**; use **Deep scan** if needed. |
| The LLM is unavailable | Check the key, API base URL, model ID, and transmission setting, then restart. |

For storage, deployment boundaries, APIs, implementation details, and development checks, see the [PRD and technical specification](prd.md). The [development log](daily-development-report.md) records changes and verification.
