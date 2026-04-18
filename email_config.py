# =============================================
#  이메일 발송 설정
# =============================================
#
# [Gmail 사용 시]
#  1. Google 계정 → 보안 → 2단계 인증 활성화
#  2. Google 계정 → 보안 → 앱 비밀번호 생성
#     (앱: 메일 / 기기: Windows)
#  3. 생성된 16자리 비밀번호를 SENDER_PASSWORD 에 입력
#  4. ENABLED = True 로 변경 후 저장
#
# [네이버 메일 사용 시]
#  1. 네이버 메일 → 환경설정 → POP3/SMTP 사용 허용
#  2. 아래 주석 참고하여 서버 변경
#
# =============================================

ENABLED = False   # 설정 완료 후 True 로 변경

# Gmail
SMTP_SERVER   = 'smtp.gmail.com'
SMTP_PORT     = 587

# 네이버 사용 시 아래 두 줄로 교체
# SMTP_SERVER = 'smtp.naver.com'
# SMTP_PORT   = 587

SENDER_EMAIL    = ''   # 발신 이메일  예) company@gmail.com
SENDER_PASSWORD = ''   # 앱 비밀번호  예) abcd efgh ijkl mnop
