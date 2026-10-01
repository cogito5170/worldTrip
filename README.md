# World Trip — 세계여행 계획 엔진

출처 있는 가격 원장으로 여러 나라 여행을 짜는 **엔진**(계산 · 판정 · API · MCP). 화면은
[gentleMonster](https://github.com/cogito5170/gentleMonster) 의 `gentle_monster/apps/worldtrip/` 이다 —
`worldtrip app` 이 그 화면을 받아 엔진에 붙인 **어플리케이션**으로 띄운다. 표준 라이브러리만 쓴다.

## 어플리케이션으로 쓰기

```bash
pip install git+https://github.com/cogito5170/worldTrip      # 또는 이 저장소에서: pip install .
worldtrip app                                                # 화면을 받아(처음 한 번) 붙이고 브라우저를 연다 → http://127.0.0.1:8766/
```

- 처음 뜰 때 gentleMonster `main` 의 `gentle_monster/apps/worldtrip/` 를 받아 `~/.cache/worldtrip/frontend/` 에 둔다
  (GitHub tar.gz → 안 되면 git sparse clone). 다시 받으려면 `--update`, 다른 갈래는 `--ref <브랜치>`,
  이미 받아 둔 gentleMonster 가 있으면 `--frontend <gentleMonster>/gentle_monster/apps/worldtrip`.
- 받은 화면에 `index.html · app.js · app.css` 가 다 없으면 붙이지 않고 실패로 끝난다(반쯤 받은 화면을 '붙었다' 고 하지 않는다).
- 화면은 설치 가능한 PWA 다 — 브라우저의 '앱 설치' 로 홈 화면·독에 둘 수 있다.
- 다른 곳(예: GitHub Pages)에 올린 화면이 이 엔진을 부르게 하려면 `WORLDTRIP_ALLOWED_ORIGINS=https://그곳` (CORS) 를 세우고
  화면 주소에 `?api=https://엔진주소` 를 붙인다.

```bash
docker build -t worldtrip . && docker run -p 8766:8766 -e WORLDTRIP_TOKEN=... -v wt-ledger:/ledger worldtrip   # 화면은 빌드할 때 받는다
worldtrip serve [--frontend 폴더]   # 엔진만(API + MCP). 화면 폴더를 주면 같이 내준다
worldtrip mcp                       # MCP stdio:  claude mcp add worldtrip -- worldtrip mcp
worldtrip plan 요청.json · worldtrip city kyoto · worldtrip status
```

| 무엇 | 어디 |
|---|---|
| 엔진: 카탈로그(트리·그래프) · 생성자 · 심판 · 원장 · API · MCP | 이 저장소 `worldtrip/` |
| 화면: 계획 · 경로 · 날짜 · 예산 보드 · 도시 펼침 · 청사진(A3) · 탐색 · 원장 | gentleMonster `gentle_monster/apps/worldtrip/` |
| 화면 검사: gentleMonster 엔진 심판의 V(넘침 · 대비 · 12px · 외부 요청 0 · …) | gentleMonster `python3 -m gentle_monster.apps.check` |
| 값 검사: T0–T7 (돈을 날짜별로 다시 더한다) | 이 저장소 `worldtrip/judge.py` |

## worldplan 과 같이 쓰기

[worldplan](https://github.com/cogito5170/worldplan)(여러 시간대 회의 배치)과 같은 갈래다 — 엔진은 각 저장소에, 화면은
gentleMonster `apps/` 에. worldplan 의 도우미(물어보기)는 이 엔진이 깔려 있으면 `worldtrip` MCP 도구를 숙고층(Claude 또는 Gemini)에
같이 붙인다 — "11월에 파리 · 로마 일주일, 그동안 서울 팀과 회의는 언제?" 를 한 대화에서 묻는다.

맥에서 둘을 함께 깔고 Gemini CLI 에 붙이기(worldplan 쪽 스크립트가 이 저장소도 깐다):

```bash
curl -fsSL https://raw.githubusercontent.com/cogito5170/worldplan/main/scripts/setup_mac.sh | bash
worldtrip app        # 세계여행 화면
worldplan app        # 회의 배치 화면 + 도우미
gemini               # worldplan · worldtrip 도구가 붙은 대화창
```

## 먼저 알아야 할 것 — 데이터의 한계

| | |
|---|---|
| 조사 | 2026-10-01, 이 세션 한 번. 도시 20 · 연결 34 · 가격 211개 · 환율 9개 |
| 확인수준 | **211개 전부 검색 결과 조각(snippet).** 원문을 읽은 것은 0개 — 페이지 읽기가 프록시에 막혔다 |
| 빈 곳 | 검색 한도(200회)가 차서 **오사카·서울·타이베이·호찌민·싱가포르·마드리드는 비어 있고, 바르셀로나는 명소 1곳뿐이다.** 치앙마이·마드리드는 연결도 없다 |
| 지어내지 않았다 | 빈 칸은 0 이 아니라 `null`(모름)이다. 그 도시에 묵는 계획은 '비용 모름' 으로 거절된다 |
| 채우는 법 | 요청의 `overrides` 에 아는 값을 넣는다(확인수준이 `user` 로 찍힌다). 조사를 더 하려면 `docs/데이터_스키마.md` 꼴로 `worldtrip/data/*.json` 을 늘린다 |

## 어떻게 고르나 — 트리 × 그래프

- **트리** 지역 → 나라 → 도시 → 명소·숙소·식비·교통·식당·활동·고민·후기.
  나라 노드는 '1박+식비 최솟값' 을 들고 있어, 최소 박수만으로 예산을 넘는 조합을 그래프 탐색 전에 자른다.
  `country_contiguous`(기본 켬)면 같은 나라는 한 번에 몰아서 간다.
- **그래프** 도시 = 노드, 이동 = 간선(항공·열차·야간열차). 고른 도시를 **직접 연결된 간선만으로** 잇는 순서를
  Held-Karp(부분집합 DP)로 전수한다. 가중치 = 최대가(원) + 시간가치 × 시간. 파리→서울→로마 같은 경유는 만들지 않는다.
- **조합** must + 후보의 부분집합을 `max_cities` 까지 전수. 목표: 예산 안 → 관심사 점수 최대 → 이동 시간 최소 → 최대가 최소.
- **밤 배분** 최소 박수를 깔고, 남은 밤을 '하루 더 머물면 보게 되는 명소 점수' 가 큰 도시에 하나씩 준다(**탐욕 — 최적이라고 말하지 않는다**).

## 심판 (LLM 아님) — T0–T7

T0 구조 · T1 간선이 원장에 있나 · T2 날짜 이음·박수·최소 박수 · T3 must/avoid/나라 연속 · T4 하루 용량 ·
T5 핵심 비용을 다 알고 예산 안인가 · T6 **생성자 합 = 심판의 날짜별 재합** · T7 근거.
예산은 기본으로 **범위의 최대가**로 판정한다(`budget_basis: "mid"` 면 가운데값, 그때는 최대가로 얼마 넘을 수 있는지 같이 말한다).

## 잰 것 (2026-10-01, 이 컨테이너)

| 무엇 | 결과 | 어떻게 · 한계 |
|---|---|---|
| 검사 | 35개 통과 | `python3 -m unittest discover -s tests -t .` (화면 받기 · 경로 탈출 · CORS 포함) |
| 순서 독립 대조 | 무작위 80세트에서 Held-Karp = 순열 전수(원장 간선을 직접 찾아 더함) | 길이 있는 경우·없는 경우가 각각 10개 미만이면 검사가 실패한다(사소한 표본 막기) |
| 돈 독립 대조 | 무작위 요청 40개에서 생성자 합 = 심판 날짜별 재합, ACCEPT·REJECT 각 5개 이상 | 생성자의 방 수 계산을 일부러 틀리면 T6 가 잡아 REJECT 가 되는 것도 확인 |
| 망가뜨린 계획 | 12가지 망가뜨림(T0–T6) 전부 해당 검사로 거절 | `tests/test_judge.py` |
| MCP 호환 | 공식 MCP Python SDK 2.2.0 클라이언트로 stdio·HTTP 둘 다 plan_trip 성공(협상 2025-06-18) | |
| 어플리케이션 | `worldtrip app` 으로 띄운 엔진+화면에 gentleMonster 화면 검사를 걸어 V 전부 통과(375·1440 px, 계획·탐색) | 진짜 엔진 응답으로 잰 것. 글꼴은 대체 글꼴(Archivo 미설치)로 보였다 |

## 정직한 한계

- **값은 검색 조각이다.** 화면의 모든 수 옆에 '조각' 표시가 있고 결과 맨 위에 경고가 뜬다. 예약 전에 출처를 확인하라.
- 장거리 항공 범위가 넓다(서울–런던 220–1,600 USD). 그래서 최대가 판정이면 유럽 2인 여행이 쉽게 예산을 넘는다 — 엔진 탓이 아니라 데이터의 폭이다.
- 원장에 명소가 도시당 4–7곳뿐이라 긴 체류는 '자유일' 이 많다. 그날은 활동을 **제안**만 하고 예산에 넣지 않는다.
- 입국 요건·실시간 좌석·휴관일·날씨는 보지 않는다(응답의 `not_checked`).
- 선행조사를 못 했다 — `docs/선행조사.md`.
