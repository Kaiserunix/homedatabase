from __future__ import annotations

import hashlib
from functools import lru_cache

from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError

from ..config import Settings

_RATE_LIMIT_SCRIPT = """
local window = tonumber(ARGV[1])
for index, key in ipairs(KEYS) do
  local count = redis.call('INCR', key)
  local ttl = redis.call('TTL', key)
  if count == 1 or ttl < 0 then
    redis.call('EXPIRE', key, window)
    ttl = window
  end
  local limit = tonumber(ARGV[index + 1])
  if count > limit then
    return {index, ttl}
  end
end
return {0, 0}
"""


@lru_cache
def _client(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=True, socket_timeout=2)


def enforce_auth_rate_limit(request: Request, subject: str, settings: Settings) -> None:
    if settings.environment != "production":
        return
    client_host = request.client.host if request.client else "unknown"
    normalised_subject = subject.casefold()

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    pair_digest = digest(f"{client_host}\0{normalised_subject}")
    keys = [
        f"homedatabase:auth-limit:pair:{pair_digest}",
        f"homedatabase:auth-limit:ip:{digest(client_host)}",
        f"homedatabase:auth-limit:subject:{digest(normalised_subject)}",
    ]
    try:
        redis = _client(settings.redis_url)
        bucket, retry_after = redis.eval(
            _RATE_LIMIT_SCRIPT,
            len(keys),
            *keys,
            settings.auth_rate_limit_window_seconds,
            settings.auth_rate_limit_attempts,
            settings.auth_rate_limit_ip_attempts,
            settings.auth_rate_limit_subject_attempts,
        )
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="登录保护服务暂不可用") from exc
    if int(bucket) > 0:
        raise HTTPException(
            status_code=429,
            detail="尝试次数过多，请稍后再试",
            headers={"Retry-After": str(max(int(retry_after), 1))},
        )
