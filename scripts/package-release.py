from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = {
    'README.md', 'LICENSE', 'package.json', 'package-lock.json',
    'pyproject.toml', 'uv.lock', 'index.html', 'vite.config.ts',
    'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json',
    'run-paperpatch.sh', 'scripts/package-release.py', 'public/favicon.svg',
}
SOURCE_DIRECTORIES = ('backend/', 'src/', '.kiro/', 'public/fonts/')


def included(path: str) -> bool:
    return path in SOURCE_FILES or path.startswith(SOURCE_DIRECTORIES)


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description='Build a clean, local PaperPatch test archive from committed HEAD.')
    parser.add_argument('--output', type=Path, help='Destination ZIP; defaults to the system temporary directory.')
    args = parser.parse_args()
    commit = git('rev-parse', 'HEAD').decode().strip()
    output = (args.output or Path(tempfile.gettempdir()) / f'paperpatch-{commit[:7]}.zip').resolve()
    if output.exists():
        parser.error(f'{output} already exists. Choose another --output path.')
    if not shutil.which('npm'):
        parser.error('npm is required to build the frontend.')

    records = git('ls-tree', '-r', '-z', commit).split(b'\0')
    sources = []
    for record in filter(None, records):
        metadata, raw_path = record.split(b'\t', 1)
        mode, kind, _ = metadata.decode().split()
        path = raw_path.decode()
        if included(path):
            if kind != 'blob' or mode not in ('100644', '100755'):
                parser.error(f'Unsupported source entry: {path}')
            sources.append((path, int(mode, 8)))
    missing = SOURCE_FILES - {path for path, _ in sources}
    if missing:
        parser.error(f'Commit these required files before packaging: {", ".join(sorted(missing))}')

    print(f'Packaging committed source {commit}. Uncommitted changes are not included.', flush=True)
    with tempfile.TemporaryDirectory(prefix='paperpatch-release-') as temporary:
        staging = Path(temporary) / 'paperpatch'
        staging.mkdir()
        for path, mode in sources:
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git('show', f'{commit}:{path}'))
            target.chmod(mode & 0o777)

        subprocess.run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=staging, check=True)
        subprocess.run(['npm', 'run', 'build'], cwd=staging, check=True)
        dependencies = json.loads((staging / 'package-lock.json').read_text())['packages']
        notices = []
        for directory, package in sorted(dependencies.items()):
            if not directory or (package.get('dev') and directory != 'node_modules/vite'):
                continue
            folder = staging / directory
            for license_file in sorted(folder.iterdir()):
                if license_file.is_file() and license_file.stem.lower() in ('license', 'licence', 'copying', 'notice'):
                    notices.append(f"{directory} ({package['version']}) — {license_file.name}\n\n"
                                   + license_file.read_text() + '\n')
        notice_path = staging / 'THIRD-PARTY-NOTICES.txt'
        notice_path.write_text('\n'.join(notices))
        artifacts = sorted(path for path in (staging / 'dist').rglob('*') if path.is_file())
        files = [*(staging / path for path, _ in sources), *artifacts, notice_path]
        manifest = {
            'commit': commit,
            'launch': './run-paperpatch.sh',
            'platforms': ['macOS', 'Linux'],
            'requires': ['uv', 'internet for first-run dependencies and OCR models'],
            'sha256': {path.relative_to(staging).as_posix(): sha256(path.read_bytes()).hexdigest()
                       for path in sorted(files)},
        }
        manifest_path = staging / 'build.json'
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        files.append(manifest_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output, 'x', compression=ZIP_DEFLATED) as archive:
            for path in sorted(files):
                name = f'paperpatch/{path.relative_to(staging).as_posix()}'
                info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100755 if path.name == 'run-paperpatch.sh' else 0o100644) << 16
                archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED)

    print(f'Created {output}\nSHA-256: {sha256(output.read_bytes()).hexdigest()}', flush=True)


if __name__ == '__main__':
    main()
