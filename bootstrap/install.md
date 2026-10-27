# 하네스 까는 순서 (Claude가 따라 할 순서)

사용자가 「이 프로젝트에 하네스 C 깔아 줘」처럼 말하면 아래 순서로 합니다.
세트 이름: `A-research`(글·조사) / `B-app`(앱·코드) / `C-submission`(제출물).

## 0. 먼저 확인
- 세트를 말하지 않았으면 사용자에게 A·B·C 중 무엇인지 여쭙니다.
- 이 프로젝트의 저장소 이름(owner/name)을 `git remote -v`로 확인합니다.
- 이미 옛 장치(한국어 지킴이·저장소 지킴이·시작 안내·끝 점검 훅)가 `.claude/settings.json`에 있으면
  목록을 사용자에게 보여 드리고, 하네스 훅으로 바꿔도 되는지 여쭙니다(같은 일을 두 번 하지 않게).
- PC에서 하네스 플러그인을 이미 켰다면 프로젝트에 또 깔지 않습니다(훅이 두 번 돕니다).

- 윈도 PC면 Git for Windows(git과 bash가 같이 깔림)와 파이썬(3.8 이상)이 있는지 봅니다.
  `bash --version`, `python --version`(또는 `py --version`). 없으면 사용자에게 설치를 부탁합니다.
  bash가 없으면 하네스 훅이 돌지 않습니다(일은 막지 않고 장치만 꺼짐). 파이썬이 없으면 시작할 때
  「[장치 상태] 파이썬(3.8 이상)을 찾지 못해 하네스 장치가 모두 꺼져 있어요」가 나옵니다.

## 1. 받기 장치 넣기
```bash
mkdir -p .claude
curl -fsSL https://raw.githubusercontent.com/safetysong/harness/main/bootstrap/harness-sync.sh -o .claude/harness-sync.sh
```
받은 파일을 끝까지 읽어 보고(무엇을 하는지 확인) 넘어갑니다.

## 2. 설정 파일 `.claude/harness.json`
```json
{
  "set": "C-submission",
  "repo": "owner/name",
  "source": "safetysong/harness",
  "progress_file": "진행상황.md"
}
```
- `set`: 세트 하나(`"C-submission"`), 두 세트(`["A-research", "B-app"]`), 바닥판만(`"core"` 또는 비움).
- 아무도 없이 도는 예약 실행 세션이면 `"unattended": true` 를 넣습니다(허락 창 대신 막기).
  같은 저장소에 사람이 쓰는 세션도 있으면, 그 세션 환경에 `HARNESS_UNATTENDED=0` 을 두면 허락 창이 다시 뜹니다
  (반대로 harness.json 에 없이 예약 실행 환경에만 `HARNESS_UNATTENDED=1` 을 둬도 됩니다).
- 읽기만 하는 저장소·세션이면 `"read_only": true`. 세션 첫 말에 「읽기만」이 있어도 읽기 전용으로 봅니다.
  읽기 전용이면 실수기록.md와 장치 숫자 기록을 고치지 않습니다.
- 세트 C면 `submission` 칸을 요강에 맞춰 채웁니다:
  `{"file": "제출/초안.md", "required_sections": [...], "min_chars": 0, "max_chars": 0, "deadline": "YYYY-MM-DD", "format": ".hwpx", "final_file": "제출/최종.hwpx"}`
- 세트 A에서 검사할 원고를 정하려면 `"research": {"files": ["원고/*.md"]}`.
- `progress_check`는 보통 적지 않습니다. 4번이 옛 진행상황 검사 장치를 찾으면 알아서 `false` 를 넣습니다.

## 3. 하네스 받기
```bash
bash .claude/harness-sync.sh sync
```
「새 판 0.x.x 받음」이 나와야 합니다. 「경고」가 나오면 새 판이 막 올라온 직후일 수 있으니 5분 뒤 한 번만 다시 하고,
그래도 「경고」거나 「네트워크」가 나오면 멈추고 사용자에게 그대로 알립니다.

## 4. 받기 장치와 settings.json 맞추기
```bash
bash .claude/harness-sync.sh upgrade
```
- 받기 장치를 승인된 새 판으로 바꾸고, 하네스 훅·금지 목록을 `.claude/settings.json` 에 합칩니다.
  기존 내용은 지우지 않고, 예전 하네스 훅만 빼고 새로 넣어서 여러 번 해도 두 번 들어가지 않습니다.
- 프로젝트의 다른 Stop 훅이 「진행상황」을 검사하면(옛 stop-check 등) `progress_check: false` 를 넣어
  같은 일로 두 번 막지 않게 합니다. 옛 장치 등록을 빼고 하네스에 맡기고 싶으면 사용자에게 먼저 여쭙니다.
- **판을 올릴 때도 이 한 줄이면 됩니다.** 이미 깐 프로젝트를 최신으로 맞출 때 1·3번 없이 이것만 합니다.
  (0.1.x로 깐 프로젝트는 받기 장치가 옛 판이라 처음 한 번은 `sync` 뒤에
  `bash .claude/harness/bootstrap/harness-sync.sh upgrade` 로 돌립니다.)

## 5. 틀 채우기
- `CLAUDE.md`: 없으면 `.claude/harness/core/CLAUDE.template.md` 를 복사해 `{{ }}` 칸을 채웁니다.
  있으면 내용은 그대로 두고 빠진 칸(목표·완료 조건 위치·하지 않는 것·말투·저장소 규칙)만 더합니다. 200줄 아래로.
- `진행상황.md`: 없으면 `.claude/harness/core/진행상황.template.md` 로 만듭니다.
  완료 조건마다 짧게 끝나는 확인 명령을 붙입니다(`· 확인: \`명령\``). 명령이 없으면 그 줄은 사람이 봅니다.
  `{{ }}` 가 남은 확인 명령은 돌리지 않으니 꼭 채웁니다.
- `.gitignore` 에 `.claude/harness-state/` 를 더합니다.

## 6. 확인하고 커밋
```bash
bash .claude/harness-sync.sh start < /dev/null    # [장치 상태] 줄이 나오는지
```
커밋할 것: `.claude/harness-sync.sh`, `.claude/harness.json`, `.claude/harness/`, `.claude/agents/`,
`.claude/settings.json`, `CLAUDE.md`, `진행상황.md`, `.gitignore`.
쓰다 보면 `.claude/harness-stats.jsonl`(장치 숫자 기록, 하루 한 줄)도 생깁니다. 이것도 커밋에 같이 넣습니다.

## 7. 사용자에게 알리기
- 훅 설정은 **다음 세션부터** 켜진다고 알립니다(설정은 세션이 시작할 때 읽음).
- 무엇이 켜졌는지 한 줄씩: 한국어 지킴이, 저장소 지킴이, 끝 점검(완료 조건), 실수 기록, 세트 검사기, 검토 에이전트.
- 4번이 옛 진행상황 검사 장치를 찾았으면 그 이름과 「하네스 진행상황 점검을 껐다」는 것도 알립니다.
