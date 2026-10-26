"""A persistent, serialized Antigravity CLI session using account sign-in."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from app_paths import ROOT, find_agy

MODEL = 'gemini-3.8-flash-high'
CHAT_MODEL = 'gemini-3.8-flash-low'
REPLY_SCHEMA = {
    'type': 'object',
    'properties': {'reply': {'type': 'boolean'}, 'text': {'type': 'string'}},
    'required': ['reply', 'text'], 'additionalProperties': False,
}
FAST_REPLY_SCHEMA = {
    'type': 'object',
    'properties': {**REPLY_SCHEMA['properties'], 'needs_high': {'type': 'boolean'},
                   'needs_web': {'type': 'boolean'},
                   'sticker_code': {'type': 'string'},
                   'research_message_ids': {'type': 'array', 'items': {'type': 'integer'}}},
    'required': ['reply', 'text', 'needs_high', 'needs_web', 'research_message_ids', 'sticker_code'], 'additionalProperties': False,
}
RESEARCH_REPLY_SCHEMA = {
    'type': 'object', 'properties': {**REPLY_SCHEMA['properties'],
        'sources': {'type': 'array', 'items': {'type': 'object',
            'properties': {'title': {'type': 'string'}, 'url': {'type': 'string'}},
            'required': ['title', 'url'], 'additionalProperties': False}}},
    'required': ['reply', 'text', 'sources'], 'additionalProperties': False,
}
MEMORY_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string'},
        'members': {'type': 'array', 'items': {
            'type': 'object', 'properties': {
                'member_id': {'type': 'string'},
                'observations': {'type': 'array', 'items': {
                    'type': 'object', 'properties': {
                        'text': {'type': 'string'},
                        'evidence_ids': {'type': 'array', 'items': {'type': 'integer'}},
                    }, 'required': ['text', 'evidence_ids'], 'additionalProperties': False,
                }},
            }, 'required': ['member_id', 'observations'], 'additionalProperties': False,
        }},
    }, 'required': ['summary', 'members'], 'additionalProperties': False,
}


class GenerationSuperseded(Exception):
    """A newer @ needs the ordinary generation slot."""


class AgySession:
    def __init__(self, schema=REPLY_SCHEMA, label='reply', timeout=120, agent='wechat-dafeiyu', model=MODEL):
        self.schema = schema
        self.label = label
        self.timeout = timeout
        self.agent = agent
        self.model = model
        self.proc = None
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.last_result = {}
        self.init = {}

    def _start(self):
        exe = find_agy()
        if not exe:
            raise RuntimeError('未找到 AGY CLI，请先安装并登录 Antigravity CLI。')
        runtime = ROOT / '.runtime'
        runtime.mkdir(exist_ok=True)
        self.events = queue.Queue()
        error_log = open(runtime / f'agy-{self.label}.log', 'a', encoding='utf-8')
        try:
            self.proc = subprocess.Popen([
                str(exe), '--agent', self.agent, '--model', self.model,
                '--input-format', 'stream-json', '--output-format', 'stream-json',
                '--disable-slash-commands',
            ], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=error_log, text=True, encoding='utf-8', bufsize=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        finally:
            error_log.close()
        proc, events = self.proc, self.events

        def read():
            try:
                for line in proc.stdout:
                    try:
                        events.put(json.loads(line))
                    except ValueError:
                        events.put({'event': 'protocol_error', 'message': line[:300]})
            finally:
                events.put({'event': 'closed'})

        threading.Thread(target=read, daemon=True).start()

    def ask(self, payload, cancel=None):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            started = time.monotonic()
            steps = []
            try:
                request = dict(payload, output_schema=self.schema,
                               output_instruction=payload.get('output_instruction',
                                   '只输出一个符合 output_schema 的 JSON 对象，不要代码围栏，不要调用工具。'))
                self.proc.stdin.write(json.dumps({
                    'event': 'user', 'message': {'content': json.dumps(request, ensure_ascii=False)},
                }, ensure_ascii=True) + '\n')
                self.proc.stdin.flush()
                while True:
                    if cancel and cancel():
                        raise GenerationSuperseded()
                    remaining = self.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError('agy 本轮调用超时')
                    try:
                        event = self.events.get(timeout=min(remaining, 0.2))
                    except queue.Empty:
                        continue
                    kind = event.get('event')
                    if kind == 'init':
                        self.init = event.get('init', {})
                    elif kind == 'step_update':
                        step = event['step_update']
                        if step.get('state') == 'DONE':
                            steps.append({'type': step.get('step_type'),
                                          'tool': step.get('tool_name'),
                                          'tool_error': (step.get('tool_info') or {}).get('error'),
                                          'seconds': step.get('duration_seconds'),
                                          'elapsed': round(time.monotonic() - started, 3)})
                    elif kind == 'result':
                        result = event['result']
                        (ROOT / '.runtime' / f'agy-{self.label}-last.json').write_text(
                            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                        if result.get('status') != 'SUCCESS':
                            raise RuntimeError(f'agy 返回状态 {result.get("status")}')
                        value = result.get('structured_output')
                        if value is None:
                            value = json.loads(result['response'])
                        self.last_result = {
                            'conversation_id': result.get('conversation_id'),
                            'num_turns': result.get('num_turns'),
                            'seconds': round(time.monotonic() - started, 3),
                            'usage': result.get('usage'),
                            'steps': steps,
                        }
                        return value
                    elif kind in ('closed', 'protocol_error'):
                        raise RuntimeError(f'agy 会话中断，查看 .runtime/agy-{self.label}.log')
            except (Exception, KeyboardInterrupt):
                self.close()
                raise

    def close(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == '__main__':
    session = AgySession(label='check')
    try:
        persona = (ROOT / 'PERSONA.md').read_text(encoding='utf-8')
        checks = []
        for content, required in [('大肥鱼，出来打个招呼😀', True), ('收到', False)]:
            reply = session.ask({'task': 'reply', 'persona': persona,
                                 'must_reply': required, 'new_message': content,
                                 'output': '只输出一个 JSON 对象，字段 reply 为布尔值，text 为回复文字；不需要回复则 reply=false,text=""。'})
            checks.append({'input': content, 'result': reply, **session.last_result})
            print(json.dumps(checks[-1], ensure_ascii=True), flush=True)
        output = {'model': MODEL, 'init': session.init, 'checks': checks}
        (ROOT / '.runtime' / 'agy-check.json').write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(output, ensure_ascii=True), flush=True)
    finally:
        session.close()
