"""Keep transport XML and media keys out of model prompts."""
import xml.etree.ElementTree as ET


def for_model(message, stickers=None):
    item = dict(message)
    if item.get('kind') != '文本':
        text = f'[{item.get("kind", "媒体消息")}]'
        if item.get('kind') == '动画表情' and stickers is not None:
            text = stickers.describe(item)
        raw = item.get('content', '')
        if item.get('kind') == '文件/链接/卡片' and raw.lstrip().startswith('<'):
            try:
                title = ET.fromstring(raw).findtext('.//appmsg/title')
                if title:
                    text += ' ' + title[:600]
            except ET.ParseError:
                pass
        item['content'] = text
    return item
