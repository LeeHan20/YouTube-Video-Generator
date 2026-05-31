<!--
사용 위치: app/services/topic_generator.py / TopicGenerator.enrich_existing_topic
목적: 이미 존재하는 소주제 제목에 대해 기획 의도와 대본 요약을 보강한다.
주의: 이 설명 영역은 개발자가 확인하기 위한 메모이며 Gemini/Claude에 전달되지 않는다.
-->

---PROMPT---

다음 YouTube 소주제에 대해 실제 기획 의도와 대본 요약을 한국어로 작성해줘.

채널명: {channel_name}
제목: {title}
주제 유형: {topic_type}
대상: 50대 이상

규칙:
- 테스트 문구가 아니라 제목에 맞는 구체적인 내용을 쓴다.
- 과장, 허위, 공포 마케팅을 피한다.
- 건강/금융/법률은 단정하지 않는다.
- JSON만 반환한다.

{{"planning_note":"...", "script_summary":"..."}}
