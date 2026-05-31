<!--
사용 위치: app/services/topic_generator.py / TopicGenerator._generate_with_ai
목적: 채널별 주간 소주제 후보를 JSON으로 생성한다.
주의: 이 설명 영역은 개발자가 확인하기 위한 메모이며 Gemini/Claude에 전달되지 않는다.
-->

---PROMPT---

채널명: {channel_name}
주차: {week_key}
필요한 소주제 수: {count}
대상: 50대 이상 한국어 시청자

규칙:
- 정보제공형과 클릭유도형을 섞는다.
- 제목은 실제 YouTube 제목으로 바로 사용할 수 있어야 한다.
- 과장, 허위, 공포 마케팅을 피한다.
- 건강/금융/법률 관련 주제는 단정적 표현을 피한다.
- 각 항목마다 실제 기획 의도와 대본 요약을 구체적으로 쓴다.

아래 JSON만 반환해라.
{{
  "topics": [
    {{
      "topic_title": "제목",
      "topic_type": "정보제공형 또는 클릭유도형",
      "planning_note": "이 영상을 왜 만들고 어떤 흐름으로 전달할지",
      "script_summary": "인트로, 본문 핵심, 마무리가 보이는 구체적 요약"
    }}
  ]
}}
