#!/usr/bin/env python3
"""Small source/fixture audit; no Java, Android SDK, network or running backend required."""
from pathlib import Path
import argparse
import json
import re
import struct
import subprocess
import tempfile
import textwrap
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / 'android'
SWIFT = ROOT / 'frontend/Packages/AssistantCore'
FIXTURES = ANDROID / 'core/src/test/resources/fixtures'


def swift_literals(source):
    pattern = r'static let (\w+) = (?:"""(.*?)"""|#"(.*?)"#|"((?:\\.|[^"\\])*)")'
    definitions = {m[0]: (m[1] or m[2] or m[3], bool(m[2])) for m in re.findall(pattern, source, re.S)}
    result = {}

    def read(name):
        if name in result:
            return result[name]
        raw, literal = definitions[name]
        refs = []
        if not literal:
            def ref(m):
                refs.append(m[1])
                return f'__SWIFT_REF_{len(refs) - 1}__'
            raw = re.sub(r'\\\((\w+)\)', ref, raw)
            raw = re.sub(r'\\(["\\nrt])', lambda m: {'"': '"', '\\': '\\', 'n': '\n', 'r': '\r', 't': '\t'}[m[1]], raw)
        value = textwrap.dedent(raw).strip()
        for i, ref_name in enumerate(refs):
            value = value.replace(f'__SWIFT_REF_{i}__', read(ref_name))
        result[name] = value
        return value
    return {name: json.loads(read(name)) for name in definitions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--regenerate', action='store_true', help='Export mock JSON with the installed Swift compiler; also refresh fixtures.')
    args = parser.parse_args()
    expected = swift_literals((SWIFT / 'Tests/AssistantCoreTests/Fixtures.swift').read_text())
    knowledge = swift_literals((SWIFT / 'Tests/AssistantCoreTests/KnowledgeTests.swift').read_text())
    expected.update({f'knowledge-{name}': data for name, data in knowledge.items()})
    if args.regenerate:
        with tempfile.TemporaryDirectory(prefix='aemeath-android-fixtures-') as temp:
            binary = Path(temp) / 'export'
            subprocess.run(['swiftc', '-module-cache-path', str(Path(temp) / 'modules'), '-parse-as-library',
                            *map(str, sorted((SWIFT / 'Sources/AssistantCore').glob('*.swift'))),
                            str(SWIFT / 'Tests/AssistantCoreTests/Fixtures.swift'),
                            str(ANDROID / 'scripts/ExportFixtures.swift'), '-o', str(binary)], check=True)
            subprocess.run([str(binary), str(ANDROID / 'core/src')], check=True)
        for name, data in expected.items():
            (FIXTURES / f'{name}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    for name, data in expected.items():
        assert json.loads((FIXTURES / f'{name}.json').read_text()) == data, f'Swift fixture drift: {name}'
    def sources(pattern):
        return [p for p in ANDROID.rglob(pattern) if not {'build', '.gradle', '.kotlin'}.intersection(p.relative_to(ANDROID).parts)]
    all_json = sources('*.json')
    for path in all_json:
        json.loads(path.read_text())
    xml = sources('*.xml')
    for path in xml:
        ET.parse(path)
    stickers = ROOT / 'frontend/Design/stickers'
    def dimensions(path):
        return struct.unpack('>II', path.read_bytes()[16:24])
    layers = 0
    for directory in stickers.iterdir():
        if directory.is_dir():
            required = [directory / f'{name}.png' for name in ('base', 'eyes_open', 'eyes_closed')]
            if not any(p.exists() for p in required):
                continue
            assert all(p.exists() for p in required), f'Incomplete sticker layers: {directory.name}'
            assert len({dimensions(p) for p in required + [stickers / f'{directory.name}.png']}) == 1
            layers += 1
    for path in (ANDROID / 'core/src').rglob('*.kt'):
        assert not re.search(r'^import (android\.|androidx\.)', path.read_text(), re.M), path
    assert not sources('*.jar'), 'No binary Gradle wrapper belongs in this worktree'
    tests = sum(len(re.findall(r'@Test\b', p.read_text())) for p in sources('*.kt'))
    print(f'OK: {len(expected)} Swift fixtures match; {len(all_json)} JSON and {len(xml)} XML valid; {layers} layered stickers; {tests} JVM tests declared.')
    print('This is a source audit, not a Kotlin compilation or a JVM test run.')


if __name__ == '__main__':
    main()
