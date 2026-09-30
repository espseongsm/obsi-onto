Obsi Onto의 첫 개발 프리뷰를 공개합니다.

Obsidian에 업무 결정, 투자 생각, 읽은 자료를 기록하다 보면 다시 찾고 싶은 건 단어 하나보다 “왜 그렇게 판단했지?”라는 질문일 때가 많았습니다.

그래서 내 노트에 질문하고, 그 기록의 근거와 연결을 함께 살펴보는 로컬 앱을 만들고 있습니다.

현재 버전에서는:
• 로컬 Obsidian 볼트를 읽기 전용으로 색인합니다.
• 단어·의미·관계 검색을 결합하고, 원문 문단과 줄 번호를 확인할 수 있습니다.
• 전체 3D 지식 지도에서 질문에 관련된 노드로 이동한 뒤, 사용된 근거를 한눈에 보여줍니다.
• 태그·제목 등을 기준으로 10개 지식 분야를 서로 다른 색으로 표시합니다.
• 원문 근거 타일, 대화 기록, Light·Dark·AI 테마를 제공합니다.

LLM API 없이도 근거 검색과 지식 검색을 사용할 수 있습니다. 생성형 답변이 필요하면 선택적으로 LLM을 연결합니다.

Python/FastAPI, SQLite FTS5, sqlite-vec, RDFLib/SHACL, Three.js로 구성했습니다. 아직 초기 개발 버전이며 검색 품질과 사용성을 계속 검증하고 있습니다. 영상은 개인 기록을 사용하지 않은 가상 노트 데모입니다.

최신 구현과 실행 방법:
https://github.com/espseongsm/obsi-onto/tree/codex/initial-preview

여러분은 자신의 노트에 어떤 질문을 하고 싶으신가요? 사용해 보신 의견과 기여를 환영합니다.

#Obsidian #KnowledgeGraph #RAG #OpenSource #SnowflakeDataSuperhero
