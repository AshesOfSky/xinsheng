# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH)
datas = [(str(root/'PERSONA.md'),'.'),(str(root/'assets/fish.ico'),'assets')]
datas += [(str(p),'.agents/agents') for p in (root/'.agents/agents').glob('*.md')]
binaries, hiddenimports = [], []
for package in ('wechatauto', 'winsdk'):
    package_data, package_binary, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binary
    hiddenimports += package_imports
datas += collect_data_files('uiautomation')
a = Analysis([str(root/'desktop.py')], pathex=[str(root)], binaries=binaries,
    datas=datas, hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[],
    excludes=['pytest','IPython','matplotlib'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='DaFeiYu',
          console=False,debug=False,strip=False,upx=False,icon=str(root/'assets/fish.ico'))
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='DaFeiYu')
