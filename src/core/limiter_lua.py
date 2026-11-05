"""Атомарный token bucket проекта для Dragonfly."""

TOKEN_BUCKET_LUA = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local values = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens = tonumber(values[1]) or 20
local updated = tonumber(values[2]) or now
tokens = math.min(20, tokens + math.max(0, now - updated) * 5)
if tokens < 1 then
  redis.call('HMSET', KEYS[1], 'tokens', tokens, 'updated', now)
  redis.call('EXPIRE', KEYS[1], 10)
  return 0
end
redis.call('HMSET', KEYS[1], 'tokens', tokens - 1, 'updated', now)
redis.call('EXPIRE', KEYS[1], 10)
return 1
"""
