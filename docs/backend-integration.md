# POSTALK 백엔드 연동 안내

이 문서는 카드뉴스 서비스와 POSTALK 백엔드가 실제 운영 환경에서 연결되는 방식을 설명한다. 브라우저나 프론트엔드는 이 서비스에 직접 접근하지 않는다. 파일 업로드, 작업 권한 확인, 생성 결과 보관은 모두 백엔드가 맡고, 이 서비스는 전달받은 재료를 PNG 카드뉴스로 렌더링한다.

## 1. 역할과 통신 범위

| 구성 요소 | 담당 역할 | 외부 공개 여부 |
| --- | --- | --- |
| 프론트엔드 | 사진 업로드, 생성 시작, 진행률과 결과 표시 | 공개 |
| POSTALK 백엔드 | 세션·파일 검증, Supabase 서명 URL 발급, 생성 작업 상태와 결과 저장 | 공개 |
| 카드뉴스 서비스 | 사진 6장 분석, 문구·템플릿 선택, PNG 3장 생성 및 업로드 | Railway 내부 전용 |
| Supabase Storage | 원본 사진과 생성 PNG 보관 | 서명 URL로만 접근 |

프론트엔드는 `POST /cardnews/renders`를 호출하지 않는다. 이 경로에 필요한 내부 API 키와 서명 URL은 브라우저에 노출해서는 안 된다.

## 2. 생성 흐름

1. 사용자가 POSTALK 프론트에서 사진을 올린다.
2. 백엔드는 Supabase Storage에 원본을 저장하고 카드뉴스 세션에 연결한다.
3. 여섯 번째 사진이 제출되면 백엔드는 생성 작업을 만들고, 사진 6개의 서명 다운로드 URL과 결과 PNG 3개의 서명 업로드 URL을 발급한다.
4. 백엔드는 Railway 내부 주소의 `POST /cardnews/renders`를 호출한다.
5. 카드뉴스 서비스는 각 사진을 임시 공간으로 받아 테마에 맞는 HTML/Chromium 렌더링을 수행한다.
6. 완성된 PNG 3장을 백엔드가 지정한 Supabase 경로에 업로드하고, 경로·파일 크기·사용 템플릿을 응답으로 돌려준다.
7. 백엔드는 생성 결과를 DB에 저장한다. 프론트는 백엔드의 생성 작업 조회 API만 폴링한다.

## 3. 백엔드 호출 계약

### 인증과 주소

| 항목 | 값 | 주의 사항 |
| --- | --- | --- |
| 내부 주소 | `http://postalk-cardnews.railway.internal:8000` | 같은 Railway 프로젝트·환경에서만 사용 |
| 상태 확인 | `GET /healthz` | 인증 없음. 서비스 이름과 미디어 저장소를 포함한 상태 응답 반환 |
| 생성 경로 | `POST /cardnews/renders` | `x-cardnews-api-key` 필수 |
| 인증 키 | `CARDNEWS_INTERNAL_API_KEY` | 백엔드와 서비스의 값이 완전히 같아야 함 |

`POST /cardnews/renders`의 요청 본문은 다음 규칙을 만족해야 한다.

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `job_id` | 예 | 백엔드가 만든 UUID |
| `theme` | 예 | `minimal` 또는 `christmas` |
| `store_name` | 예 | 1~200자 |
| `area`, `menu_name`, `direction` | 아니오 | 각각 지역·대표 메뉴·디자인 보충 요청 |
| `photos` | 예 | 6개, 역할 중복 없이 모두 포함 |
| `uploads` | 예 | PNG 3개용 Supabase 서명 업로드 URL |

사진 역할은 `bowl`, `zoom`, `food1`, `food2`, `store1`, `store2`를 각각 한 번씩만 넣는다. 같은 파일을 여러 역할에 재사용할 수 없다. 입력과 출력 URL은 현재 `SUPABASE_URL`과 같은 프로젝트의 `uploads` 버킷 서명 URL이어야 한다.

성공 응답 예시는 아래와 같다.

```json
{
  "cards": [
    { "key": "stores/.../generated/<job-id>-1.png", "bytes": 123456 }
  ],
  "template": "x1",
  "fallback": false
}
```

`fallback`이 `true`이면 OpenAI 분석 또는 문구 생성 대신 기본 문구와 기본 템플릿으로 렌더링한 결과다. 결과 PNG는 정상 생성되므로 백엔드는 실패로 처리하지 않는다.

## 4. 환경 변수

| 변수 | 카드뉴스 서비스 | 백엔드 | 설명 |
| --- | --- | --- | --- |
| `SUPABASE_URL` | 필요 | 필요 | 같은 Supabase 프로젝트 URL. 서비스에서는 서명 URL 검증에 사용 |
| `CARDNEWS_INTERNAL_API_KEY` | 필요 | 필요 | 서버 간 호출 인증 값 |
| `CARDNEWS_API_URL` | 불필요 | 필요 | Railway 내부 서비스 주소 |
| `OPENAI_API_KEY` | 선택 | 불필요 | 사진 분석·문구 생성. 없으면 기본 문구로 정상 렌더링 |
| `PORT` | 필요 | 불필요 | 기본 예시 `8000` |
| `CARDNEWS_CHROMIUM_PATH` | 선택 | 불필요 | 로컬 Chromium 경로가 기본 탐색 위치와 다를 때만 설정 |

실제 키, Supabase URL의 비밀값, 서명 URL은 README·커밋·PR 본문·로그에 기록하지 않는다.

## 5. Railway 배포와 점검

1. 이 저장소를 Railway의 별도 Docker 서비스로 연결한다.
2. 서비스에 `SUPABASE_URL`, `CARDNEWS_INTERNAL_API_KEY`, `PORT=8000`을 설정한다.
3. 백엔드에 `CARDNEWS_API_URL=http://postalk-cardnews.railway.internal:8000`과 동일한 `CARDNEWS_INTERNAL_API_KEY`를 설정한다.
4. 백엔드 DB에 카드뉴스 촬영 슬롯 마이그레이션을 적용한다.
5. 카드뉴스 서비스 로그에서 컨테이너 기동과 `GET /healthz` 성공을 확인한다.
6. 백엔드의 준비 상태와 카드뉴스 생성 작업을 각각 한 건씩 확인한다.

서비스는 Chromium과 한글 폰트를 Docker 이미지에 포함한다. 따라서 렌더링 의존성을 실행 중에 설치할 필요가 없다.

## 6. 장애 확인 순서

| 증상 | 먼저 확인할 곳 | 대표 원인 |
| --- | --- | --- |
| 백엔드에서 카드뉴스 생성이 시작되지 않음 | 백엔드 환경 변수와 로그 | `CARDNEWS_API_URL` 또는 내부 키 누락 |
| `401 Invalid credentials` | 두 서비스의 내부 키 | 키 불일치 |
| `400` 응답 | 백엔드가 만든 요청 | 사진 6장 미완료, 역할 중복, 잘못된 서명 URL 또는 결과 경로 |
| `502 Unable to create card news` | 카드뉴스 Railway 로그 | 이미지 다운로드 실패, Chromium 렌더링 실패, Storage 업로드 실패 |
| 결과는 생성됐지만 문구가 기본값 | 카드뉴스 로그와 OpenAI 설정 | OpenAI 키 미설정 또는 분석·문구 생성 실패. 렌더링 자체는 정상 |

오류 로그에 서명 URL 전체나 내부 API 키를 남기지 않는다. 오류를 공유할 때는 상태 코드, 작업 ID, 예외 종류만 기록한다.

## 7. 로컬 검증

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
python -c "from cardnews.design import ensure_fonts; ensure_fonts()"
python -m unittest discover -s tests
```

로컬에서 실제 내부 API를 호출하려면 `SUPABASE_URL`과 테스트용 `CARDNEWS_INTERNAL_API_KEY`를 설정한다. 운영 Supabase의 서명 URL이나 운영 키를 테스트 코드와 샘플 파일에 넣지 않는다.
