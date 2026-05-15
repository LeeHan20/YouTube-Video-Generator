# Google Sheets 기반 YouTube 자동화 MVP

Google Sheets를 운영 상태 저장소로 사용하는 YouTube 자동 생성/검수/업로드 시스템입니다. 이 저장소의 현재 구현 범위는 **MVP 1단계**입니다.

- 별도 DB 없음
- Google Sheets API로 상태 읽기/쓰기
- FastAPI 관리자 API
- APScheduler/worker 진입점
- 채널별 소주제 `n * 3`개 생성
- 기본 대본 생성
- 채널 시트와 `검수대기` 시트 기록
- OAuth refresh token은 Sheets에 저장하지 않는 암호화 파일 저장소 인터페이스 포함

## 디렉토리 구조

```text
app/
  api/              FastAPI 라우트와 관리자 인증
  core/             설정, 상태값, 시트 컬럼 상수
  google/           Google Sheets 클라이언트, 템플릿 생성, 저장소 계층
  pipeline/         MVP 단계별 자동화 파이프라인
  scheduler/        APScheduler 실행 진입점
  services/         소주제/대본 생성 서비스
  sms/              SMS provider abstraction
  storage/          로컬/S3 저장소 확장 지점
  youtube/          OAuth 토큰 암호화 저장소, 업로드 확장 지점
scripts/
  create_sheets_template.py
  run_stage1_once.py
worker.py
```

## 로컬 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env`에 다음을 설정합니다.

- `GOOGLE_APPLICATION_CREDENTIALS`: Google service account JSON 경로
- `GOOGLE_SHEETS_SPREADSHEET_ID`: 사용할 Spreadsheet ID
- `TOKEN_ENCRYPTION_KEY`: OAuth 토큰 암호화 키
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`: 관리자 API Basic Auth

암호화 키 생성:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Google Sheets 템플릿 생성

서비스 계정 이메일을 대상 Google Sheet에 편집자로 공유한 뒤 실행합니다.

```bash
python3 scripts/create_sheets_template.py
```

생성되는 탭:

- `사용안내`
- `채널목록`
- `검수대기`
- `업로드현황`
- `채널_건강정보`
- `채널_생활상식`
- `채널_역사이야기`
- `_SYSTEM_JOBS`
- `_SYSTEM_LOGS`
- `_SYSTEM_ASSETS`
- `_SYSTEM_SESSIONS`
- `_SYSTEM_SETTINGS`

시스템 탭은 숨김 처리됩니다. 사용자가 수정할 수 있는 칸은 초록색, 시스템 관리 칸은 회색으로 표시됩니다. 상태값, ON/OFF, 그림체, 숫자 길이 입력은 데이터 검증을 설정합니다.

## MVP 1단계 실행

1. `채널목록`에서 원하는 채널의 `automation_enabled`를 `ON`으로 바꿉니다.
2. 업로드 요일을 `ON`으로 설정합니다.
3. 마지막 업로드 요일에 자동 생성됩니다. 테스트할 때는 `--force`를 사용합니다.

```bash
python3 scripts/run_stage1_once.py --force
```

FastAPI로 실행:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

관리자 API:

- `GET /health`
- `POST /admin/sheets/template`
- `GET /admin/channels`
- `POST /admin/pipeline/stage1/run?force=true`

상시 worker:

```bash
python3 worker.py
```

## 1단계 처리 규칙

- `채널목록`에서 `automation_enabled == ON`인 채널만 읽습니다.
- `_SYSTEM_JOBS`에 `job_stage1_{channel_id}_{week_key}` 작업을 만들고 `locked_by`, `locked_until`로 락을 잡은 worker만 실행합니다.
- ON인 업로드 요일 수를 `n`으로 계산합니다.
- 매주 마지막 업로드 요일에 다음 주차 `week_key` 기준으로 `n * 3`개 소주제를 만듭니다.
- 같은 `week_key`가 이미 있거나 `last_topic_generated_week`가 같으면 다시 생성하지 않습니다.
- 소주제는 정보제공형과 클릭유도형을 섞습니다.
- 기본 타겟은 50대 이상이며, 과장/허위/공포 마케팅을 피하는 기획 노트를 포함합니다.
- 대본은 인트로, 요약, 챕터, 마무리 구조로 생성됩니다.
- 결과는 채널별 시트에 기록되고, `검수대기`에는 “소주제 선택 필요” 항목이 추가됩니다.

## 보안 원칙

- Google 계정 비밀번호를 Sheets나 서버에 저장하지 않습니다.
- 2단계 인증 코드를 자동 우회하지 않습니다.
- YouTube 연결은 OAuth 2.0으로만 처리합니다.
- refresh token은 `encrypted_tokens/{channel_id}.json.enc`처럼 서버 암호화 파일로 저장합니다.
- `.env`, `encrypted_tokens/`, `storage/`는 Git에 올리지 않습니다.

## AWS 배포 방법

가장 단순한 MVP 배포는 EC2 단일 인스턴스입니다.

1. EC2에 Python 3.11+와 FFmpeg를 설치합니다.
2. 이 저장소를 배포하고 `.env`와 service account JSON을 서버에만 둡니다.
3. `pip install -r requirements.txt`를 실행합니다.
4. FastAPI는 systemd 서비스로 `uvicorn app.main:app`을 실행합니다.
5. worker는 별도 systemd 서비스로 `python3 worker.py`를 실행합니다.
6. 렌더링 산출물은 후속 단계에서 S3 또는 `LOCAL_STORAGE_DIR`에 저장하고 Sheets에는 URL만 기록합니다.

## 일반 사용자 사용 설명서

일반 사용자는 주로 다음 탭만 봅니다.

- `사용안내`
- `채널목록`
- `검수대기`
- `업로드현황`
- `채널_{채널명}`

운영 흐름:

1. `채널목록`에서 자동화와 업로드 요일을 켭니다.
2. 자동 생성된 소주제 후보를 채널별 시트에서 확인합니다.
3. 사용할 소주제의 `selected`를 `TRUE`로 바꿉니다.
4. 다음 단계 구현 후 `review_link`로 웹 검수 UI에 들어갑니다.
5. 최종 승인 후 YouTube 비공개 업로드 및 예약 공개가 진행됩니다.

## 다음 단계

MVP 2단계에서는 `selected == TRUE` 감지, `_SYSTEM_JOBS` 락 기반 영상 생성 job 생성, 이미지/TTS/자막/FFmpeg 기본 렌더링을 구현합니다.
