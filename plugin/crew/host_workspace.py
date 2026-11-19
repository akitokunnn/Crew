"""Host-side folder discovery and workspace registration."""
import os
import re
import stat
import string
import subprocess
from pathlib import Path

from . import git_process

FOLDER_LIMIT = 2000


def directory_state(root):
    try:
        is_directory = stat.S_ISDIR(Path(root).stat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        is_directory = False
    except OSError as error:
        return {'status': 'error', 'error': str(error)}
    return {'status': 'ready' if is_directory else 'missing'}


def with_directory_state(projects):
    return [item if item.get('read_only') else {**item, 'directory_state': directory_state(item['root'])}
            for item in projects]


def _directory(value):
    if not isinstance(value, (str, Path)) or not value:
        raise ValueError('工作区路径必须是非空文本')
    try:
        selected = Path(value).resolve(strict=True)
    except RuntimeError as error:
        # Python 3.11 and 3.12 report symlink loops as RuntimeError.
        raise ValueError(str(error)) from error
    if not stat.S_ISDIR(selected.stat().st_mode):
        raise ValueError('工作区路径不是文件夹：' + str(selected))
    return selected


def folders(path):
    home = Path.home()
    selected = Path(path) if path else home
    if not selected.is_absolute():
        raise ValueError('文件夹路径必须是绝对路径')
    try:
        selected = _directory(selected)
        entries = []
        for child in selected.iterdir():
            if child.is_dir():
                hidden = child.name.startswith('.') or (
                    os.name == 'nt' and bool(child.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN))
                entries.append({'name': child.name, 'path': str(child),
                                'git': (child / '.git').exists(), 'hidden': hidden})
        roots = ([str(root) for drive in string.ascii_uppercase if (root := Path(drive + ':\\')).is_dir()]
                 if os.name == 'nt' else ['/'])
    except OSError as error:
        raise ValueError(str(error)) from error
    entries.sort(key=lambda entry: (entry['name'].casefold(), entry['name']))
    return {'path': str(selected), 'parent': str(selected.parent) if selected.parent != selected else None,
            'home': str(home), 'roots': roots, 'entries': entries[:FOLDER_LIMIT],
            'truncated': len(entries) > FOLDER_LIMIT}


def _git_root(selected):
    if git_process.repository_root(selected) is None:
        return selected
    result = git_process.run(['-C', str(selected), 'rev-parse', '--show-toplevel'])
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return Path(result.stdout.strip()).resolve(strict=True)


def _registered_at(projects, directory):
    root = os.path.normcase(str(directory))
    matches = [item for item in projects if os.path.normcase(os.path.abspath(item['root'])) == root]
    return next((item for item in matches if not item['removed_at']), matches[0] if matches else None)


def existing(projects, directory, new_project, register):
    if type(new_project) is not bool:
        raise ValueError('new_project 必须是布尔值')
    try:
        selected = _directory(directory)
        project = _registered_at(projects, selected)
        if project is None:
            selected = _git_root(selected)
            project = _registered_at(projects, selected)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(str(error)) from error
    if project is not None:
        return register(project['name'], project['root']) if project['removed_at'] else {
            **project, 'directory_state': {'status': 'ready'}}
    if not new_project:
        missing = [item for item in with_directory_state(project for project in projects if not project['removed_at'])
                   if item['directory_state']['status'] == 'missing']
        if missing:
            return {'relocation': {'directory': str(selected), 'projects': missing}}
    stem = selected.name
    if not stem:
        raise ValueError('字段必须是非空文本: name')
    names = {item['name'] for item in projects}
    name, number = stem, 2
    while name in names:
        name = f'{stem} {number}'
        number += 1
    return register(name, selected)


def new(parent, value, register):
    if (not isinstance(value, str) or not (name := value.strip())
            or len(name.encode('utf-16-le', errors='surrogatepass')) > 200
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith(('.', ' '))
            or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', name, re.IGNORECASE | re.ASCII)
            or name in ('.', '..')):
        raise ValueError('请输入有效的工作区名称，不能包含路径分隔符或系统保留名称')
    try:
        root = _directory(parent) / name
        root.mkdir()
    except OSError as error:
        raise ValueError(str(error)) from error
    return register(name, root)
