# Jeon's Drill

강의자료(PDF/PPT)를 넣으면 시험 문제를 끝없이 내주는 AI 코딩 에이전트용 스킬.
객관식 → 단답식 → 서술형 순서로 개념별로 승급하고, 틀리면 해설과 질문 응답, 진도는 파일에 저장된다.
벼락치기용 **I'm fucked. 나는 좆됐어.** 모드 포함.

> An agent skill for Codex / Claude Code that turns your lecture slides (PDF/PPTX) into an endless, adaptive quiz.
> Concepts climb from multiple choice → short answer → essay; misses get explained; progress is saved to disk.
> Includes a cram mode ("I'm fucked" mode) with summaries, a diagnostic sweep, batched questions and a final check before the exam.
> Instructions and output are in Korean.

## 특징

- **범위를 빠짐없이:** 자료에서 개념 목록을 먼저 만들고 거기서 출제. 앞 강의만 계속 나오는 일이 없음
- **출제 순서는 스크립트가 결정:** 랜덤, 승급·강등, 객관식 정답 위치를 모델이 아니라 코드가 정함
- **자료 기준 출제·채점:** 모든 해설에 `[출처: 03_트리 p.14]`, 자료 밖 보충은 따로 표시
- **확신도 체크:** 객관식은 `확실/애매/찍음`. 찍어서 맞힌 건 승급 안 됨
- **과목 유형별 문제:** 용어·원리·수학·코드. 계산과 코드 출력은 실제로 실행해서 정답 확정
- **손코딩 채점:** 문법 오류는 줄 번호 짚어서 감점, 숨겨진 테스트로 로직 검사
- **약점 집중, 정정, 통계**

## 설치

필요한 것: Python 3.9+, Git

```bash
pip install pymupdf python-pptx sympy
```

**Codex**

```powershell
# Windows (PowerShell)
git clone https://github.com/Seiun03/jeons-drill "$HOME\.agents\skills\jeons-drill"
```
```bash
# macOS / Linux
git clone https://github.com/Seiun03/jeons-drill ~/.agents/skills/jeons-drill
```

**Claude Code**: 위 경로 대신 `~/.claude/skills/jeons-drill`에 clone.

설치 후 Codex를 다시 열고 `/skills`에 `jeons-drill`이 보이면 성공.
업데이트는 그 폴더에서 `git pull`.

## 사용법

1. 과목마다 빈 폴더를 하나 만들고 (예: `운영체제`), 그 폴더에서 Codex를 연다.
2. **처음에는 `$jeons-drill`만 입력한다.** 강의자료를 대화창에 첨부하지 말 것 (토큰 낭비).
3. Codex가 `materials/` 폴더를 만들어 열어 주면, 강의자료 PDF/PPTX를 거기에 넣고 "넣었어"라고 한다.
   파일명 앞에 강의 번호가 있으면 좋다 (`01_개요.pdf`, `02_프로세스.pptx`). 없으면 Codex가 이름을 제안한다.
4. 자료 변환과 개념 목록 생성이 끝나면 목록을 한 번 훑어보고 시작한다. (과목당 한 번)

다음부터는 그 폴더에서 `$jeons-drill`만 입력하면 이어서 문제가 나온다.
자료를 추가할 땐 `materials/`에 넣고 "자료 추가"라고 하면 된다.

(선택) 과목 폴더에 `exam_info.md`를 만들어 시험 범위, 교수님 강조 부분, 문제 형식, 족보를 적어 두면 출제에 반영된다.

문제 중에 쓸 수 있는 말: `다음` `패스` `힌트` `통계` `3강만` `전체` `약점 모드` `핵심만` `정정` `마지막 점검` `그만`

객관식 답은 `2 확실` / `2 애매` / `2 찍음`.

## 두 가지 모드

| | 일반 모드 (기본) | I'm fucked. 나는 좆됐어. 모드 |
|---|---|---|
| 켜기 | 기본값 | 드릴 중에 `i'm fucked` / `나 좆됐어` |
| 승급 | 2연속 정답 (서술형은 1번) | 1번 정답 ('애매'는 승급 안 함) |
| 시작 | 바로 문제 | 시험 시각 확인 → 강의별 요약본 → 진단 라운드 |
| 출제 | 한 문제씩 | 객관식·단답 5개 묶음 + 서술형 1~2개 |
| 숙련 복습 | 가끔, 복합·다각도 문제 포함 | 거의 없음. 시험 2시간 전 마지막 점검 제안 |
| 시간 계획 | - | 남은 시간 대비 소화율 계산, 부족하면 핵심만 제안 |

진도는 두 모드가 공유한다. `일반 모드`라고 하면 돌아온다.

## 조정

`scripts/progress.py` 위쪽의 `MODES`에서 모드별 숫자를 바꾼다.

| 값 | 의미 |
|---|---|
| `promote` | 몇 번 연속 맞혀야 승급하는지 |
| `mastered_weight` | 숙련 개념이 얼마나 자주 나오는지 |
| `combo_prob` | 복합 문제 확률 |
| `essay_inject` | 중요 개념을 낮은 단계에서 서술형으로 낼 확률 |
| `FINAL_CHECK_HOURS` | 시험 몇 시간 전에 마지막 점검을 제안할지 |

## 구조

```
SKILL.md                     전체 흐름 (에이전트가 읽는 지시문)
references/question-types.md 문제 유형, 채점 기준, 출력 형식
references/schemas.md        개념 목록 형식, 개념 나누는 기준, 중요도 기준
references/cram-mode.md      좆됐어 모드 흐름
scripts/extract.py           PDF/PPTX → 페이지별 텍스트, 그림 페이지 추출
scripts/progress.py          출제 스케줄러, 승급·강등, 통계
agents/openai.yaml           ChatGPT 앱 표시 이름
```

## License

MIT
