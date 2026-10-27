from types import SimpleNamespace
import unittest
from reply_router import ResearchUnavailable, generate_reply, researched_reply

QUESTION = {'instruction': '', 'new_messages': [{'id': 7, 'text': '请查一下'}]}


class RouterTests(unittest.TestCase):
    def test_banter_is_not_sent_to_high(self):
        fast = SimpleNamespace(model='low', last_result={'seconds': 2},
            ask=lambda *a, **k: {'reply': True, 'text': '大肥鱼在呢', 'needs_high': False, 'needs_web': False})
        result, model, seconds = generate_reply(fast, None, {'instruction': ''})
        self.assertEqual((result['text'], model, seconds), ('大肥鱼在呢', 'low', 2))

    def test_serious_question_uses_high_instead_of_fast_draft(self):
        fast = SimpleNamespace(model='low', last_result={'seconds': 2},
            ask=lambda *a, **k: {'reply': True, 'text': '不应该发送的草稿', 'needs_high': True, 'needs_web': False, 'research_message_ids': [7]})
        high = SimpleNamespace(model='high', last_result={'seconds': 6},
            ask=lambda *a, **k: {'reply': True, 'text': '认真计算后的回答'})
        result, model, seconds = generate_reply(fast, high, QUESTION)
        self.assertEqual((result['text'], model, seconds), ('认真计算后的回答', 'high', 8))

    def test_web_request_cannot_send_an_unsearched_current_fact(self):
        fast = SimpleNamespace(model='low', last_result={'seconds': 2},
            ask=lambda *a, **k: {'reply': True, 'text': '', 'needs_high': True, 'needs_web': True, 'research_message_ids': [7]})
        high = SimpleNamespace(model='high', last_result={'seconds': 6, 'steps': []},
            ask=lambda *a, **k: {'reply': True, 'text': '没查过却声称今天已经上市',
                                'sources': [{'title':'假来源','url':'https://example.com/'}]})
        with self.assertRaises(ResearchUnavailable):
            generate_reply(fast, high, QUESTION)

    def test_successful_search_without_usable_sources_does_not_reply(self):
        for sources in ([], [{'title':'', 'url':'https://example.com/'}],
                        [{'title':'不支持的网址', 'url':'file:///private'}]):
            with self.subTest(sources=sources):
                high = SimpleNamespace(last_result={'steps':[{'tool':'search_web','tool_error':None}]},
                    ask=lambda *a, **k:{'reply':True,'text':'未经证实','sources':sources})
                with self.assertRaises(ResearchUnavailable):
                    researched_reply(high, QUESTION, require_web=True)

    def test_slow_research_can_be_queued_without_blocking_chat(self):
        jobs = []
        fast = SimpleNamespace(model='low', last_result={'seconds': 2},
            ask=lambda *a, **k: {'reply': True, 'text': '', 'needs_high': True, 'needs_web': True, 'research_message_ids': [7]})
        high = SimpleNamespace(model='high', ask=lambda *a, **k: self.fail('不应同步等待搜索'))
        result, _, _ = generate_reply(fast, high, QUESTION,
                                     defer=lambda *args: jobs.append(args))
        self.assertTrue(result['deferred'])
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0][1])


if __name__ == '__main__':
    unittest.main()
