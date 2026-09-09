# VOCALOID COSPLAY — 카카오톡 테마

보컬로이드 코스프레 사진 13장으로 만든 카카오톡 테마 + 키보드 배경.

![preview](docs/assets/preview.png)

---

## 아이폰 — 카카오톡 테마

### 다운로드

**[VocaloidCosplay.ktheme 받기](https://github.com/DevToolXD/comi2/raw/refs/heads/claude/kakaotalk-theme-creation-bho737/ios/VocaloidCosplay.ktheme)** (5.9MB)

### 적용 방법

1. 위 파일을 아이폰에서 다운로드
2. 카카오톡 **나와의 채팅방**에 파일 전송
3. 채팅방에 올라온 파일을 눌러 다운로드
4. 파일 미리보기 화면 우측 상단 **공유 버튼** → **카카오톡에 복사**
   - 또는 파일을 눌러 **테마 적용하기**
5. 카카오톡 → 설정 → 테마 에서 `VOCALOID COSPLAY` 선택

### 들어있는 것

| 항목 | 내용 |
| --- | --- |
| 친구목록 / 채팅목록 배경 | 사진 13장 모자이크 (흐림 처리, 글자 잘 보이게 어둡게) |
| 채팅방 배경 | 사진 13장 모자이크 |
| 말풍선 | 내 말풍선 미쿠 민트 `#39C5BB` / 상대 말풍선 다크 그레이 |
| 탭 아이콘 | 친구·채팅·오픈채팅·검색·게임·더보기 (기본/선택 상태) |
| 기본 프로필 | 미쿠 사진 |
| 잠금화면 | 배경 + 숫자 입력 점 + 키패드 눌림 효과 |
| 알림바 / 공유바 | 핑크 이름 `#FF6FA5` + 밝은 본문 |

---

## 키보드 배경 — 아이폰 · 갤럭시 공통

자판 키 하나하나에 얼굴이 박혀 있는 키보드 배경입니다. 총 35개 키 자리에 사진 13장이 순서대로 반복해서 들어갑니다. QWERTY와 한글 두벌식이 같은 배열이라 두 자판 모두 맞습니다.

### 다운로드

| 파일 | 크기 | 설명 |
| --- | --- | --- |
| **[keyboard_vocaloid.png](https://github.com/DevToolXD/comi2/raw/refs/heads/claude/kakaotalk-theme-creation-bho737/keyboard/keyboard_vocaloid.png)** | 1440×1040 | 기본 (추천) |
| **[keyboard_vocaloid_tall.png](https://github.com/DevToolXD/comi2/raw/refs/heads/claude/kakaotalk-theme-creation-bho737/keyboard/keyboard_vocaloid_tall.png)** | 1440×1300 | 숫자줄 있는 키보드용 |
| **[keyboard_vocaloid_dark.png](https://github.com/DevToolXD/comi2/raw/refs/heads/claude/kakaotalk-theme-creation-bho737/keyboard/keyboard_vocaloid_dark.png)** | 1440×1040 | 더 어두운 버전 (글자 잘 보임) |

### 적용 방법 — Gboard (아이폰 · 갤럭시 둘 다)

1. Gboard 앱 설치 후 키보드로 설정
2. 키보드 열고 왼쪽 위 톱니바퀴 → **테마**
3. **내 테마** → `+` → 위 이미지 선택
4. 밝기 조절 후 **완료** → 키 테두리 **켜기** 추천

### 적용 방법 — 삼성 키보드 (갤럭시)

삼성 키보드는 배경 이미지를 직접 넣는 기능이 One UI 버전에 따라 없을 수 있습니다. 없으면 Gboard를 쓰세요.

---

## 갤럭시 — 카카오톡 배경

갤럭시 카카오톡 테마는 원래 `.apk` 파일로 만들어야 하고, 카카오가 제공하는 공식 샘플 프로젝트가 있어야 리소스 이름이 맞습니다. 이 작업 환경에서 카카오 서버 접속이 막혀 있어서 **정상 동작을 보장하는 갤럭시 테마 apk는 만들지 못했습니다.**

대신 카카오톡 기본 기능으로 배경만 바꾸면 아이폰 테마와 거의 같은 느낌이 납니다.

### 다운로드

| 파일 | 크기 | 용도 |
| --- | --- | --- |
| **[chatroom_background.png](https://github.com/DevToolXD/comi2/raw/refs/heads/claude/kakaotalk-theme-creation-bho737/android/images/chatroom_background.png)** | 1440×3120 | 카카오톡 채팅방 배경 |
| **[wallpaper.png](https://github.com/DevToolXD/comi2/raw/refs/heads/claude/kakaotalk-theme-creation-bho737/android/images/wallpaper.png)** | 1440×3120 | 폰 배경화면 |
| **[theme_icon.png](https://github.com/DevToolXD/comi2/raw/refs/heads/claude/kakaotalk-theme-creation-bho737/android/images/theme_icon.png)** | 512×512 | 프로필 / 아이콘용 |

### 적용 방법

- **모든 채팅방**: 카카오톡 → 설정 → 채팅 → 배경화면 → 앨범에서 선택
- **특정 채팅방만**: 채팅방 → 메뉴(≡) → 채팅방 설정 → 배경화면

---

## 직접 다시 만들기

```bash
python3 -m pip install pillow
python3 tools/build_assets.py      # 테마 이미지 생성
python3 tools/package_ktheme.py    # .ktheme 패키징
python3 tools/build_keyboard.py    # 키보드 배경 생성
python3 tools/build_preview.py     # 미리보기 목업
```

색을 바꾸려면 `ios/src/KakaoTalkTheme.css`의 색상값을, 사진 순서를 바꾸려면 `tools/build_assets.py`의 `ORDER`를 고치면 됩니다.

### 폴더 구조

```
photos/           원본 사진 13장
ios/src/Images/   테마 이미지 58개
ios/src/KakaoTalkTheme.css
ios/VocaloidCosplay.ktheme    ← 아이폰용 완성 파일
keyboard/         키보드 배경
android/images/   갤럭시용 배경
tools/            생성 스크립트
docs/             다운로드 페이지
```

`.ktheme` 파일은 그냥 zip입니다. 확장자를 `.zip`으로 바꾸면 안을 열어볼 수 있어요.
