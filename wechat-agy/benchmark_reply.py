"""Measure High-model live-chat prompts without sending anything to WeChat."""
import json
from types import SimpleNamespace
from pathlib import Path
import time

from agy_client import AgySession, ROOT, MODEL
from chat_prompt import reply_payload


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default=MODEL)
    args = parser.parse_args()
    rows = []
    archive = SimpleNamespace(recent=lambda n: rows[-n:], profiles=lambda ids: [])
    persona = (ROOT / 'PERSONA.md').read_text(encoding='utf-8')
    session = AgySession(label='latency-check', model=args.model)
    results = []
    try:
        session.ask({'task': 'warmup', 'persona': persona.partition('原创口吻示例')[0],
                     'instruction': '准备接收群聊，现在只输出 {"reply":false,"text":""}。'})
        for i, text in enumerate(['大肥鱼你会做饭吗', '那就泡面加个蛋吧', '等会，不吃泡面了，楼下新开了家火锅店'], 1):
            row = {'id': i, 'name': '测试群友', 'member_id': 'sample', 'created': time.time(),
                   'is_self': False, 'is_at': i == 1, 'kind': '文本', 'content': text}
            rows.append(row)
            payload, _ = reply_payload(archive, [row], persona)
            reply = session.ask(payload)
            item = {'input': text, 'reply': reply,
                    'model': args.model,
                    'seconds': session.last_result['seconds'],
                    'prompt_chars': len(json.dumps(payload, ensure_ascii=False)),
                    'steps': session.last_result.get('steps'),
                    'conversation_id': session.last_result['conversation_id']}
            results.append(item)
            print(json.dumps(item, ensure_ascii=True), flush=True)
        (ROOT / '.runtime' / f'reply-latency-{args.model}.json').write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        session.close()


if __name__ == '__main__':
    main()
