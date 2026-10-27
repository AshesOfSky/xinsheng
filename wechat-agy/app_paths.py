"""Bundled resources are read-only; the selected workspace keeps local data."""
import os
from pathlib import Path
import shutil
import sys

VERSION = '0.2.7'
BUNDLE = Path(__file__).resolve().parent


def workspace_path():
    selected = os.environ.get('DAFEIYU_HOME')
    if selected:
        return Path(selected).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        return Path(os.environ['LOCALAPPDATA']) / 'DaFeiYu'
    return BUNDLE


ROOT = workspace_path()


def initialize_workspace(root=ROOT):
    root = Path(root)
    for folder in ('data', '.runtime', '.agents/agents'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    resources = [Path('PERSONA.md')]
    resources += [p.relative_to(BUNDLE) for p in (BUNDLE / '.agents/agents').glob('*.md')]
    for relative in resources:
        destination = root / relative
        if not destination.exists():
            shutil.copy2(BUNDLE / relative, destination)
    return root


def find_agy():
    installed = Path(os.environ.get('LOCALAPPDATA', '')) / 'agy/bin/agy.exe'
    return str(installed) if installed.is_file() else shutil.which('agy')
