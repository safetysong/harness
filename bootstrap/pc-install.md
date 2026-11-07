# PC 기본판 까는 순서 (Claude가 PC에서 따라 할 순서)

사용자가 PC의 Claude Code에서 「하네스 PC 기본판 깔아 줘」처럼 말하면 아래 순서로 합니다.
PC 기본판은 하네스 플러그인입니다. PC의 모든 폴더에서 공통 바닥판(한국어 지킴이, 저장소 지킴이, 끝 점검, 실수 기록)이 돕니다.

## 하네스를 깔지 않은 폴더에서 하는 일
- 시작 안내: 그 폴더에 `진행상황.md` 가 있으면 보여 주고, 없으면 만들라고 하지 않습니다.
- 끝 점검: `진행상황.md` 가 있는 폴더에서만 「진행상황 안 적음」과 완료 조건 확인 명령을 봅니다.
- 저장소 지킴이: 폴더의 git `origin` 이 깃허브 저장소면 그 저장소를 지킵니다(Bash와 PowerShell 도구 모두).
  다른 저장소 clone·remote 바꾸기·깃허브 도구 쓰기는 허락 창을 띄웁니다.
- 기록: 그 폴더에는 아무 파일도 만들지 않고, `~/.claude/harness-state/projects/` 에 둡니다.
- 하네스가 따로 깔린 프로젝트(`.claude/harness-sync.sh` 가 있음)에서는 플러그인이 비켜서고 프로젝트 하네스가 돕니다.

## 0. 먼저 확인
- `claude --version` 이 2.1.295 이상인지.
- 윈도면 Git for Windows(`bash --version`)와 파이썬 3.8 이상(`python --version` 또는 `py --version`).
  없으면 사용자에게 설치를 부탁하고 멈춥니다.

## 1. 옛 PC 장치 찾기
- `~/.claude/settings.json` 을 읽고, `hooks` 안에서 하네스가 아닌 옛 장치(예: `progress-guard` 폴더를 부르는 것,
  한국어·저장소 지킴이, 시작 안내, 끝 점검)를 찾아 목록을 사용자에게 보여 드립니다.
- 사용자가 좋다고 하면 `~/.claude/settings.json` 을 `settings.json.bak-날짜` 로 복사해 둔 뒤 그 항목만 뺍니다.
  다른 내용(권한, 모델, 다른 훅)은 그대로 둡니다. 옛 장치 폴더는 지우지 않고 그대로 둡니다(되돌릴 수 있게).

## 2. 플러그인 깔기
PowerShell이나 터미널에서:
```
claude plugin marketplace add safetysong/harness
claude plugin install harness@safetysong-harness
```
(Claude Code 안에서는 `/plugin marketplace add safetysong/harness` 뒤 `/plugin install harness@safetysong-harness`,
「Install for you (user scope)」를 고릅니다.)

## 3. 자동 업데이트 켜기
다른 사람이 만든 마켓플레이스는 자동 업데이트가 꺼져 있습니다. 사용자에게 이렇게 부탁합니다.
- Claude Code에서 `/plugin` → Marketplaces 탭 → `safetysong-harness` → 「Enable auto-update」.
- 손으로 올릴 때는 `claude plugin update harness@safetysong-harness`.

## 4. 금지 목록 합치기(선택)
플러그인은 금지 목록(permissions.deny)을 넣지 못합니다. 사용자가 원하면
https://raw.githubusercontent.com/safetysong/harness/main/core/settings.core.json 의 `permissions.deny` 줄들을
`~/.claude/settings.json` 의 `permissions.deny` 에 더합니다(이미 있는 줄은 두 번 넣지 않음, 다른 내용은 그대로).

## 5. 확인
- Claude Code를 끄고 새 세션을 엽니다. 첫 안내에 `[장치 상태] 판 0.x.x · PC 기본판(이 폴더엔 하네스 설정 없음)` 이 보이면 됩니다.
- 옛 줄(`판 2026-10-19-single` 같은 것)이 아직 보이면 1번에서 옛 장치가 덜 빠진 것입니다.
- 깃허브 저장소 폴더에서 열면 「켜진 지킴이: 저장소(owner/name) …」가 보입니다.

## 되돌리기
`claude plugin uninstall harness@safetysong-harness` 뒤 `settings.json.bak-날짜` 를 `settings.json` 으로 되돌립니다.
