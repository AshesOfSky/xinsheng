"""Add redistributable instructions and dependency license notices only."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
from app_paths import VERSION

root = Path(__file__).resolve().parent
package = root / 'dist/DaFeiYu'
for name in ('DESKTOP.md','RELEASE_NOTES.md','Install.cmd'):
    shutil.copy2(root/name,package/name)
(package/'install.ps1').write_text((root/'install.ps1').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
notices = package/'third-party'
notices.mkdir(exist_ok=True)
for distribution in distributions():
    for item in distribution.files or []:
        if 'license' not in Path(item).name.lower() and Path(item).name.lower() not in ('copying','notice'):
            continue
        source = Path(distribution.locate_file(item))
        if source.is_file():
            target = notices/distribution.metadata['Name']
            target.mkdir(exist_ok=True)
            shutil.copy2(source,target/Path(item).name)
(package/'VERSION.txt').write_text(VERSION,encoding='ascii')
print(f'Prepared {package.name} {VERSION}; no local account or chat data included.')
