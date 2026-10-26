import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

from stickers import StickerLibrary
from classify_stickers import classify
from context_text import for_model
from native_stickers import media_matches


def understood(state='clear'):
    return dict(description='角色挠头',visible_text='',meaning='困惑',tags=['疑惑'],
                use_when=['不理解'],avoid_when=['严肃道歉'],understanding=state,
                uncertainty='动作不清' if state=='uncertain' else '',suitable=True)


class StickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.library=StickerLibrary(self.tmp.name)
        self.identity='a'*32
        self.message={'id':1,'kind':'动画表情','content':
            f'<msg><emoji md5="{self.identity}" cdnurl="https://example.com/test.gif" aeskey="private" /></msg>'}

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self):
        buffer=io.BytesIO()
        Image.new('RGB',(8,8),'blue').save(buffer,format='PNG')
        with patch('stickers.urllib.request.urlopen',return_value=io.BytesIO(buffer.getvalue())):
            return self.library.collect(self.message)

    def test_duplicate_keeps_code_and_original_and_records_new_source(self):
        first=self.collect()
        with patch('stickers.urllib.request.urlopen',side_effect=AssertionError('重复图不应下载')):
            second=self.library.collect(dict(self.message,id=2))
        self.assertEqual(first['code'],second['code'])
        self.assertEqual(second['source_message_ids'],[1,2])
        self.assertTrue(Path(first['path']).exists())
        self.assertEqual(StickerLibrary(self.tmp.name).lookup(first['code'])['id'],self.identity)

    def test_uncertain_or_unoffered_image_cannot_be_selected(self):
        entry=self.collect()
        self.library.update(entry['id'],**understood('uncertain'))
        self.assertEqual(self.library.choices(),[])
        with self.assertRaises(ValueError): self.library.select(entry['code'],[])
        self.library.update(entry['id'],**understood())
        self.assertEqual(self.library.select(entry['code'],self.library.choices())['path'],entry['path'])
        with self.assertRaises(ValueError): self.library.select('S999',self.library.choices())

    def test_recognized_image_context_keeps_transport_keys_out(self):
        entry=self.collect(); self.library.update(entry['id'],**understood())
        rendered=for_model(self.message,self.library)['content']
        self.assertIn('困惑',rendered)
        self.assertIn(entry['code'],rendered)
        self.assertNotIn('https:',rendered)
        self.assertNotIn('private',rendered)

    def test_uncertain_agy_uses_codex_vision(self):
        entry=self.collect(); calls=[]
        session=SimpleNamespace(model='agy',last_result={'steps':[{'tool':'view_file'}]},
            ask=lambda *a,**k:understood('uncertain'))
        def codex(path,cancel=None):
            calls.append(path)
            return understood()
        result=classify(entry,session,codex=codex)
        self.assertEqual(result['classified_by'],'codex')
        self.assertEqual(result['agy_first_pass']['understanding'],'uncertain')
        self.assertEqual(len(calls),1)

    def test_agy_must_actually_view_the_image(self):
        entry=self.collect(); calls=[]
        session=SimpleNamespace(model='agy',last_result={'steps':[]},ask=lambda *a,**k:understood())
        result=classify(entry,session,codex=lambda *a,**k:(calls.append(a) or understood()))
        self.assertEqual(result['classified_by'],'codex')
        self.assertEqual(len(calls),1)

    def test_media_confirmation_checks_the_real_sticker_identity(self):
        pending={'after_id':5,'sticker_id':self.identity,'image_format':'.gif'}
        sent=dict(self.message,id=6,is_self=True)
        self.assertTrue(media_matches(sent,pending))
        self.assertFalse(media_matches(dict(sent,content=sent['content'].replace(self.identity,'b'*32)),pending))
        self.assertFalse(media_matches(dict(sent,is_self=False),pending))
        self.assertFalse(media_matches(dict(sent,kind='图片'),pending))


if __name__=='__main__': unittest.main()
