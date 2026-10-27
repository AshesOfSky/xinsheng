import json
import unittest

from political_guard import PoliticalGuard


def message(mid, text, *, at=False, bot=False):
    return {'id': mid, 'name': '群友', 'text': text, 'at_bot': at, 'bot': bot}


class Session:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.closed = False

    def ask(self, payload):
        self.calls.append(payload)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        self.closed = True


class PoliticalGuardTests(unittest.TestCase):
    def guard(self, decisions=(), draft='allow'):
        session = Session({'decisions': list(decisions), 'draft_decision': draft})
        return PoliticalGuard(model_session=session), session

    def test_direct_political_mention_is_silent_even_when_asked(self):
        guard, session = self.guard()
        blocked, block_draft = guard.filter([message(1, '@大肥鱼 说说政治人物', at=True)], [])
        self.assertEqual(blocked, {1})
        self.assertFalse(block_draft)
        self.assertEqual(session.calls, [])

    def test_explicit_generic_politics_is_blocked_without_network(self):
        guard, session = self.guard()
        blocked, _ = guard.filter([message(1, '@大肥鱼 你怎么看政治', at=True)], [])
        self.assertEqual(blocked, {1})
        self.assertEqual(session.calls, [])

    def test_mixed_batch_keeps_unrelated_financial_chat(self):
        guard, session = self.guard([{'id':1, 'decision':'allow'}, {'id':2, 'decision':'allow'}])
        batch = [message(1, '聊聊政府外交'), message(2, '基金今天涨了')]
        blocked, block_draft = guard.filter(batch, [])
        self.assertEqual(blocked, {1})
        self.assertFalse(block_draft)
        self.assertEqual(len(session.calls), 1)

    def test_contextual_political_followup_and_uncertain_are_silent(self):
        guard, session = self.guard([{'id':2, 'decision':'block'}, {'id':3, 'decision':'uncertain'}])
        context = [message(1, '有人在讨论政治争议')]
        blocked, _ = guard.filter([message(2, '刚才那件事到底怎么回事？'), message(3, '他说了什么？')], context)
        self.assertEqual(blocked, {2, 3})
        self.assertEqual(session.calls[0]['recent_context'][0]['text'], context[0]['text'])

    def test_draft_is_checked_independently_of_neutral_question(self):
        guard, session = self.guard([{'id':1, 'decision':'allow'}], draft='allow')
        blocked, draft_block = guard.filter([message(1, '这位球员到底说了什么？')], [],
                                             draft='涉及毛主席的争议，来源：示例报道')
        self.assertEqual(blocked, set())
        self.assertTrue(draft_block)  # explicit guard overrides a mistaken model allowance
        self.assertEqual(len(session.calls), 1)

    def test_draft_uncertainty_is_silent_and_no_draft_needs_no_check(self):
        guard, session = self.guard([], draft='uncertain')
        self.assertEqual(guard.filter([], [], '一则含糊的报道'), (set(), True))
        self.assertEqual(guard.filter([], [], ''), (set(), False))
        self.assertEqual(len(session.calls), 1)

    def test_bad_or_incomplete_classifier_results_raise(self):
        for result in ({'decisions': [], 'draft_decision':'allow'},
                       {'decisions':[{'id':1,'decision':'allow'}, {'id':1,'decision':'allow'}], 'draft_decision':'allow'},
                       {'decisions':[{'id':2,'decision':'allow'}], 'draft_decision':'allow'},
                       {'decisions':[{'id':1,'decision':'allow'}], 'draft_decision':'maybe'},
                       RuntimeError('classifier unavailable')):
            with self.subTest(result=result):
                guard = PoliticalGuard(model_session=Session(result))
                with self.assertRaises((ValueError, RuntimeError)):
                    guard.filter([message(1, '今天火锅怎样？')], [])

    def test_media_transport_xml_is_never_put_in_classifier_prompt(self):
        guard, session = self.guard([{'id':1, 'decision':'uncertain'}, {'id':3, 'decision':'allow'}])
        guard.filter([message(1, '<msg><img aeskey="private-key"/></msg>'), message(3, '晚饭吃什么？')],
                     [message(2, '<?xml version="1.0"?><msg>private-url</msg>')])
        encoded = json.dumps(session.calls[0], ensure_ascii=False)
        self.assertNotIn('private-key', encoded)
        self.assertNotIn('private-url', encoded)
        self.assertIn('媒体内容未读取', encoded)

    def test_unread_media_and_dependent_question_are_silent(self):
        guard, session = self.guard([{'id':1, 'decision':'allow'}, {'id':2, 'decision':'allow'},
                                     {'id':3, 'decision':'allow'}])
        batch = [message(1, '[图片，内容尚未读取]'), message(2, '图里说了什么？'),
                 message(3, '晚饭吃火锅吗？')]
        blocked, _ = guard.filter(batch, [])
        self.assertEqual(blocked, {1, 2})
        self.assertEqual(len(session.calls), 1)

    def test_closing_guard_closes_its_own_session(self):
        guard, session = self.guard()
        guard.close()
        self.assertTrue(session.closed)


if __name__ == '__main__':
    unittest.main()
