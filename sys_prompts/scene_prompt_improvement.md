<!--
사용 위치: app/services/scene_planner.py / ScenePlanner.improve_prompt
목적: 검수 UI에서 사용자가 요청한 장면 재생성 지시를 이미지/영상 프롬프트로 개선한다.
주의: 이 설명 영역은 개발자가 확인하기 위한 메모이며 Gemini/Claude에 전달되지 않는다.
-->

---PROMPT---

아래 영상 장면을 다시 생성하기 위한 이미지/영상 프롬프트를 한국어로 개선해줘.
50대 이상 시청자에게 편안하고 과장되지 않아야 해.

영상 제목: {title}
기존 장면 설명: {scene_visual_prompt}
사용자 요청: {user_instruction}
