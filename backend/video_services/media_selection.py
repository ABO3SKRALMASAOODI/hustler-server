"""Choose explicitly named initial footage and keep alternate versions separate."""
import os
import re


def _name(asset):
    return str((asset.get('meta') or {}).get('filename') or
               asset.get('storage_key') or '').replace('\\', '/').rsplit('/', 1)[-1]


def _plain(value):
    return re.sub(r'[\W_]+', ' ', str(value).casefold()).strip()


def named_base(assets, brief):
    text = _plain(str(brief or '')[:30000])
    matches, commands = [], []
    for asset in assets:
        if asset.get('kind') != 'video_clip':
            continue
        name = _plain(_name(asset))
        if len(name) < 8:
            continue
        match = re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text)
        if not match:
            continue
        prefix = text[max(0, match.start() - 80):match.start()]
        verbs = list(re.finditer(r'\b(?:upload|edit|use|start with|base on)\b', prefix))
        verb = verbs[-1] if verbs else None
        if verb and re.search(r'\b(?:don t|do not|never|not)\s*$', prefix[:verb.start()]):
            continue
        matches.append((match.start(), asset))
        if verb and len(prefix[verb.end():]) <= 40:
            commands.append((match.start(), asset))
    # A brief may instruct editing A now and analyzing B after the edit. Its
    # first explicit source instruction owns the initial timeline.
    if commands:
        return min(commands, key=lambda pair: pair[0])[1]
    return matches[0][1] if len(matches) == 1 else None


def contains_alternate_versions(assets, brief=''):
    if re.search(r'\b(?:combine|concatenate|join|stack)\b.{0,35}\b(?:both|all|versions|files|clips)\b',
                 str(brief or ''), re.I):
        return False
    families, hashes = set(), set()
    for asset in assets:
        if asset.get('kind') != 'video_clip':
            continue
        sha = asset.get('sha256')
        if sha and sha in hashes:
            return True
        if sha:
            hashes.add(sha)
        stem = os.path.splitext(_name(asset))[0].casefold()
        family = re.sub(r'(?:[-_\s]+(?:final|no[-_\s]*captions|with[-_\s]*captions|'
                        r'captioned|uncaptioned|draft|v\d+))+$', '', stem)
        family = _plain(family)
        if len(family) >= 8 and family in families:
            return True
        families.add(family)
    return False
