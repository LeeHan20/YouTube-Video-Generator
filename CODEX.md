# Codex Project Rules

이 파일은 이 프로젝트에서 Codex가 항상 지켜야 하는 운영 원칙이다. 새 기능이나 수정사항이 생기면 관련 규칙도 함께 업데이트한다.

## 핵심 제약

- 별도 DB를 추가하지 않는다. PostgreSQL, MySQL, MongoDB, SQLite를 사용하지 않는다.
- Google Sheets를 운영 상태 저장소이자 관리자 화면으로 사용한다.
- 영상, 이미지, 음성, 썸네일 파일은 Google Sheets에 저장하지 않는다. Sheets에는 URL, 상태값, 검수 링크만 저장한다.
- OAuth refresh token은 Google Sheets에 저장하지 않고 서버의 암호화 파일 저장소에만 저장한다.
- Google 계정 비밀번호나 2단계 인증 코드는 저장하거나 우회하지 않는다.

## UI 항상성

- Google Sheets의 진파랑 배경은 헤더 행에만 사용한다.
- 데이터 행은 시스템 컬럼 회색, 사용자 입력 가능 컬럼 연녹색으로 유지한다.
- 새 행 생성, 초기화, 복구 스크립트 실행 뒤에는 `scripts/repair_sheet_ui_format.py`를 실행하거나 동등한 서식 복구 로직을 호출한다.
- 사용자가 직접 만지는 셀은 드롭다운, 숫자 검증, 색상 구분으로 명확히 표시한다.
- 50대 이상 사용자를 기준으로 글자 크기, 행 높이, 줄바꿈, 정렬을 보수적으로 유지한다.
- 검수 UI는 CapCut식 편집 흐름을 유지하되, 레이아웃 변경은 기능상 꼭 필요할 때만 작게 한다.
- 버튼이 오래 걸리는 작업을 시작하면 반드시 로딩 문구와 disabled 상태를 제공한다.

## 이미지/영상 편집 규칙

- 기본 이미지 소스는 크롤링이다.
- AI 이미지 생성은 사용자가 명시적으로 선택했을 때만 실행한다.
- Stage2 초안 생성에서는 장면당 최종 에셋 1개만 수집한다. 후보 4개 수집은 검수 UI에서 사용자가 “이미지 다시 가져오기”를 눌렀을 때만 실행한다.
- 이미지 후보를 다시 가져올 때 기존 장면 이미지는 유지한다.
- 후보 이미지 선택 또는 업로드 시 이미지 관련 필드만 바꾸고, 장면 ID, 순서, 대본, 자막, 나레이션, 프롬프트, 타임라인, 길이는 보존한다.
- 렌더링은 원본 이미지 비율을 유지하는 contain/pad 방식을 기본으로 한다. crop/fill을 기본값으로 되돌리지 않는다.
- 자막은 대본/나레이션에 맞춰 자동 생성하고 영상에 보이도록 합성한다.

## 상태값과 Sheets 동기화

- 렌더링 시작 시 `VIDEO_RENDERING`, 완료 시 `VIDEO_RENDERED`, 실패 시 `VIDEO_RENDER_FAILED`를 기록한다.
- 이미지 크롤링 상태는 `IMAGE_CRAWLING`, `IMAGE_CANDIDATES_READY`, `IMAGE_SELECTED`, `IMAGE_CRAWLING_FAILED`를 사용한다.
- 최종 승인 상태는 `FINAL_APPROVING`, `FINAL_APPROVED`를 사용한다.
- 렌더링 완료 후 Google Sheets의 `rendered_video_url`, `status`, `updated_at`을 최신 값으로 갱신한다.
- 실패 시 `error_message`에 사용자가 이해 가능한 이유를 남긴다.

## 스크립트 운영

- 사용자가 터미널에서 실행하기 쉬운 형태를 우선한다.
- 루트에서 실행하는 기본 형태는 `PYTHONPATH=. .venv/bin/python -m scripts.<module>`이다.
- 전체 Google Sheets 탭 초기화가 필요할 때는 `scripts.reset_all_sheets`를 사용한다. 이 스크립트는 모든 탭을 삭제하고 기본 템플릿을 재생성하므로 실행 전 의도를 명확히 확인한다.
- 외부 AI/TTS 호출은 타임아웃과 fallback을 가져야 한다.
- 기본 TTS provider는 Supertone이다. `SUPERTONE_API_KEY`와 `SUPERTONE_VOICE_ID`는 `.env`에만 두고 커밋하지 않는다.
- 개발 테스트에서 빠른 렌더가 필요하면 `NARRATION_PROVIDER=system` 또는 `GEMINI_API_KEY=`로 Gemini TTS를 우회할 수 있어야 한다.
- 오래 걸리는 스크립트, 특히 Stage2는 터미널에 단계별 진행 메시지를 출력해야 한다.
- 파괴적 스크립트는 이름과 출력에서 삭제/초기화 범위를 분명히 드러낸다.
