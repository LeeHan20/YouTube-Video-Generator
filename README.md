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
- 선택된 소주제 감지, 기본 장면 분리, SRT/FFmpeg 렌더링, 검수 링크 생성
- React + TypeScript 검수 UI 스캐폴드와 FastAPI 내장 검수 화면
- CapCut형 검수/편집 화면: 미디어 소스, 프리뷰, 속성 패널, 타임라인
- 장면별 에셋 소스 선택: 자동, AI 이미지 생성, 이미지 크롤링, 영상 크롤링
- YouTube OAuth/비공개 업로드/공개 전환 파이프라인 skeleton
- SMS 알림 provider abstraction

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
frontend/           React + TypeScript 검수 UI
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
- `AI_PROVIDER`: `gemini` 또는 `claude`
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`
- `MEDIA_SOURCE_MODE`: `auto`, `ai_image`, `crawl_image`, `crawl_video`
- `MEDIA_CRAWL_PROVIDER`: 기본 `wikimedia`
- `MEDIA_CRAWL_ALLOWED_LICENSES`: 허용 라이선스 목록

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
python3 scripts/run_stage2_once.py
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
- `POST /admin/pipeline/stage2/run`
- `GET /admin/youtube/oauth/start/{channel_id}`
- `GET /admin/youtube/oauth/callback`
- `POST /admin/pipeline/stage4/upload/run`
- `POST /admin/pipeline/stage5/run`

상시 worker:

```bash
python3 worker.py
```

단일 tick 테스트:

```bash
python3 scripts/run_worker_tick.py
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

## 2단계 처리 규칙

- 채널별 시트에서 `선택 == TRUE`인 소주제를 감지합니다.
- `_SYSTEM_JOBS`에 `GENERATE_VIDEO_DRAFT` job을 만들고 락을 잡습니다.
- 대본을 장면 단위로 나눕니다.
- 장면별 프롬프트와 placeholder scene asset을 만듭니다.
- 자막 파일 `subtitles.srt`를 만듭니다.
- FFmpeg가 설치되어 있으면 기본 MP4를 렌더링합니다.
- FFmpeg가 없거나 렌더링에 실패하면 manifest JSON URL을 fallback으로 기록합니다.
- 채널 시트에 `검수 링크`, `편집 링크`, `완성 영상 URL`, `WAITING_USER_APPROVAL` 상태를 기록합니다.

## 검수 UI

FastAPI 내장 화면:

```text
/review/{session_id}
```

React + TypeScript UI:

```bash
cd frontend
npm install
npm run dev
```

검수 UI 기능:

- 영상 미리보기
- CapCut형 좌측 미디어 소스 패널
- 중앙 프리뷰
- 우측 장면 속성/프롬프트 패널
- 하단 비디오/나레이션/자막 타임라인
- 장면별 에셋 링크와 출처/라이선스 표시
- 장면별 에셋 생성 방식 선택
  - 자동 선택
  - AI 이미지 생성
  - 이미지 크롤링
  - 영상 크롤링
- 사용자 지시사항 입력
- “이 장면 다시 생성”
- 재렌더링
- 최종 승인

## 장면 에셋 소스 규칙

기본값은 `auto`입니다.

```env
MEDIA_SOURCE_MODE=auto
MEDIA_CRAWL_PROVIDER=wikimedia
MEDIA_CRAWL_ALLOWED_LICENSES=cc0,public domain,cc-by,cc-by-sa
```

동작 방식:

- `ai_image`: AI 생성 프롬프트를 만들고 원본 생성 이미지 후보를 생성합니다. 현재는 로컬 SVG placeholder이며, 실제 이미지 생성 API adapter를 붙일 수 있습니다.
- Gemini API 키와 이미지 모델이 설정되어 있으면 실제 Gemini 이미지 생성 결과를 PNG로 저장합니다.
- Gemini 이미지 생성이 실패하거나 키가 없으면 로컬 PNG 후보를 생성합니다.
- `crawl_image`: Wikimedia Commons에서 이미지 후보를 검색하고 허용 라이선스만 로컬 저장소에 내려받습니다.
- `crawl_video`: Wikimedia Commons에서 영상 후보를 검색합니다.
- `auto`: 장면 문맥을 보고 크롤링이 어울리면 크롤링, 아니면 AI 생성으로 처리합니다.

저작권 원칙:

- 크롤링한 에셋은 출처, 작성자, 라이선스를 manifest와 `_SYSTEM_ASSETS`에 기록합니다.
- 라이선스가 허용 목록에 없으면 사용하지 않습니다.
- 후보가 없으면 AI 생성 후보로 대체합니다.

## 나레이션과 실제 렌더링

장면별 렌더링은 이제 다음 순서로 처리됩니다.

1. 장면별 이미지/영상 에셋 확보
2. 장면별 나레이션 음성 생성
3. FFmpeg로 장면 MP4 생성
4. 장면 MP4를 최종 영상으로 병합

TTS 우선순위:

- Gemini TTS: `GEMINI_API_KEY`, `GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE_NAME`이 설정되어 있으면 사용
- macOS 개발 환경: `say` 명령이 있으면 한국어 음성 파일 생성
- 최후 fallback: 무음 오디오 생성

현재 FFmpeg 빌드에 `drawtext`가 없는 환경에서도 실패하지 않도록, 자막 번인은 하지 않고 `subtitles.srt` 파일을 별도로 생성합니다.

## AI Provider

기본은 Gemini입니다. `.env`에서 바꿀 수 있습니다.

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash

# 가끔 Claude를 쓰고 싶을 때
AI_PROVIDER=claude
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-5
```

API 키가 비어 있으면 로컬 템플릿 fallback으로 개발 흐름이 계속됩니다.

## YouTube OAuth와 업로드

1. Google Cloud Console에서 OAuth client JSON을 내려받아 `client_secret.json`으로 저장합니다.
2. `.env`에 `GOOGLE_OAUTH_CLIENT_SECRETS`와 `YOUTUBE_OAUTH_REDIRECT_URI`를 설정합니다.
3. 관리자 API에서 연결 URL을 발급합니다.

```text
GET /admin/youtube/oauth/start/{channel_id}
```

4. Google OAuth 동의 후 callback에서 refresh token을 암호화 파일로 저장합니다.
5. `APPROVED` 상태의 영상은 다음 API로 비공개 업로드합니다.

```text
POST /admin/pipeline/stage4/upload/run
```

## 5단계 skeleton

- `UPLOADED_PRIVATE` 또는 `SCHEDULED` 상태 영상의 `upload_datetime`이 지나면 공개 전환을 시도합니다.
- 월요일/화요일에 소주제 선택이 없으면 SMS provider를 통해 알림을 보냅니다.
- 중복 SMS 방지는 `_SYSTEM_JOBS`의 24시간 락으로 처리합니다.

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

다음 구현 고도화 지점은 실제 이미지/영상 생성 provider, 실제 TTS provider, S3 업로드, 썸네일 생성, YouTube 설명란 챕터 자동화입니다.
