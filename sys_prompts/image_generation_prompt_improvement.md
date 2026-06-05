<!--
사용 위치: app/services/media_sources.py / MediaSourceService._improve_generation_prompt
목적: AI 이미지 생성을 명시적으로 선택했을 때 장면용 이미지 프롬프트를 정리한다.
주의: 이 설명 영역은 개발자가 확인하기 위한 메모이며 Gemini/Claude에 전달되지 않는다.
-->

---PROMPT---

영상 장면용 이미지 생성 프롬프트를 한국어로 정리해줘.
저작권 문제가 없는 원본 생성 이미지여야 하고, 50대 이상 시청자가 편안하게 볼 수 있어야 한다.
프롬프트 본문만 반환한다. 인사, 설명, "네, 요청하신" 같은 답변체, 마크다운, 따옴표는 쓰지 않는다.

장면 설명: {scene_visual_prompt}
사용자 지시: {user_instruction}
