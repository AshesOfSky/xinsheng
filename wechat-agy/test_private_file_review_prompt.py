import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from private_file_worker import codex_runner


class FileReviewPromptTests(unittest.TestCase):
    def test_review_request_instructs_evidence_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            workdir = Path(folder)
            (workdir / 'output').mkdir()
            (workdir / 'output' / 'result.xlsx').touch()
            with patch('private_file_worker.shutil.which', return_value='codex'), \
                 patch('private_file_worker.subprocess.run',
                       return_value=SimpleNamespace(returncode=0, stdout='', stderr='')) as run:
                codex_runner(workdir, '检查一下毛利表', '毛利表.xlsx')
            prompt = run.call_args.kwargs['input']
            for phrase in ('检查一下毛利表', '逐项实际核查', '核查范围', '具体发现',
                           '单元格或段落', '判断依据', '无法确认', '局限',
                           '不得修改输入文件', '不得把原样副本称为已完成检查',
                           '保留所有原有工作表名称、内容、公式和格式', '核查结果（2）'):
                self.assertIn(phrase, prompt)


if __name__ == '__main__':
    unittest.main()
