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
- Stage2 장면 분할은 대본의 빈 줄 기준 단락을 우선한다. 이미지/영상 수를 10개 같은 고정값으로 제한하지 않는다.
- 첫 번째와 두 번째 장면은 기본적으로 영상 클립(`media_type=video`)을 먼저 크롤링한다. 이후 장면은 이미지 크롤링을 기본으로 한다.
- 크롤링 실패 시 렌더링 단계에서 몰래 AI 이미지나 placeholder로 대체하지 않는다. 사용자가 검수 UI에서 후보 재크롤링, 직접 업로드, AI 이미지 생성을 명시적으로 선택하게 한다.
- 장면 프롬프트는 검수 UI에서 사용자가 직접 수정할 수 있어야 하고, 수정된 프롬프트는 다음 크롤링/생성 요청에 반영되어야 한다.
- Stage2 초안 생성에서는 장면당 최종 에셋 1개만 수집한다. 후보 4개 수집은 검수 UI에서 사용자가 “이미지 다시 가져오기”를 눌렀을 때만 실행한다.
- 장면은 항상 `scene_id`, `narration`, `caption/subtitle`, `asset_url`, `tts_audio_path`, `duration_seconds`, `start_seconds`, `end_time` 기준으로 관리한다.
- 전체 대본, raw script, prompt 문구를 자막이나 TTS에 직접 넣지 않는다. 자막 우선순위는 `caption`, `subtitle`, `narration`이다.
- 장면별 TTS는 정확히 한 파일만 만들고, 최종 mp4에는 오디오 스트림이 하나만 남아야 한다.
- 장면 길이와 자막 timestamp는 고정값이 아니라 실제 TTS audio duration 기준으로 계산한다.
- 렌더링 후에는 mp4 존재 여부, 오디오 스트림 수, scene/audio/image/subtitle 개수, 긴 대본 자막 유입, 이미지 반복, 영상 길이 차이를 검증한다.
- 이미지 후보를 다시 가져올 때 기존 장면 이미지는 유지한다.
- 후보 이미지 선택 또는 업로드 시 이미지 관련 필드만 바꾸고, 장면 ID, 순서, 대본, 자막, 나레이션, 프롬프트, 타임라인, 길이는 보존한다.
- 이미지 선택은 scene별 키워드와 기존 사용 이미지 URL/hash를 고려해 반복 이미지를 피한다.
- 장면 이미지 검색은 전체 주제보다 해당 scene narration/caption의 대표어를 우선한다.
- Openverse/Wikimedia 검색은 좁은 후보에서 첫 이미지를 고르지 않는다. scene별 focused query로 넓은 후보 풀을 모은 뒤 점수와 출처 다양성으로 최종 후보를 고른다.
- `MEDIA_CRAWL_MAX_RESULTS`는 영상 품질을 위해 기본 32 이상을 권장한다. 너무 낮추면 같은 건강/시니어 이미지가 반복될 수 있다.
- Openverse 익명 API는 `page_size` 20 초과 요청을 거부한다. 후보 풀을 넓힐 때도 한 요청의 page_size는 20 이하로 유지하고 여러 쿼리로 확장한다.
- 이미지 장면 레이어와 자막 레이어는 별도 타임라인으로 관리한다. 이미지 scene 수와 caption segment 수가 같을 필요는 없다.
- 자막 segment는 한 줄만 사용하며, 긴 문장은 여러 caption segment로 나눠 TTS duration 안에 배치한다.
- 렌더링은 원본 이미지 비율을 유지하는 contain/pad 방식을 기본으로 한다. crop/fill을 기본값으로 되돌리지 않는다.
- 자막은 대본/나레이션에 맞춰 자동 생성하고 영상에 보이도록 합성한다.
- 사용자가 후편집할 수 있도록 편집용 다운로드는 프로젝트 JSON, SRT/VTT, manifest, 사용 에셋을 포함한다.

## 상태값과 Sheets 동기화

- 렌더링 시작 시 `VIDEO_RENDERING`, 완료 시 `VIDEO_RENDERED`, 실패 시 `VIDEO_RENDER_FAILED`를 기록한다.
- 이미지 크롤링 상태는 `IMAGE_CRAWLING`, `IMAGE_CANDIDATES_READY`, `IMAGE_SELECTED`, `IMAGE_CRAWLING_FAILED`를 사용한다.
- 최종 승인 상태는 `FINAL_APPROVING`, `FINAL_APPROVED`를 사용한다.
- 렌더링 완료 후 Google Sheets의 `rendered_video_url`, `status`, `updated_at`을 최신 값으로 갱신한다.
- 실패 시 `error_message`에 사용자가 이해 가능한 이유를 남긴다.

## 스크립트 운영

- 사용자가 터미널에서 실행하기 쉬운 형태를 우선한다.
- 루트에서 실행하는 기본 형태는 `PYTHONPATH=. .venv/bin/python -m scripts.<module>`이다.
- Stage1 수동 실행에서 `--force`는 날짜/주차/완료 job 조건을 무시하고 자동화 ON 채널의 소주제를 생성하며, `--test`는 해당 채널의 기존 Stage1 관련 Sheets 행과 로컬 topic 산출물을 삭제한 뒤 새로 생성한다. `--test`는 `--force`를 포함한다.
- Stage2 수동 실행에서 `--force`는 상태/완료 job 조건을 무시하고 선택된 소주제를 재실행하며, `--test`는 해당 topic의 기존 로컬 산출물을 삭제한 뒤 새로 생성한다. `--test`는 `--force`를 포함한다.
- 전체 Google Sheets 탭 초기화가 필요할 때는 `scripts.reset_all_sheets`를 사용한다. 이 스크립트는 모든 탭을 삭제하고 기본 템플릿을 재생성하므로 실행 전 의도를 명확히 확인한다.
- 시트 템플릿만 만들거나 다시 적용할 때는 `scripts.create_sheets_template`를 사용한다. `--force`는 기존 데이터를 보존하고 템플릿/서식만 재적용하며, `--test`는 기존 탭을 삭제한 뒤 새 템플릿을 만든다.
- 외부 AI/TTS 호출은 타임아웃과 fallback을 가져야 한다.
- 기본 TTS provider는 Supertone이다. `SUPERTONE_API_KEY`와 `SUPERTONE_VOICE_ID`는 `.env`에만 두고 커밋하지 않는다.
- 개발 테스트에서 빠른 렌더가 필요하면 `NARRATION_PROVIDER=system` 또는 `GEMINI_API_KEY=`로 Gemini TTS를 우회할 수 있어야 한다.
- 오래 걸리는 스크립트, 특히 Stage2는 터미널에 단계별 진행 메시지를 출력해야 한다.
- Google Sheets API는 인증 토큰 발급 단계에서도 네트워크 타임아웃이 날 수 있다. `GOOGLE_API_TIMEOUT_SECONDS`로 timeout을 명시하고, SSL handshake/네트워크 계열 오류는 재시도 대상으로 유지한다.
- 파괴적 스크립트는 이름과 출력에서 삭제/초기화 범위를 분명히 드러낸다.

## AI 프롬프트 관리

- Gemini/Claude 텍스트 호출에 쓰는 긴 프롬프트는 코드에 직접 쓰지 않고 `sys_prompts/*.md`에서 관리한다.
- `sys_prompts` 파일 상단의 설명 영역은 개발자 확인용이며 모델에 전달하지 않는다.
- 실제 모델에 전달되는 내용은 `---PROMPT---` 아래 본문만 사용한다.
- 프롬프트 파일에서 Python `format_map` placeholder를 사용하므로, 모델에 보낼 JSON 예시의 중괄호는 `{{`와 `}}`로 이스케이프한다.
