"""Low handles the conversation; High handles requests needing careful work."""


class ResearchUnavailable(RuntimeError):
    """A requested live check did not produce a usable, verified source."""


def researched_reply(high, payload, require_web, cancel=None):
    result = high.ask(dict(payload, require_web=require_web,
        output_instruction='按 require_web 和问题需要实际使用 search_web、read_url_content 查证。'
                           '完成后只输出一个符合 output_schema 的 JSON 对象，包含真实来源 sources。'), cancel=cancel)
    web_steps = [s for s in high.last_result.get('steps', [])
                 if s.get('tool') in ('search_web', 'read_url_content') and not s.get('tool_error')]
    raw_sources = result.get('sources') or []
    sources = [source for source in raw_sources
               if isinstance(source, dict) and isinstance(source.get('title'), str)
               and source['title'].strip() and isinstance(source.get('url'), str)
               and source['url'].startswith(('https://', 'http://'))]
    if require_web and (not web_steps or not sources):
        raise ResearchUnavailable('联网查证未取得可用的真实来源')
    if result.get('reply') and sources:
        links = [source['title'] + '：' + source['url'] for source in sources[:2]]
        if links:
            result['text'] = result['text'].rstrip() + '\n来源：\n' + '\n'.join(links)
    return result


def generate_reply(fast, high, payload, cancel=None, defer=None):
    fast_payload = dict(payload, instruction=payload['instruction'] +
        '你同时判断是否要交给 High：接梗、寒暄、简单追问、角色玩笑直接回答，needs_high=false。'
        '需要计算、推理、事实核实、技术解释、比较方案或认真建议的请求，needs_high=true、'
        'reply=true、text=""，交给 High 回答。仅被 @ 不代表需要 High。'
        '明确要求联网/搜索/查一下/看链接，或涉及当前行情、公司上市状态、现行规则、新闻、'
        '今天/最新的事实时，needs_web=true 且 needs_high=true，交给具备搜索工具的 High。'
        '其他情况 needs_web=false。不要沿用旧聊天里“没有网络”的说法；联网能力已接通。')
    fast_payload['instruction'] += (
        '仅从 new_messages 中挑选本轮需要认真处理的问题，research_message_ids 填其真实消息 id；'
        '无认真问题时填空数组。background_questions 中的问题已有 High 在后台处理，不要重复转交，'
        '也不要替它报未经查证的结论；继续回应新的日常聊天。')
    result = fast.ask(fast_payload, cancel=cancel)
    if type(result.get('needs_high')) is not bool:
        raise ValueError('快速回复缺少 needs_high 判定，未发送')
    elapsed = fast.last_result['seconds']
    if type(result.get('needs_web')) is not bool:
        raise ValueError('快速回复缺少 needs_web 判定，未发送')
    if result['needs_high'] or result['needs_web']:
        require_web = result['needs_web']
        ids = result.get('research_message_ids', [])
        targets = [m for m in payload['new_messages'] if m['id'] in ids]
        if not targets:
            raise ValueError('认真回答未指向本轮真实提问消息，未发送')
        payload = dict(payload, research_questions=targets,
                       instruction=payload['instruction'] + '本轮只回答 research_questions 中的问题。'
                       '这些是稍早前的问题，答案完成后会由程序真实 @ 提问者发送；不要自行拼 @ 昵称。')
        if defer:
            defer(payload, require_web, elapsed)
            return {'reply': False, 'text': '', 'deferred': True}, high.model, elapsed
        result = researched_reply(high, payload, require_web, cancel=cancel)
        return result, high.model, round(elapsed + high.last_result['seconds'], 3)
    return result, fast.model, elapsed
