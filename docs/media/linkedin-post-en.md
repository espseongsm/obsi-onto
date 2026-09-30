Sharing the first development preview of Obsi Onto.

My Obsidian notes contain work decisions, investment ideas, and things I have read. When I revisit them, the question is often “Why did I decide that?”

I am building a local app to ask questions about those notes and explore the original evidence and its connections.

The current preview includes:
• Read-only indexing of a local Obsidian vault.
• Keyword, semantic, and relationship search with original passages and line numbers.
• A 3D knowledge map that follows relevant nodes and then frames the evidence used for a question.
• Ten knowledge areas, colored using rules based on tags, titles, and other available metadata.
• Source tiles, saved conversations, and Light, Dark, and AI themes.

Evidence and knowledge search work without an LLM API key. You can optionally connect an LLM for generated answers.

Built with Python/FastAPI, SQLite FTS5, sqlite-vec, RDFLib/SHACL, and Three.js. This is an early development preview; retrieval quality and usability are still being evaluated. The video uses fictional notes, not personal records.

Latest implementation and setup:
https://github.com/espseongsm/obsi-onto/tree/codex/initial-preview

What would you like to ask your own notes? Feedback and contributions are welcome.

#Obsidian #KnowledgeGraph #RAG #OpenSource #SnowflakeDataSuperhero
