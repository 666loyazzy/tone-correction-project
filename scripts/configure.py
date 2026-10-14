"""Generate deployment secrets locally, without printing or overwriting them."""
import os
import secrets
from pathlib import Path

destination=Path(__file__).resolve().parents[1]/'.env'
if destination.exists():
    raise SystemExit('.env 已存在，未覆盖。请直接编辑已有配置。')
content=(f'POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n'
         f'SETUP_TOKEN={secrets.token_urlsafe(32)}\n'
         'SITE_ADDRESS=localhost\nAUDIO_RETENTION_DAYS=30\n'
         'LLM_BASE_URL=https://api.deepseek.com\nLLM_MODEL=deepseek-v4-pro\nLLM_API_KEY=\n'
         'LLM_REASONING_EFFORT=high\nLLM_MAX_TOKENS=8192\nLLM_TIMEOUT_SECONDS=120\n')
with os.fdopen(os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as output:
    output.write(content)
print('已创建 .env。数据库密码和初始化口令已写入本地文件，没有输出到日志。')
print('部署前填写 SITE_ADDRESS、LLM_MODEL、LLM_API_KEY；首个教师账号使用 SETUP_TOKEN 初始化。')
