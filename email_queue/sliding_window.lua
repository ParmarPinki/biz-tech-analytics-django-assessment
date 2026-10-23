-- Redis supplies the clock, so different workers cannot disagree about time.
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

-- The active window is (now - window, now]. Each attempt has a unique member.
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    return {0, math.max(1, tonumber(oldest[2]) + window - now + 1)}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window + 1000)
return {1, 0}
