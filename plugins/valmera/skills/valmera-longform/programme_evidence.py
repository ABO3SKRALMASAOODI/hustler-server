"""Bring a retained encoded observation upstream to the next documentary assembly."""
from pathlib import Path


def corrections(ctx, script, rows, clock, report):
    from interval_evidence import panel_shots
    gaps = []
    feedback = {}
    for i in report.get('rejected', []):
        row = rows[i]
        if row.get('real_voice'):
            continue
        a, b = row['f']
        gaps.append(dict(seg=row['seg'], start=a/30, end=b/30, seconds=(b-a)/30,
                         words=row.get('context', row['say']), heard=row['say']))
        verdict = report['evidence'].get(str(i), report['evidence'].get(i))
        feedback[str(a)] = [verdict['reason']]
    return panel_shots(ctx, script, gaps, clock, feedback) if gaps else []


def reuse(ctx, script, rows, clock):
    import cinema as C
    from interval_evidence import signature
    root = Path(ctx.supply)
    receipt = root/'picture-input.json'
    previous = root/'shots.json'
    review = root/'picture_check.json'
    # Rebuilding the plan can precede final voice measurement. Read the actual
    # picture evidence here, without waiting for all the other finish checks.
    def identity(row):
        c = row['clip']
        return signature([row['f'], row['src'], c['source']['url'], c.get('content_hash'),
                          c.get('graphic_id'), c['time_range'], row['say'], row.get('context')])
    record = root/'programme-corrections.json'
    saved = C.read(record) if record.exists() else {}
    if receipt.exists() and review.exists() and previous.exists():
        stamp = C.read(receipt)
        if stamp['shots'] == C.digest(previous):
            old = C.read(previous)
            from rendered_cache import adopt
            adopt(ctx,old,C.read(root/'pack.json')['pack']['words'])
            report = C.read(review)
            # Preserve receipts, including every reason for a failed pairing.
            C.save(root/'programme-history'/stamp['film']/'review.json', report)
            current = {identity(r) for r in rows}
            pending = [i for i in report['rejected'] if identity(old[i]) in current
                       and identity(old[i]) not in saved]
            if pending:
                replacements = corrections(ctx, script, old, clock, dict(report, rejected=pending))
                original = {tuple(r['f']): r for r in old}
                for replacement in replacements:
                    saved[identity(original[tuple(replacement['f'])])] = replacement
                C.save(record, saved)
    return [saved.get(identity(row), row) for row in rows]


def assemble(ctx, script, rows, clock):
    """Try a cited replacement for source mismatches on the real word clock."""
    import cinema as C
    rows = reuse(ctx, script, rows, clock)
    review = C.source_review(ctx, rows, required=False)
    if review['ok']:
        return rows
    replacements = {tuple(row['f']):row for row in corrections(ctx,script,rows,clock,review)}
    fixed = [replacements.get(tuple(row['f']),row) for row in rows]
    C.source_review(ctx,fixed)
    return fixed
