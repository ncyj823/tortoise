import re, sys, py_compile

path = r'agent_team/core.py'
s = open(path, encoding='utf-8').read()

# add helper
if '_is_quota_or_server_error' not in s:
    helper = '''def _is_quota_or_server_error(msg: str) -> bool:\n    m = (msg or "").lower()\n    return any(k in m for k in ("429", "rate limit", "quota", "insufficient", "exhausted",\n                                "tokens per minute", "tpm", "requests per minute", "rpm",\n                                "503", "502", "500", "504", "overloaded", "capacity",\n                                "rate_limit_exceeded"))\n\n\n'''
    # insert after LLMOutputError
    idx = s.find('class LLMOutputError')
    if idx == -1: sys.exit('no LLMOutputError')
    # find end of class
    # next def
    idx2 = s.find('\n\n', idx)
    s = s[:idx2+2] + helper + s[idx2+2:]

new_chat = r'''def _chat(system: str, user: str, max_tokens: int) -> str:
    """OpenAI-compatible chat completion with fallback + truncation handling."""
    base = (os.getenv("LLM_BASE_URL") or "").strip().rstrip("/")
    key = (os.getenv("LLM_API_KEY") or "").strip()
    model = (os.getenv("LLM_MODEL") or "").strip()
    if not base:
        raise LLMError("LLM_BASE_URL is empty in .env")
    if not model:
        raise LLMError("LLM_MODEL is empty in .env")
    if not key:
        raise LLMError("LLM_API_KEY is empty in .env")

    fb_base = (os.getenv("LLM_FALLBACK_BASE_URL") or "").strip().rstrip("/")
    fb_key = (os.getenv("LLM_FALLBACK_API_KEY") or "").strip()
    fb_model = (os.getenv("LLM_FALLBACK_MODEL") or "").strip()
    has_fb = bool(fb_base and fb_key and fb_model)

    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    mt = max_tokens
    truncated_retry = False

    def _post(b, k, m, u, tokens, sys_msg=None, usr_msg=None):
        s_msg = sys_msg if sys_msg is not None else system
        u_msg = usr_msg if usr_msg is not None else user
        return httpx.post(u, timeout=LLM_TIMEOUT,
                          headers={"Authorization": f"Bearer {k}"},
                          json={"model": m, "max_tokens": tokens, "temperature": 0.4,
                                "messages": [{"role": "system", "content": s_msg},
                                             {"role": "user", "content": u_msg}]})

    def _try_fallback(tokens, sys_msg=None, usr_msg=None):
        if not has_fb:
            return None
        try:
            fb_url = fb_base if fb_base.endswith("/chat/completions") else fb_base + "/chat/completions"
            r2 = _post(fb_base, fb_key, fb_model, fb_url, tokens, sys_msg, usr_msg)
            if r2.status_code < 400:
                data = r2.json()
                choice = (data.get("choices") or [])[0]
                content = (choice.get("message") or {}).get("content") or ""
                if content.strip():
                    log.info("LLM fallback used: %s", fb_model)
                    return content
            return None
        except Exception:
            return None

    for attempt in range(6):
        try:
            r = _post(base, key, model, url, mt)
        except httpx.TimeoutException:
            if attempt < 5:
                time.sleep(3 * (attempt + 1))
                continue
            fb = _try_fallback(mt)
            if fb is not None:
                return fb
            raise LLMError(f"The LLM provider did not answer within {LLM_TIMEOUT:.0f}s.") from None
        except httpx.HTTPError as e:
            if attempt < 5:
                time.sleep(3 * (attempt + 1))
                continue
            if _is_quota_or_server_error(str(e)):
                fb = _try_fallback(mt)
                if fb is not None:
                    return fb
            raise LLMError(f"Could not reach the LLM provider: {e}") from None

        body = r.text[:500]
        if r.status_code == 429:
            if attempt < 5:
                time.sleep(10 * (attempt + 1))
                continue
            if _is_quota_or_server_error(body):
                fb = _try_fallback(mt)
                if fb is not None:
                    return fb
            raise LLMError("The LLM provider is rate-limiting this key (429). Free tiers have per-minute limits - wait a minute and try again.")
        if r.status_code in (500, 502, 503, 504):
            if attempt < 5:
                time.sleep(5 * (attempt + 1))
                continue
            fb = _try_fallback(mt)
            if fb is not None:
                return fb
            raise LLMError(f"LLM provider error ({r.status_code}). Try again in a moment.")
        if r.status_code == 400 and "max_tokens" in body.lower() and mt > 1024:
            mt = max(1024, mt // 2)
            log.warning("Provider rejected max_tokens=%s, retrying with %s", max_tokens, mt)
            continue
        if r.status_code >= 400:
            if _is_quota_or_server_error(body):
                fb = _try_fallback(mt)
                if fb is not None:
                    return fb
            raise LLMError(f"LLM request failed ({r.status_code}): {body.strip() or r.reason_phrase}")
        try:
            data = r.json()
            choice = (data.get("choices") or [])[0]
            content = (choice.get("message") or {}).get("content") or ""
            finish = choice.get("finish_reason")
        except Exception:
            fb = _try_fallback(mt)
            if fb is not None:
                return fb
            raise LLMError("Unexpected reply from the LLM provider") from None
        if not content.strip():
            raise LLMError("The model returned an empty reply. Try again.")
        if finish == "length" and not truncated_retry:
            truncated_retry = True
            log.warning("Reply truncated (finish_reason=length); retrying concisely")
            sys2 = system + " Be extremely concise. Avoid long descriptions."
            usr2 = user + "\n\nBE CONCISE. Return valid JSON only if asked. Be brief. Max 6 steps per list."
            try:
                r2 = _post(base, key, model, url, max(512, mt // 2), sys2, usr2)
                if r2.status_code < 400:
                    data2 = r2.json()
                    choice2 = (data2.get("choices") or [])[0]
                    c2 = (choice2.get("message") or {}).get("content") or ""
                    if c2.strip() and choice2.get("finish_reason") != "length":
                        log.info("LLM answered via primary (concise retry): %s", model)
                        return c2
            except Exception:
                pass
            fb = _try_fallback(max(512, mt // 2), sys2, usr2)
            if fb is not None:
                return fb
        if finish == "length":
            raise LLMError("truncated reply (finish_reason=length); response cut off")
        log.info("LLM answered via primary: %s", model)
        return content
    raise LLMError("LLM request failed after several retries.")
'''

start = s.find('def _chat')
if start == -1: sys.exit('no _chat')
end = s.find('\n\n\ndef ', start)
if end == -1: end = s.find('\n\ndef ask(', start)
s2 = s[:start] + new_chat + '\n\n\n' + s[end+1:]
open(path, 'w', encoding='utf-8').write(s2)
py_compile.compile(path, doraise=True)
print('ok')
