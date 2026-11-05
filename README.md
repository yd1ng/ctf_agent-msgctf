# CTF Agent for MSG CTF

[ctf_agent](https://github.com/yd1ng/ctf_agent)를 [MSG CTF](https://github.com/MSG-CTF/msg-backend)의 **참가자 API**에 맞춘 버전입니다. 팀이 보드에서 이미 연 문제를 자동으로 가져와 Codex로 풀고, 찾은 플래그를 제출합니다. 주사위 굴리기와 보드에서 문제 선택하기는 직접 해야 합니다.

## 실사용 로그인

CTFd 계정이나 CTFd API 토큰은 필요하지 않습니다. **대회 운영진에게 받은 MSG CTF 참가자 아이디와 비밀번호**를 사용합니다. 브라우저의 MSG CTF 로그인 화면에 입력하는 값과 같습니다. 팀 이름이나 GitHub 계정이 아닌 참가자 `login_id`를 입력하세요. 참가자 계정 발급은 운영진에게 문의해야 합니다.

Codex 로그인은 별개입니다. 호스트에서 `codex login`을 실행하면 풀이용 Codex 인증이 준비됩니다. 실행 스크립트가 첫 실행 때 Codex 인증 파일을 `data/codex-home/`으로 복사합니다.

## Docker로 실행

Docker와 Docker Compose, Codex CLI가 설치되어 있어야 합니다. Docker 데몬을 실행한 뒤:

```sh
git clone https://github.com/yd1ng/ctf_agent-msgctf.git
cd ctf_agent-msgctf
cp .env.msgctf.example .env
# 편집기로 .env의 MSGCTF_URL, MSGCTF_LOGIN_ID, MSGCTF_PASSWORD 수정
chmod 600 .env
codex login
./run-msgctf.sh --list
```

`.env`의 핵심 설정:

```env
PLATFORM=msgctf
MSGCTF_URL=https://msgctf.kr
MSGCTF_LOGIN_ID=운영진이_발급한_참가자_아이디
MSGCTF_PASSWORD=운영진이_발급한_비밀번호
```

`MSGCTF_URL`에는 실제 대회 사이트 주소를 넣습니다. `/api/v1` 경로는 클라이언트가 자동으로 붙입니다. `.env.example`의 `CTFD_URL`과 `CTFD_TOKEN`은 기존 CTFd 모드용이므로 MSG CTF 실행에는 사용하지 않습니다. `.env`는 Git 추적에서 제외되어 있습니다.

`--list`는 로그인과 **현재 열린 문제 조회**를 확인하고 종료합니다. 아직 연 문제가 없으면 0개가 표시됩니다. 확인 후 실행 방법을 고르세요.

```sh
./run-msgctf.sh --no-submit  # 문제는 풀고 플래그는 제출하지 않음
./run-msgctf.sh              # 문제 풀이 후 플래그 자동 제출
```

`--no-submit`에서도 문제에 필요한 인스턴스는 생성하거나 교체할 수 있습니다. 실행 중지는 `Ctrl+C`입니다.

첫 풀이 실행 때 원본의 전체 CTF 도구가 들어간 `ctf-sandbox` 이미지를 빌드합니다. 이후 해당 이미지가 있으면 다시 빌드하지 않습니다. 문제 파일과 풀이 기록은 `data/challenges/`, 실행 로그는 `data/logs/`에 저장됩니다. Docker Compose 에이전트는 호스트 Docker 소켓을 사용하여 풀이 컨테이너를 실행합니다.

## 동작 범위

- 팀이 이미 연 문제만 `GET /api/v1/board/opened_challenges`에서 감지합니다. 주사위와 문제 선택은 자동화하지 않습니다.
- 문제 설명과 첨부 파일을 가져오고, 필요한 경우 문제 인스턴스를 시작합니다.
- Codex 풀이에서 찾은 플래그를 `POST /api/v1/challenges/{id}/submit`으로 제출합니다. 인증 토큰은 로그인 후 자동으로 갱신합니다.
- 백엔드가 참가자당 활성 인스턴스 하나만 허용하므로 한 번에 문제 하나를 풉니다. 열린 미해결 문제가 여러 개면 기본 15분 간격으로 순서를 바꿉니다. `.env`의 `MSGCTF_SWARM_TIMESLICE_SECONDS`로 조정할 수 있습니다.
- `remote_run` 실행 파일 제출 방식은 현재 자동 제출 대상이 아닙니다.

MSG CTF 연동은 로컬 백엔드에서 참가자 로그인, 문제 파일 다운로드, Codex 풀이, 플래그 제출까지 검증했습니다. 실제 대회 서버 계정으로는 아직 검증하지 않았습니다.

## 인증 파일 관리

`.env`와 `data/`는 Git에서 제외됩니다. 참가자 비밀번호와 Codex 인증 파일을 저장소에 커밋하지 마세요. 호스트의 Codex 계정을 바꿨다면 `data/codex-home/auth.json`을 삭제한 다음 다시 실행해야 새 인증 파일이 복사됩니다. 풀이용 자식 컨테이너에는 참가자 계정과 Codex 인증 디렉터리를 전달하지 않습니다.

## 로컬 Python 실행

Compose 대신 직접 실행할 때는 Python 3.14 이상과 `uv`가 필요합니다.

```sh
uv sync
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
uv run ctf-solve --platform msgctf --list
uv run ctf-solve --platform msgctf --challenges-dir challenges --max-challenges 1 -v
```

이 저장소에는 원본 CTFd 모드도 남아 있습니다. 위 Docker 실행 스크립트와 안내는 MSG CTF 모드 기준입니다. 원본 라이선스는 [LICENSE](LICENSE)를 따릅니다.
