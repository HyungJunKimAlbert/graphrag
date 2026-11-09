# 증상-기전 연결의 공식 출처 찾기 (그룹 작업 지시)

## 목적
건강기능식품 영양성분 추천 서비스가 "증상 → 기전" 연결을 근거로 쓴다. 현재 연결은 근거 없는 초안이다.
각 연결(symptom, mechanism)을 **공식 출처의 실제 문장**으로 뒷받침할 수 있는지 확인하고, 출처를 남긴다.

## 허용 출처 (이 도메인만. 그 외는 쓰지 않는다)
- 식품의약품안전처: mfds.go.kr, foodsafetykorea.go.kr, impfood.mfds.go.kr
- 질병관리청 국가건강정보포털: health.kdca.go.kr / 질병관리청 kdca.go.kr
- 보건복지부 mohw.go.kr (한국인 영양소 섭취기준 등), 국민건강보험공단 nhis.or.kr, 서울대병원 아님(X)
- 미국 NIH: ods.od.nih.gov(영양소 팩트시트), medlineplus.gov, nccih.nih.gov, niddk.nih.gov, nhlbi.nih.gov, nia.nih.gov, ninds.nih.gov, nei.nih.gov, niams.nih.gov, ncbi.nlm.nih.gov/books(NBK StatPearls 제외 — 공식 기관 저작물만)
- 미국 CDC cdc.gov, WHO who.int
대학병원·제약사·블로그·위키·뉴스·MSD 매뉴얼은 쓰지 않는다.

## 방법
1. 그룹 파일(`group_X.json`)의 기전마다, 그 기전과 관련 증상을 설명하는 공식 페이지를 찾는다(WebSearch 사용 가능하면 사용, 아니면 알려진 URL 패턴: ODS `https://ods.od.nih.gov/factsheets/<Nutrient>-Consumer/` 또는 `-HealthProfessional/`, MedlinePlus `https://medlineplus.gov/...`, KDCA 국가건강정보포털 검색).
2. 페이지 원문을 **저장**한다: 먼저 Bash로 `curl -sL -A 'Mozilla/5.0' <url>`을 받아 python으로 HTML 태그를 제거한 본문 텍스트를 `evidence/sources/<source_id>.txt`에 저장한다(source_id 예: `ODS-magnesium-hp`, `MLP-insomnia`, `KDCA-<짧은영문>`). curl이 막히면(403 등) WebFetch로 본문을 가져와 "본문 그대로 옮겨 달라"고 요청해 같은 파일에 저장하고 `fetch_method: "webfetch"`로 표시한다.
3. 각 연결에 대해 저장한 텍스트 안에서 **증상과 기전(원인·결핍·생리)을 함께 잇는 문장**을 찾는다. 인용(quote)은 저장 파일에 있는 문자열을 **한 글자도 바꾸지 않고** 복사한다(공백 정리만 허용). 증상만 나오고 기전과의 관계가 없는 문장은 근거가 아니다.
4. 찾지 못하면 `status: "not_found"`로 둔다. **추측·일반 지식으로 채우지 않는다.** 부분적으로만 맞으면(예: 증상이 더 넓은 범주로만 언급) `status: "partial"`과 note에 이유.

## 출력 (반드시 이 경로·형식)
`evidence/results/group_X.json`:
```json
{"group":"X",
 "sources":[{"source_id":"ODS-magnesium-hp","org":"NIH Office of Dietary Supplements","title":"Magnesium - Health Professional Fact Sheet","url":"...","accessed":"2026-11-09","fetch_method":"curl|webfetch","file":"sources/ODS-magnesium-hp.txt"}],
 "links":[{"symptom":"근육경련(쥐남)","mech_id":"X3","mech":"X3 마그네슘 부족","level":"1차",
           "status":"supported|partial|not_found","source_id":"ODS-magnesium-hp",
           "quote":"원문 그대로","quote_ko":"인용의 한국어 번역(원문이 영어일 때)","note":""}]}
```
- 입력 links의 모든 행을 빠짐없이 출력한다(행 수 동일). 한 연결에 근거가 여럿이면 가장 직접적인 1개.
- 최종 답변(보고)은 짧게: supported/partial/not_found 개수, 사용한 출처 수, 막힌 사이트. 파일 내용 덤프 금지.

## 금지
- 프로젝트 파일 수정, DB 접속, .env 읽기 금지. 쓰기는 `evidence/` 아래만.
