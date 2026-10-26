"""Live model routing check with synthetic inputs and no WeChat sends."""
import json
import time
from types import SimpleNamespace
from agy_client import AgySession, ROOT, CHAT_MODEL, MODEL, FAST_REPLY_SCHEMA, RESEARCH_REPLY_SCHEMA
from chat_prompt import reply_payload
from reply_router import generate_reply


def main():
    rows, checks = [], []
    archive = SimpleNamespace(recent=lambda n: rows[-n:], profiles=lambda ids: [])
    fast = AgySession(model=CHAT_MODEL, schema=FAST_REPLY_SCHEMA, label='routing-fast')
    high = AgySession(model=MODEL, label='routing-high', agent='wechat-researcher', schema=RESEARCH_REPLY_SCHEMA)
    try:
        for i, (text, expected) in enumerate([
            ('大肥鱼你会做饭吗', CHAT_MODEL),
            ('本金 1000 元，每年增长 10%，连续两年不追加，第二年末是多少？请算清楚。', MODEL),
        ], 1):
            row = {'id': i, 'name': '测试群友', 'member_id': 'sample', 'created': time.time(),
                   'is_self': False, 'is_at': True, 'kind': '文本', 'content': text}
            rows.append(row)
            payload, _ = reply_payload(archive, [row], (ROOT / 'PERSONA.md').read_text(encoding='utf-8'))
            result, used, seconds = generate_reply(fast, high, payload)
            if used != expected:
                raise AssertionError(f'路由应为 {expected}，实际 {used}')
            if i == 2 and '1210' not in result['text'].replace(',', ''):
                raise AssertionError('复利计算未返回 1210')
            item = {'input': text, 'model': used, 'seconds': seconds, 'result': result}
            checks.append(item)
            print(json.dumps(item, ensure_ascii=True), flush=True)
        (ROOT / '.runtime' / 'routing-check.json').write_text(
            json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        fast.close()
        high.close()


if __name__ == '__main__':
    main()
