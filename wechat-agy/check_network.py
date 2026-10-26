"""Exercise real AGY search routing; never sends to WeChat."""
import json
import time
from types import SimpleNamespace
from agy_client import AgySession, ROOT, CHAT_MODEL, MODEL, FAST_REPLY_SCHEMA, RESEARCH_REPLY_SCHEMA
from chat_prompt import reply_payload
from reply_router import generate_reply


def main():
    row = {'id': 1, 'name': '测试群友', 'member_id': 'sample', 'created': time.time(),
           'is_self': False, 'is_at': True, 'kind': '文本',
           'content': '请联网查一下长鑫科技目前是否已经上市，给出上市日期和代码，用原始公告核实即可，先不要算指数权重。'}
    archive = SimpleNamespace(recent=lambda n: [row], profiles=lambda ids: [])
    fast = AgySession(model=CHAT_MODEL, schema=FAST_REPLY_SCHEMA, label='network-fast')
    high = AgySession(model=MODEL, agent='wechat-researcher', schema=RESEARCH_REPLY_SCHEMA,
                      label='network-high', timeout=180)
    try:
        payload, _ = reply_payload(archive, [row], (ROOT / 'PERSONA.md').read_text(encoding='utf-8'))
        result, model, seconds = generate_reply(fast, high, payload)
        record = {'model': model, 'seconds': seconds, 'result': result, 'steps': high.last_result.get('steps')}
        (ROOT / '.runtime' / 'network-check.json').write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(record, ensure_ascii=True), flush=True)
        if not result.get('sources'):
            raise AssertionError('尚未拿到实际网页来源')
    finally:
        fast.close()
        high.close()


if __name__ == '__main__':
    main()
