# 구현 구조와 흐름

## 구성

```mermaid
flowchart LR
  U[로컬 웹 화면] --> A[FastAPI · 127.0.0.1]
  V[Obsidian 볼트 · 읽기 전용] --> F[안정된 파일 읽기]
  W[macOS FSEvents] --> Q[2초 변경 묶기 · 단일 작업 큐]
  M[시작 · 복귀 · 30분 메타데이터 대조] --> Q
  A --> Q
  Q --> F
  F --> P[문단 · 속성 · 명시 링크 · 출처 파싱]
  P --> S[(SQLite · 공통 노트 버전)]
  P --> E[로컬 ONNX 임베딩 · 변경 입력 캐시]
  E --> S
  S --> T[FTS5]
  S --> B[sqlite-vec]
  S --> G[RDFLib · SHACL]
  A --> R[질문 · 영역 · 기록일 필터]
  T --> R
  B --> R
  G --> R
  R --> C[순위 결합 · 원문 해시/줄 검증]
  C --> O[원문 근거 또는 선택적 생성 답변]
  O --> H[(당시 근거 스냅샷)]
  O --> U
```

## 갱신의 공개 경계

```mermaid
sequenceDiagram
  participant FS as 파일 시스템
  participant Q as 변경 큐
  participant I as 색인기
  participant DB as SQLite
  participant QA as 질문 처리
  FS->>Q: 생성·수정·이동·삭제 알림
  Q->>DB: 기존 노트를 pending으로 표시
  Note over Q: 마지막 알림 후 2초 대기
  Q->>I: 대상 파일만 처리
  I->>FS: 안정적으로 읽기 · 해시
  I->>I: 변경 입력만 파싱·임베딩
  I->>FS: 원문을 다시 읽어 최신 해시 확인
  alt 처리 중 원문 변경·읽기 실패
    I->>DB: 대기·오류 상태 보존
    I->>Q: 재시도
  else 같은 버전
    I->>DB: 문단·FTS·벡터·명시 링크 트랜잭션 갱신
    I->>DB: ready와 공개 세대 기록
  end
  QA->>DB: ready이며 노트·문단 버전이 같은 근거 조회
  QA->>FS: 선택된 원문 위치·해시 검증
  QA->>DB: 검증된 근거 스냅샷 저장
```

질문·색인·모델 준비는 하나의 작업 슬롯을 공유합니다. 정기 검사는 질문 중에 기다립니다. OS 이벤트는 별도로 기록하므로 처리 중 발생한 변경도 다시 큐에 남습니다. 같은 데이터 폴더를 두 프로세스가 동시에 사용하는 것은 파일 잠금으로 막습니다.

## 의미 관계와 근거

```mermaid
flowchart TD
  N[Note · 내부 ID / 경로 / 버전] -->|contains| S[Section · 원문 / 줄 / 해시]
  S -->|linksTo · 명시 링크| N2[다른 Note]
  S -->|taggedWith| T[Tag]
  S -->|about · frontmatter| P[Topic]
  S -->|records · 사용자 확인 후| C[Claim 또는 Activity]
  C -->|about| P
  C -->|source| S
  X[외부 Source · 내용 미검증] -->|source| S
  L[관계 근거 레코드] -->|source| S
  L -->|target| N2
```

- RDF는 SQLite에서 투영하며 독립적인 최신 상태를 갖지 않습니다. 공개 세대가 같으면 검증한 그래프를 재사용합니다.
- 외부 URL·문서 간 링크·동일 주제라는 이유만으로 인과 관계나 `citesAsEvidence`를 만들지 않습니다.
- LLM 후보는 확인 전 그래프에 넣지 않습니다. 명시적 `revises`/`citesAsEvidence`의 범용 매핑은 후속 확장입니다.
- SHACL은 출처·타입·관계 구조를 확인합니다. 주장 내용의 진실 여부를 확인하는 도구는 아닙니다.

## 현재의 검색 선택

FTS5는 단어 및 한글 2글자 조각을 검색합니다. sqlite-vec는 범위가 맞는 현재 모델 벡터의 코사인 거리를 계산합니다. 정확 검색 결과의 순위, 의미 검색 순위, 실제 관계 경로의 순위를 각각 가중 RRF로 결합합니다. 점수와 거리를 직접 더하지 않습니다. 제목만 있는 문단이나 frontmatter 자체는 최종 답변 발췌에서 제외합니다.

기본 순위 가중치는 단어 1, 의미 0.5, 관계 0.15입니다. 작은 가상 개발 표본에서 보정한 값이며 일반적인 최적값이 아닙니다. 관계만 많이 연결된 문서가 질문과 직접 관련된 문단을 밀어내는 문제를 줄이려는 초기값입니다.

현재 벡터 검색은 정확 거리 비교 방식이므로 노트가 커질수록 비용도 증가합니다. 사용자 볼트에서 성능 측정 전까지 대규모 처리 성능을 보장하지 않습니다.

## 그래프 화면과 답변의 연결

```mermaid
flowchart LR
  RDF[RDFLib · 현재 공개 세대] --> GV[graph_view.py · 출처 있는 관계 투영]
  GV --> API[GET /api/graph · 이름/본문 글자 찾기]
  API --> MAP[지식 그래프 · 현재 색인]
  R[검색 · 원문 검증] --> E[검증한 답변 문단]
  E --> GV
  GV --> SNAP[답변 범위의 그래프 스냅샷]
  SNAP --> DB[(SQLite runs.payload)]
  SNAP --> ANS[답변 근거 그래프]
  DB --> OLD[과거 질문 · 당시 그래프]
  MAP --> UI[graph.js · SVG · 최대 80개 노드]
  ANS --> UI
  OLD --> UI
  UI --> PICK[노드 선택 · 직접 이웃 강조]
  PICK <--> CARD[근거 카드 · 원문 위치]
```

- `graph_view.py`는 RDF의 문서 구조·명시 관계·검토 관계를 보여줍니다. 벡터 간 거리로 새 관계를 생성하지 않습니다. 원문을 찾은 검색 경로는 별도 노드 속성으로 표시합니다.
- 질문 그래프는 검증한 근거 문단과 연결된 노트·주제·태그·판단/활동으로 제한합니다. 다른 영역/기간의 문단을 확장하지 않습니다. 과거 답변의 그래프를 현재 RDF로 재구성하지 않습니다.
- 현재 그래프와 답변 그래프는 하나의 렌더러를 공유합니다. 최대 80개 노드·200개 선만 전달하고 배치는 최초 한 번 계산합니다. 노드 선택·확대·이동에는 서버 및 모델 호출이 없습니다.
- 현재 그래프 조회는 색인 게시와 동일한 작업/저장소 잠금 범위에서 수행합니다. 갱신 대기 상태인 대상 노트를 향한 명시 링크도 RDF 공개에서 제외해 잘못된 관계와 SHACL 실패를 막습니다.

## Finder 폴더 선택

```mermaid
flowchart LR
  B[Finder로 선택] --> A[로컬 API · 요청 토큰 확인]
  A --> P[macOS 폴더 선택 창]
  P -->|선택| V[경로 입력란에 반영]
  P -->|취소| K[기존 입력 유지]
  V --> S[볼트 연결 · 설정 저장]
  S --> R[기존 폴더 등록·색인 절차]
```

`folder_picker.py`는 고정 AppleScript와 별도 경로 인자로 폴더 선택 창을 엽니다. 폴더 선택 중에는 색인 작업 잠금을 점유하지 않으며 창 하나만 허용합니다. 폴더를 고르는 동작 자체는 볼트 연결을 변경하거나 노트를 색인하지 않습니다.

## .env와 GPT-5.6 Luna 생성 답변

```mermaid
flowchart LR
  ENV[프로젝트 .env · 기존 환경변수 우선] --> MAIN[main.py · 설정 로딩]
  MAIN --> C[Config · 모델/주소/외부 허용]
  Q[질문] --> S[로컬 FTS5 · MiniLM · 관계 검색]
  S --> V[원문 검증 · 필요한 근거 문단]
  C --> G[Generator · 설정된 API]
  V --> G
  G --> L[gpt-5.6-luna · Azure OpenAI 호환 API]
  L --> J[JSON 응답 · 인용 ID 검증]
  J --> UI[생성 답변 · 근거 그래프]
  J -->|생성/검증 실패| F[원문 발췌로 복귀]
```

인증 키는 서버에서만 읽습니다. 질문과 검색된 근거 문단을 설정한 API에 보내며 로컬 임베딩·벡터 색인의 구성은 유지합니다. `.env` 변경은 서버 재시작 시 적용합니다. 실제 연결은 가상 근거로 확인했고, 인용 ID 검증은 답변 내용의 의미적 정확성을 보장하지 않습니다.

## 볼트 설정 변경과 예상 질문

```mermaid
flowchart TD
  SAVE[볼트 연결·설정 저장] --> CHANGE{경로·제외·날짜 규칙 변경?}
  CHANGE -->|같음| KEEP[저장된 예상 질문 유지]
  CHANGE -->|변경·최초 연결| CLEAR[기존 질문 비우기 · 생성 예약 저장]
  CLEAR --> SCAN[작업 큐 · 설정을 적용한 색인 대조]
  SCAN --> SAMPLE[준비된 노트 최대 24개 · 영역별 표본]
  SAMPLE --> VERIFY[원문 버전·해시 확인]
  VERIFY --> MODEL{모델 사용·표본 전송 허용?}
  MODEL -->|예| LLM[표본 일부 → 예상 질문 최대 6개]
  MODEL -->|아니오| FALLBACK[실제 노트 제목으로 기본 질문]
  LLM --> VALIDATE[질문 대상·근거 ID·구조 검증]
  VALIDATE -->|실패| FALLBACK
  VALIDATE -->|성공| RECHECK[생성 후 원문 재확인]
  RECHECK -->|변경| FALLBACK
  RECHECK -->|유효| DB[(SQLite · 질문·시각·참고 위치)]
  FALLBACK --> DB
  DB --> STATUS[상태 API · 화면 자동 갱신]
  STATUS --> CLICK[선택 시 질문·영역 입력]
  CLICK --> ASK[사용자가 질문 실행]
```

`app/suggestions.py`가 기존 검색의 문단 필터·원문 검증과 생성 API를 재사용합니다. 모델 전송은 노트당 제목·문단 제목 각 120자와 본문 600자 이내, 기록일·근거 ID로 제한합니다. 표본의 원문은 새 질문 캐시에 복제하지 않습니다. 제외 설정이 바뀌면 이전 질문을 바로 숨기며, 일반 노트 변경 때는 질문을 자동 재생성하지 않습니다. 모델 호출 실패는 기본 질문으로 복귀하고 화면 조회 때마다 재시도하지 않습니다.

외부 모델의 예상 질문 생성은 `OBSI_ALLOW_EXTERNAL_SUGGESTIONS`로 별도 제어합니다. 꺼져 있으면 기존 답변 모델 연결을 유지하면서 예상 질문만 로컬 기본 질문으로 처리합니다. 이 허용을 켜고 재시작하면 저장된 기본 질문을 한 번 갱신합니다.
