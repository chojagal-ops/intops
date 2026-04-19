import os

SMTP_SERVER     = os.environ.get('SMTP_SERVER',   'smtp.gmail.com')
SMTP_PORT       = int(os.environ.get('SMTP_PORT', '587'))
SENDER_EMAIL    = os.environ.get('SMTP_EMAIL',    '')
SENDER_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
ENABLED         = bool(SENDER_EMAIL and SENDER_PASSWORD)
