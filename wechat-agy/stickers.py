"""Preserve original stickers received in the configured group."""
import json
from pathlib import Path
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

from PIL import Image


def sticker_node(message):
    if message.get('kind') != '动画表情':
        return None
    try:
        return ET.fromstring(message.get('content', '')).find('.//emoji')
    except ET.ParseError:
        return None


def sticker_id(message):
    node = sticker_node(message)
    value = node.get('md5', '').lower() if node is not None else ''
    return value if re.fullmatch(r'[0-9a-f]{32}', value) else ''


class StickerLibrary:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.directory / 'catalog.json'
        self.catalog = json.loads(self.catalog_path.read_text(encoding='utf-8')) if self.catalog_path.exists() else {}
        self.lock = threading.RLock()
        self._assign_codes()

    def _assign_codes(self):
        number = max((int(e['code'][1:]) for e in self.catalog.values() if e.get('code')), default=0) + 1
        for entry in self.catalog.values():
            if not entry.get('code'):
                entry['code'] = f'S{number:03d}'
                number += 1

    def save(self):
        with self.lock:
            self._assign_codes()
            temporary = self.catalog_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.catalog, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.catalog_path)

    def update(self, identity, **values):
        with self.lock:
            self.catalog[identity].update(values)
            self.save()

    def entries(self):
        with self.lock:
            return [dict(entry) for entry in self.catalog.values()]

    def lookup(self, code):
        with self.lock:
            return next((dict(e) for e in self.catalog.values() if e['code'] == code), None)

    def select(self, code, offered):
        if code not in {e['code'] for e in offered}:
            raise ValueError('模型选择了未提供的表情代码')
        entry = self.lookup(code)
        if not entry or entry.get('understanding') != 'clear' or not entry.get('suitable'):
            raise ValueError('表情尚未理解或不适合自动使用')
        return entry

    def describe(self, message):
        with self.lock:
            entry = self.catalog.get(sticker_id(message))
            if entry and entry.get('understanding') == 'clear':
                return f'[表情 {entry["code"]}：{entry["description"]}；常见含义：{entry["meaning"]}]'
        return '[动画表情，尚未看清含义]'

    def choices(self, limit=12):
        entries = self.entries()
        used = sorted((e for e in entries if e.get('last_used')), key=lambda e:e['last_used'], reverse=True)
        if used and time.time() - used[0]['last_used'] < 30:
            return []
        recent_codes = {e['code'] for e in used[:2]}
        ready = [e for e in entries if e.get('understanding') == 'clear' and e.get('suitable') and e['code'] not in recent_codes]
        ready.sort(key=lambda e:(e.get('last_used', 0), -e.get('source_message_id', 0)))
        return [{'code':e['code'],'meaning':e['meaning'][:90],'visible_text':e.get('visible_text','')[:60],
                 'tags':e['tags'],'avoid_when':[t[:50] for t in e.get('avoid_when',[])[:1]]}
                for e in ready[:limit]]

    def export_index(self):
        import html
        cards = []
        for e in self.entries():
            esc = lambda value: html.escape(str(value))
            cards.append('<article><h2>'+esc(e['code'])+'</h2><img src="'+esc(Path(e['path']).name)+
                '"><p>'+esc(e.get('description','待识别'))+'</p><p><b>含义：</b>'+esc(e.get('meaning','待识别'))+
                '</p><p><b>适合：</b>'+esc('；'.join(e.get('use_when',[])))+'</p><p><b>避免：</b>'+
                esc('；'.join(e.get('avoid_when',[])))+'</p><p>'+esc(e.get('classified_by','待识别'))+' · '+
                esc(e.get('understanding','pending'))+'</p><code>'+esc(e['id'])+'</code></article>')
        page = '<!doctype html><meta charset="utf-8"><title>大肥鱼表情字典</title><style>body{font:16px system-ui;background:#f2f6f8;margin:30px;color:#17364a}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px}article{background:white;padding:20px;border-radius:16px}img{width:100%;height:220px;object-fit:contain}code{font-size:11px}h2{margin:0 0 12px}</style><h1>大肥鱼表情字典</h1><p>代码对应原图；动图保留动画。含义来自识图，具体使用仍须结合群聊上下文。</p><main>'+''.join(cards)+'</main>'
        (self.directory/'index.html').write_text(page,encoding='utf-8')

    def collect(self, message):
        node = sticker_node(message)
        if node is None:
            return None
        identity = sticker_id(message)  # Existing WeChat identity, not a new digest.
        if not identity:
            return None
        with self.lock:
            if identity in self.catalog:
                entry = self.catalog[identity]
                sources = entry.setdefault('source_message_ids',[entry['source_message_id']])
                if message['id'] not in sources:
                    sources.append(message['id'])
                    self.save()
                return dict(entry)
        url = node.get('cdnurl')
        if not url:
            return None
        with urllib.request.urlopen(url, timeout=20) as response:
            raw = response.read()
        import io
        with Image.open(io.BytesIO(raw)) as img:
            ext = {'GIF': '.gif', 'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(img.format)
            if not ext:
                return None
            frames = getattr(img, 'n_frames', 1)
            size = img.size
        path = self.directory / (identity + ext)
        path.write_bytes(raw)
        entry = {'id': identity, 'path': str(path.resolve()), 'source_message_id': message['id'],
                 'source_message_ids':[message['id']],
                 'frames': frames, 'size': size, 'description': '',
                 'tags': [], 'added_to_wechat': False}
        with self.lock:
            self.catalog[identity] = entry
            self.save()
            return dict(entry)


if __name__ == '__main__':
    import sqlite3
    root = Path(__file__).resolve().parent
    db = sqlite3.connect(root / 'data' / 'archive.sqlite')
    db.row_factory = sqlite3.Row
    library = StickerLibrary(root / 'data' / 'stickers')
    rows = db.execute("SELECT * FROM messages WHERE kind='动画表情' AND is_self=0 ORDER BY id DESC").fetchall()
    db.close()  # Do not hold a read transaction while downloading media.
    for row in rows:
        try:
            entry = library.collect(dict(row))
            if entry:
                print(json.dumps({k:entry[k] for k in ('id','frames','size','source_message_id')},ensure_ascii=True), flush=True)
        except Exception as exc:
            print(json.dumps({'source_message_id':row['id'],'error_type':type(exc).__name__},ensure_ascii=True),flush=True)
