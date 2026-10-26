"""Check real Low-model sticker selection without sending a WeChat message."""
import json,time
from types import SimpleNamespace
from agy_client import AgySession,CHAT_MODEL,FAST_REPLY_SCHEMA,ROOT
from stickers import StickerLibrary
from chat_prompt import reply_payload

library=StickerLibrary(ROOT/'data'/'stickers')
message={'id':1,'name':'测试群友','member_id':'sample','created':time.time(),'is_at':True,
         'is_self':False,'kind':'文本','content':'用一个匹配这个情绪的表情回复：别给大肥鱼上压力了，鱼都快委屈哭了。'}
archive=SimpleNamespace(recent=lambda n:[message],profiles=lambda ids:[])
payload,_=reply_payload(archive,[message],(ROOT/'PERSONA.md').read_text(encoding='utf-8'),library)
model=AgySession(model=CHAT_MODEL,schema=FAST_REPLY_SCHEMA,label='sticker-choice-check')
try:
    result=model.ask(payload)
    selected=library.select(result.get('sticker_code'),payload['sticker_choices'])
    record={'reply':result,'selected_meaning':selected['meaning'],'seconds':model.last_result['seconds']}
    (ROOT/'.runtime'/'sticker-selection-check.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))
finally:
    model.close()
