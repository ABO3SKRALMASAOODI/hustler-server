"""Repair failed encoded picture pairings without changing the spoken film.

Encoded rejections outrank a source preview. Preserve them across resumes so a
cached preview or a new label cannot silently restore the same failed pairing.
"""
from pathlib import Path
import copy
import shutil


def review_shots(shots,brand_check,programme,words):
    """Declare verified branding separately from historical source claims.

    Samples and matching criteria are unchanged. The mandatory card's dissolve
    must remain visible, with its actual timing relative to the final words.
    """
    import cinema as C
    if not brand_check.get('endcard_ok'):return shots
    fps=round(programme['fps']);end=programme['frames']/fps if programme.get('frames') else programme['dur']
    join=C.brand._renderer().outro_join_frames(fps,end)/fps
    wd=C.read(words)
    result=copy.deepcopy(shots)
    for row in result:
        if row['f'][1]/30<=end-join:continue
        row['clip']['source']['verified_output_presentation']=dict(
            kind='Required Valmera end-card transition, independently verified against the product brand asset',
            interval_s=[end-join,end],last_spoken_word_end_s=max(w['end'] for w in wd['words'])+C.F.LEAD_F/30,
            role='Output branding; not part of the historical source or a historical claim')
    return result


def original(row):
    c=row['clip'];a=c['time_range'][0]
    return dict(url=c['source']['url'],image=c.get('content_hash') if c.get('kind')=='image' else None,
                span=[a+t for t in row['src']])


def observations(shots,report):
    rows=[]
    for i,row in enumerate(shots):
        verdict=report['evidence'].get(str(i),report['evidence'].get(i,{}))
        if verdict.get('matches') is True:continue
        rows.append(dict(f=row['f'],source=original(row),words=row['say'],reason=verdict.get('reason','Missing encoded verdict')))
    return rows


def blocked(row,rejections):
    source=original(row)
    for r in rejections:
        if min(row['f'][1],r['f'][1])<=max(row['f'][0],r['f'][0]):continue
        old=r['source']
        same_image=source['image'] and source['image']==old['image']
        same_frames=not source['image'] and not old['image'] and source['url']==old['url'] and min(source['span'][1],old['span'][1])-max(source['span'][0],old['span'][0])>.04
        if same_image or same_frames:return r['reason']
    return None


def enforce(ctx,shots,report):
    import cinema as C
    path=Path(ctx.supply)/'encoded-rejections.json'
    if not path.exists():return report
    result=copy.deepcopy(report);rejections=C.read(path)
    for i,row in enumerate(shots):
        reason=blocked(row,rejections)
        if reason:
            key=str(i) if str(i) in result['evidence'] else i
            result['evidence'][key]=dict(i=i,matches=False,real_moving_footage=False,
                reason='Encoded-film rejection: '+reason,encoded_authority=True)
    result['rejected']=[int(i) for i,r in result['evidence'].items() if r.get('matches') is not True]
    result['ok']=not result['rejected'] and not result.get('missing')
    return result


def begin(ctx):
    import cinema as C
    run=Path(ctx.run_dir);root=run/'encoded-repairs'
    identity=C.digest(run/'final.mp4')
    prior=list(root.glob('*/state.json'))
    if len(prior)>=2:raise RuntimeError('encoded picture repair exhausted its two-film budget; inspect the retained verdicts')
    folder=root/identity[:16]
    if folder.exists():raise RuntimeError('encoded repair produced the same failed film; inspect its retained verdicts')
    folder.mkdir(parents=True,exist_ok=True)
    shots=C.read(Path(ctx.supply)/'shots.json');report=C.read(Path(ctx.supply)/'picture_check.json')
    measured=C.read(run/'measured.json')
    # A later use of a visually duplicated take must also be replaced, even
    # when its words were appropriate and its numerical source range is new.
    for duplicate in measured['longform']['visual_duplicates'].get('verdicts',[]):
        if duplicate.get('repeated') is False:continue
        i=max(duplicate['pair']);key=str(i) if str(i) in report['evidence'] else i
        report['evidence'][key]=dict(report['evidence'].get(key,{}),i=i,matches=False,
            reason='Repeated or unresolved picture: '+duplicate['why'])
        if i not in report['rejected']:report['rejected'].append(i)
    report['ok']=not report.get('rejected') and not report.get('missing')
    if not report.get('rejected') and not report.get('missing'):raise ValueError('no encoded picture failures to repair')
    if any(shots[i].get('real_voice') for i in report.get('rejected',[])):
        raise RuntimeError('a witness picture failed; repair its attribution before resuming')
    C.save(folder/'shots.json',shots);C.save(folder/'picture_check.json',report)
    for i in report['rejected']:
        frame=Path(ctx.supply)/'picture-evidence'/f'shot-{i:04d}.jpg'
        if frame.exists():shutil.copy2(frame,folder/frame.name)
    C.save(folder/'measured.json',measured)
    C.save(folder/'footage-before.json',C.read(Path(ctx.supply)/'footage/footage.json'))
    C.save(folder/'state.json',dict(phase='prepare',film_sha256=identity))
    C.save(run/'encoded-repair.json',dict(folder=str(folder)))
    return folder


def repair(ctx,folder,rd,sc):
    import cinema as C
    from timeline_repair import retain,repair as repair_timeline,smooth
    folder=Path(folder);state=C.read(folder/'state.json')
    run=Path(ctx.run_dir);script=C.read(run/'script.json')
    fp=Path(ctx.supply)/'footage/footage.json';pp=Path(ctx.supply)/'pack.json'
    pack=C.read(pp);words=pack['pack']['words']
    if state['phase']=='prepare':
        shots=C.read(folder/'shots.json');report=C.read(folder/'picture_check.json')
        ft=C.read(folder/'footage-before.json')
        retain(ft,shots,C.F.Clock(words,script['script']),report)
        # All surviving intervals are now pinned. Old tentative assignments
        # must not bypass the rejection list when the gaps are allocated again.
        ft['editorial']['assignments']=[]
        rejects=Path(ctx.supply)/'encoded-rejections.json'
        rows=C.read(rejects) if rejects.exists() else []
        for row in observations(shots,report):
            if row not in rows:rows.append(row)
        C.save(rejects,rows);C.save(fp,ft)
        state['phase']='pictures';C.save(folder/'state.json',state)
    if state['phase']=='pictures':
        ft=C.read(fp)
        ctx.log('repairing encoded picture mismatches on the existing narration clock')
        shots,_=repair_timeline(ctx,script,ft,words)
        if C.source_review(ctx,shots)['moving_share']<.55:
            from moving_picture import improve
            improve(ctx,script,ft,words,shots)
        smooth(ctx,script,ft,words)
        C.save(fp,ft);pack['pack']['footage']=ft;C.save(pp,pack)
        state['phase']='render';C.save(folder/'state.json',state)
    if state['phase']=='render':
        sc.clear();sc.update(C.score(ctx,script));C.save(run/'score.json',sc)
        rd.clear();rd.update(C.render(ctx,script,sc));C.save(run/'render.json',rd)
        state['phase']='checked-next';C.save(folder/'state.json',state)


def retain_evidence(ctx,evidence):
    for folder in (Path(ctx.run_dir)/'encoded-repairs').glob('*'):
        if not folder.is_dir():continue
        target=Path(evidence)/'encoded-repairs'/folder.name;target.mkdir(parents=True,exist_ok=True)
        for source in [folder/n for n in ('state.json','shots.json','picture_check.json','measured.json')]+list(folder.glob('shot-*.jpg')):
            if source.exists():shutil.copy2(source,target/source.name)


def check(ctx,rd,sc):
    import cinema as C
    pointer=Path(ctx.run_dir)/'encoded-repair.json'
    if pointer.exists():repair(ctx,C.read(pointer)['folder'],rd,sc)
    else:
        measured=Path(ctx.run_dir)/'measured.json';stamp=Path(ctx.supply)/'picture-input.json'
        final=Path(ctx.run_dir)/'final.mp4'
        if measured.exists() and stamp.exists() and final.exists() and measured.stat().st_mtime>=stamp.stat().st_mtime:
            prior=C.read(measured);inputs=C.read(stamp)
            if (not prior['targets']['pictures'] or not prior['targets']['unique']) and inputs['film']==C.digest(final) and inputs['shots']==C.digest(Path(ctx.supply)/'shots.json'):
                # Reuse a completed failed measurement only for diagnosis,
                # never as the verdict on the repaired film.
                repair(ctx,begin(ctx),rd,sc)
    while True:
        try:return C.check(ctx,rd,sc)
        except RuntimeError as error:
            if not str(error).startswith('delivered-file gates failed:'):raise
            measured=C.read(Path(ctx.run_dir)/'measured.json')
            if measured['targets']['pictures'] and measured['targets']['unique']:raise
            folder=begin(ctx)
            repair(ctx,folder,rd,sc)
