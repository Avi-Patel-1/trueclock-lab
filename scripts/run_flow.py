#!/usr/bin/env python3
"""Run one immutable, dispatcher-approved SKY130 HD source build on a standard Linux VM."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

REPO = 'Avi-Patel-1/trueclock-lab'
REPO_ID = 1446511200
IMAGE = 'ghcr.io/librelane/librelane@sha256:4bc9042ba177f73f275b5b39e7d372a07d3f4043f548a0ba7f640097b7205051'
IMAGE_CONFIG = 'sha256:62371fe70faeb48f0d9dc142636f9e03729e85e8ad7d72b7a42b4d90a9cb3590'
PDK_VERSION = '8afc8346a57fe1ab7934ba5a6056ea8b43078e71'
LIBS = {
    'ff_n40C_1v95':'fb61d91c55a7f85b1989e8149d040b13ba5e7f5478f5bf0c3c16c5da30720139',
    'ss_100C_1v60':'9b24f0db3967ac67b4cae1f74bb480fd47922d18d0ed577e3c121739b412c361',
    'tt_025C_1v80':'8e78e14442062dba34d414fca6490b2f6b96038d4510d1438ca44fee31487135',
}
CORNERS = {f'{rc}_{pvt}' for rc in ('min','nom','max') for pvt in LIBS}
GIB = 1 << 30
STOP = datetime.fromisoformat('2026-11-05T18:13:55.762650+00:00')
HARD = datetime.fromisoformat('2026-11-05T19:13:55.762650+00:00')
OWNER = 'trueclock.run_token'
CONFIG_KEYS = {'DESIGN_NAME','PDK','STD_CELL_LIBRARY','VERILOG_FILES','CLOCK_PERIOD',
               'CLOCK_PORT','STA_CORNERS','PNR_SDC_FILE','SIGNOFF_SDC_FILE','IO_PIN_ORDER_CFG',
               'FP_CORE_UTIL','PL_TARGET_DENSITY_PCT','SYNTH_STRATEGY',
               'PDN_VOFFSET','PDN_HOFFSET','PDN_VWIDTH','PDN_HWIDTH','PDN_VPITCH','PDN_HPITCH',
               'PDN_SKIPTRIM','FP_SIZING','DIE_AREA','CORE_AREA','CLOCK_TRANSITION_CONSTRAINT',
               'CLOCK_UNCERTAINTY_CONSTRAINT','CTS_SINK_CLUSTERING_SIZE','GRT_DESIGN_REPAIR_MAX_CAP_PCT',
               'GRT_DESIGN_REPAIR_MAX_SLEW_PCT','MAX_FANOUT_CONSTRAINT','MAX_TRANSITION_CONSTRAINT',
               'OUTPUT_CAP_LOAD','RUN_KLAYOUT_DRC','RUN_POST_GRT_DESIGN_REPAIR','SYNTH_DRIVING_CELL'}


class Refusal(ValueError):
    pass


def check(ok, reason):
    if not ok:
        raise Refusal(reason)


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_text())


def exclusive_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def checked_path(root, value):
    rel = Path(value)
    check(not rel.is_absolute() and '..' not in rel.parts and rel.parts and rel == Path(rel.as_posix()),
          'path must be repository-relative')
    path = root / rel
    check(path.is_file() and path.resolve() == path and path.is_relative_to(root),
          'unresolved, nonregular or symlinked source: ' + str(rel))
    return path


def verify_ref(root, ref):
    check(isinstance(ref,dict) and re.fullmatch('[0-9a-f]{64}', ref.get('sha256','')), 'invalid SHA-256')
    path = checked_path(root, ref['path'])
    check(checksum(path) == ref['sha256'], 'SHA-256 mismatch: ' + str(path))
    return path


def check_host(root, env):
    check(env.get('GITHUB_ACTIONS') == 'true' and env.get('GITHUB_REPOSITORY') == REPO and
          env.get('GITHUB_REF') == 'refs/heads/main' and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
          and env.get('GITHUB_RUN_ATTEMPT') == '1' and env.get('RUNNER_OS') == 'Linux'
          and env.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'unapproved runner or destination')
    event = read(Path(env['GITHUB_EVENT_PATH']))
    repo = event['repository']
    check(repo['id'] == REPO_ID and repo['full_name'] == REPO and repo['private'] is False
          and repo['visibility'] == 'public', 'repository identity mismatch')
    check(env.get('RUNNER_ARCH') == 'X64' and platform.machine() == 'x86_64', 'requires native linux/amd64')
    head = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root,text=True).strip()
    check(head == env.get('GITHUB_SHA'), 'checked-out commit differs from dispatched SHA')
    return head


def budget(now, free_bytes, available_bytes, *, admission, memory_gib):
    check(now < HARD and (not admission or now < STOP), 'campaign time limit')
    check((free_bytes >= 60*GIB) if admission else (free_bytes > 50*GIB), 'insufficient disk headroom')
    if admission:
        check(available_bytes >= (memory_gib+2) * GIB, 'insufficient RAM headroom')


def memory_available():
    match = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
    check(match, 'unavailable host memory reading')
    return int(match[1]) * 1024


def literal_files(design, value):
    rows = value if isinstance(value,list) else [value]
    result = set()
    for raw in rows:
        check(isinstance(raw,str) and raw.startswith('dir::'), 'source path must be dir::relative')
        field = raw[5:]
        check('/../' not in '/' + field + '/' and not field.startswith('/') and
              bool(re.fullmatch(r'[a-zA-Z0-9_.*/-]+',field)) and '**' not in field,
              'unreviewed source path expression')
        paths = sorted(design.glob(field))
        check(paths and len(paths) <= 32, 'missing or too many frozen source files')
        for path in paths:
            check(path.is_file() and path.resolve() == path and path.is_relative_to(design), 'unbound design path')
            result.add(path)
    return result


def source_proof(root, proof, cfg, design, grant):
    if proof['state']=='not_applicable':
        check(grant.get('purpose')=='settings_only_control','settings-only exception requires root grant')
        return
    check(proof['state']=='passed', 'source proof is required')
    receipt=read(verify_ref(root,proof['evidence']))
    check(receipt['schema']=='a070-formal-v1' and receipt['status']=='passed' and
          receipt['comparison']=='source_rtl_equivalence' and
          receipt['negative_control_status']=='counterexample_replayed', 'unsupported source proof')
    entries=cfg['VERILOG_FILES']
    inputs=[]
    for entry in (entries if isinstance(entries,list) else [entries]):
        inputs.extend(checksum(p) for p in sorted(literal_files(design,entry)))
    matches = []
    for side in ('gold','gate'):
        chunks = receipt['source_parts'][side]
        matches.append(inputs == [p['sha256'] for p in chunks])
    check(any(matches),'proven source does not match exact ordered physical RTL files')


def image_contract(command, result, ctx):
    # Bind units and aggregators to the code and OpenROAD actually selected by this image.
    probe="""import hashlib, importlib.metadata, json, subprocess
from pathlib import Path
import librelane
base=Path(librelane.__file__).parent
names=['scripts/openroad/sta/corner.tcl','config/flow.py','common/metrics/library.py']
info={'librelane':importlib.metadata.version('librelane'),
      'openroad':subprocess.check_output(['openroad','-version'],text=True).strip(),
      'files':{name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in names}}
print('TC_IMAGE_EVIDENCE '+json.dumps(info,sort_keys=True))
"""
    name = 'trueclock-unit-'+ctx['owner'][:14]
    run_command([*command,'--name',name,'--network','none',IMAGE,'python3','-c',probe],
                result/'toolchain.log',ctx,container=name)
    log=(result/'toolchain.log').read_text()
    rows=re.findall(r'^TC_IMAGE_EVIDENCE ([^\n]+)$',log,re.M)
    check(len(rows)==1,'missing toolchain/units receipt')
    observed=json.loads(rows[0])
    check(observed['librelane']=='3.0.16' and
          'dcf36133a369abc8f3c5e5738cd4d82e4903c0e0' in observed['openroad'] and
          observed['files']=={
            'scripts/openroad/sta/corner.tcl':'e0096884c1929ed80506366ecc3e809c78c8493180e366fc095e2b09f5c36f53',
            'config/flow.py':'c51a24d9f3f9eef6549c559c0469f61524a26ed0746db6fea706022f1db88dc8',
            'common/metrics/library.py':'086eebb9b2dd8ee0902fdfbd6f3002fbae217fe2a51fe8aefc5834a0ab9eb2ab'},
          'toolchain or unit producers differ from verified contract')
    return observed


def freeze(root, request_path, request_sha, approval_sha):
    check(checksum(request_path) == request_sha, 'request differs from independent dispatcher digest')
    req = read(request_path)
    check(req['schema_version'] == 'trueclock.physical-request.v1' and req['action'] == 'fresh_rebuild'
          and req['destination'] == 'farm' and req['repository'] == REPO, 'unsupported request')
    jid = req['job_id']
    check(re.fullmatch('[a-z][a-z0-9_-]{0,62}',jid) and request_path == root/'jobs'/(jid+'.json'),
          'invalid job ID or job path')
    check(datetime.fromisoformat(req['cutoff']) == HARD and datetime.fromisoformat(req['stop_new_heavy']) == STOP,
          'request cutoff differs from campaign')
    grant_path = verify_ref(root, req['approval'])
    check(req['approval']['sha256'] == approval_sha, 'grant differs from dispatcher digest')
    grant = read(grant_path)
    check(grant['id'] == jid and grant['approved_by'] == 'root' and grant['request_id'] == req['request_id']
          and grant['candidate_id'] == req['candidate_id'], 'grant does not authorize this candidate')
    runner = req['runner']
    check(runner['script'] == 'scripts/run_flow.py' and runner['backend'] == 'librelane' and
          verify_ref(root,{'path':runner['script'],'sha256':runner['sha256']}) == Path(__file__).resolve(),
          'runner identity mismatch')
    region = req['platform']
    check(region['pdk'] == 'sky130A' and region['library'] == 'sky130_fd_sc_hd' and
          region['time_unit'] == 'ns' and region['area_unit'] == 'um2' and
          len(region['corners']) == 9 and set(region['corners']) == CORNERS and region['io_policy'].strip(),
          'unqualified technology/units/corners')
    contract_file = verify_ref(root,region['contract'])
    r = req['resources']
    check(type(r['threads']) is int and 1 <= r['threads'] <= 4 and
          type(r['memory_gib']) in (int,float) and math.isfinite(r['memory_gib']) and
          1 <= r['memory_gib'] <= 6 and type(r['timeout_seconds']) is int and 
          1 <= r['timeout_seconds'] <= min(2400, grant['wall_limit_s']), 'invalid bounded resources')
    period = req['target_period_ns']
    check(type(period) in (int,float) and math.isfinite(period) and period>0,'invalid target period')
    config = verify_ref(root, runner['config'])
    design = config.parent
    check(design.is_relative_to(root/'designs') and design != root/'designs' and config.suffix == '.json',
          'input must be a JSON config in an isolated design folder')
    cfg = read(config)
    check(not set(cfg)-CONFIG_KEYS and cfg['DESIGN_NAME'] == runner['design'] and
          cfg['CLOCK_PERIOD'] == period and cfg.get('PDK','sky130A') == 'sky130A' and
          cfg.get('STD_CELL_LIBRARY','sky130_fd_sc_hd') == 'sky130_fd_sc_hd' and
          len(cfg['STA_CORNERS']) == 9 and set(cfg['STA_CORNERS']) == CORNERS, 'unqualified config')
    check(cfg.get('RUN_KLAYOUT_DRC',True) is True, 'DRC cannot be disabled')
    files = {config}
    for key in ('VERILOG_FILES','PNR_SDC_FILE','SIGNOFF_SDC_FILE','IO_PIN_ORDER_CFG'):
        if key == 'IO_PIN_ORDER_CFG' and key not in cfg:
            continue
        files.update(literal_files(design,cfg[key]))
    supplied = {verify_ref(root,item) for item in req['inputs']}
    check(files <= supplied, 'missing hash-bound source or constraint')
    check(len(supplied) == len(req['inputs']), 'duplicate bound input path')
    for path in files:
        if path.suffix in ('.v','.sv'):
            check(not re.search(r'`include|\$readmem[hb]',path.read_text()), 'HDL with external sources unsupported')
        if path.suffix in ('.sdc','.tcl'):
            check(not re.search(r'(?m)^\s*(?:source|exec|load|open)\s+',path.read_text()),
                  'external SDC dependencies unsupported')
    proof=req['source_formal']
    source_proof(root,proof,cfg,design,grant)
    if proof['state']=='passed':
        supplied.add(verify_ref(root,proof['evidence']))
    return req, design, files, {request_path, grant_path, contract_file, Path(__file__).resolve(), *supplied}


def stop_owned(container, owner):
    listing = subprocess.run(['docker','ps','--no-trunc','--filter','name=^/'+container+'$',
                              '--format','{{.ID}} {{.Names}}'],capture_output=True,text=True,timeout=8)
    check(listing.returncode == 0, 'could not inspect possible owned container')
    fields = [line.split() for line in listing.stdout.splitlines()]
    targets = [row[0] for row in fields if len(row)==2 and row[1]==container]
    if not targets:
        return
    check(len(targets)==1 and re.fullmatch('[0-9a-f]{64}',targets[0]), 'no immutable owned Docker ID')
    cid = targets[0]
    probe = subprocess.run(['docker','inspect','--format','{{index .Config.Labels "'+OWNER+'"}}',cid],
                           text=True,capture_output=True,timeout=8)
    if probe.returncode and re.search('no such (object|container)',probe.stderr,re.I):
        return
    check(probe.returncode==0 and probe.stdout.strip()==owner, 'foreign or unidentified container; cleanup refused')
    stopper = subprocess.run(['docker','stop','-t','5',cid],text=True,capture_output=True,timeout=14)
    if stopper.returncode and not re.search('no such (object|container)',stopper.stderr,re.I):
        raise Refusal('could not stop owned container ' + cid)


def run_command(command, log, context, *, container=None):
    with log.open('a') as stream:
        stream.write(json.dumps({'argv':command,'utc':datetime.now(timezone.utc).isoformat()})+'\n')
        stream.flush()
        budget(datetime.now(timezone.utc),shutil.disk_usage(context['root']).free,0,
               admission=False,memory_gib=0)
        check(time.monotonic() < context['deadline'], 'attempt timeout before process start')
        started = time.monotonic()
        proc = subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while proc.poll() is None:
                budget(datetime.now(timezone.utc),shutil.disk_usage(context['root']).free,0,
                       admission=False,memory_gib=0)
                remaining=context['deadline']-time.monotonic()
                check(remaining>0, 'attempt timeout')
                try:
                    proc.wait(timeout=min(2,remaining))
                except subprocess.TimeoutExpired:
                    pass
            check(proc.returncode == 0, f'{command[0]} returned {proc.returncode}; see raw log')
        finally:
            try:
                if proc.poll() is None:
                    os.killpg(proc.pid,signal.SIGTERM)
                    try:
                        proc.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL)
                        proc.wait(timeout=5)
            finally:
                # Verify the label before stopping a container, including creation/exit races.
                if container:
                    stop_owned(container,context['owner'])
            context['stages'].append({'program':command[0],'seconds':time.monotonic()-started,
                                      'exit_code':proc.returncode,'log':log.name})


def pdk_ready(pdk):
    target = (pdk/'sky130A').resolve(strict=True)
    check(target.is_relative_to(pdk) and target.parts[-2:] == (PDK_VERSION,'sky130A'), 'PDK identity mismatch')
    library = target/'libs.ref/sky130_fd_sc_hd/lib'
    for corner, expected in LIBS.items():
        check(checksum(library/f'sky130_fd_sc_hd__{corner}.lib')==expected, 'PDK Liberty bytes differ: '+corner)


def bounded_lease(env_value,event_value,now,timeout):
    check(isinstance(env_value,str) and env_value==event_value,'missing lease or dispatch lease mismatch')
    try:
        limit=datetime.fromisoformat(env_value)
    except ValueError as exc:
        raise Refusal('invalid dispatcher lease time') from exc
    check(limit.tzinfo is not None and limit.utcoffset() is not None,'lease needs an explicit offset')
    remaining=min((limit-now).total_seconds(),(HARD-now).total_seconds(),timeout)
    check(remaining>0,'dispatcher lease or campaign expired')
    return remaining


PDK_RELEASE_ID = 233817270
RELEASE_URL = ('https://github.com/fossi-foundation/ciel-releases/releases/download/'
               'sky130-'+PDK_VERSION+'/')
ASSETS = {
    'common.tar.zst': (274819141,6421325,'be8a548341fd9177af9e461ad7b4fdd89afbe03dba68dc42d155321fdc3f86bf'),
    'sky130_fd_sc_hd.tar.zst': (274824784,127299820,'17047505cfff30841bf53960b8cbf9e7c79b2a3bbb2ebec0bb254eb441e4ce1e'),
}
# The archive names and hashes are pinned to the public Ciel release. The worker has
# no network during extraction and the subsequent Ciel command can only enable local files.
UNPACK = '''import hashlib, pathlib, shutil, sys, tarfile, zstandard
assets=pathlib.Path(sys.argv[1])
target=pathlib.Path(sys.argv[2])
target.mkdir(parents=True,exist_ok=False)
total=0
files=0
for name,expected in zip(sys.argv[3::2],sys.argv[4::2]):
    archive=assets/name
    with archive.open("rb") as raw:
        if hashlib.file_digest(raw,"sha256").hexdigest()!=expected:
            raise ValueError("compressed archive SHA mismatch: "+name)
    with zstandard.open(archive,mode="rb") as source, tarfile.open(fileobj=source,mode="r|") as tar:
        for entry in tar:
            rel=pathlib.PurePosixPath(entry.name)
            if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] not in ("sky130A","sky130B"):
                raise ValueError("unsupported archive path: "+entry.name)
            if entry.isdir():
                continue
            if not entry.isfile():
                raise ValueError("unsupported archive member type: "+entry.name)
            files+=1
            total+=entry.size
            if files>90000 or total>4*(1<<30):
                raise ValueError("PDK extraction bound exceeded")
            out=target/rel
            out.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(entry) as src, out.open("xb") as dst:
                shutil.copyfileobj(src,dst,1024*1024)
            if out.stat().st_size!=entry.size:
                raise ValueError("short extracted file")
print("PDK extracted",files,total)
'''


def obtain_pdk(root,pdk,common,ctx,result,jid):
    cache=root/('.pdk-sources-'+jid)
    cache.mkdir()
    log=result/'acquisition.log'
    metadata={}
    for name,(asset_id,size,digest) in ASSETS.items():
        dest=cache/name
        command=['curl','--proto','=https','--proto-redir','=https','--location','--fail','--silent','--show-error',
                 '--connect-timeout','10','--max-time','90','--retry','2','--retry-delay','2',
                 '--retry-max-time','180','--retry-all-errors','--max-filesize',str(size),'--output',str(dest),
                 RELEASE_URL+name]
        run_command(command,log,ctx)
        check(dest.is_file() and dest.stat().st_size==size and checksum(dest)==digest, 'PDK release archive mismatch: '+name)
        metadata[name]={'release_id':PDK_RELEASE_ID,'asset_id':asset_id,'bytes':size,'sha256':digest}
    name='trueclock-unpack-'+jid
    args=[part for key,(_,_,digest) in ASSETS.items() for part in (key,digest)]
    run_command([*common,'--name',name,'--network','none','-v',f'{cache}:/assets:ro','-v',f'{pdk}:/pdk',IMAGE,
                 'python3','-c',UNPACK,'/assets','/pdk/ciel/sky130/versions/'+PDK_VERSION,*args],log,ctx,container=name)
    name='trueclock-enable-'+jid
    run_command([*common,'--name',name,'--network','none','-v',f'{pdk}:/pdk',IMAGE,
                 'ciel','enable','--pdk-root','/pdk','--pdk-family','sky130','-l','sky130_fd_sc_hd',PDK_VERSION],
                log,ctx,container=name)
    pdk_ready(pdk)
    return metadata


def run(root, req, design, files, refs, result, head):
    r = req['resources']
    jid = req['job_id']
    owner = uuid.uuid4().hex
    stages = []
    ctx = {'root':root,'owner':owner,'deadline':time.monotonic(),'stages':stages}
    preimages = {str(p.relative_to(root)):checksum(p) for p in refs}
    record = {'schema_version':'farm-run/v1','run_id':jid,'checkout_sha':head,'execution_status':'failed',
              'github_run_id':os.environ['GITHUB_RUN_ID'],'request_sha256':checksum(root/'jobs'/(jid+'.json')),
              'image':IMAGE,'platform':'linux/amd64','pdk_version':PDK_VERSION,'liberty_hashes':LIBS,
              'started_utc':datetime.now(timezone.utc).isoformat(),'physical_acceptance':None,
              'formal_status':'not_evaluated','input_sha256':preimages,'stages':stages,'owner_token':owner}
    started = time.monotonic()
    try:
        lease = os.environ.get('TRUECLOCK_LEASE_DEADLINE')
        event_lease = read(Path(os.environ['GITHUB_EVENT_PATH'])).get('inputs',{}).get('lease_deadline')
        ctx['deadline'] = time.monotonic()+bounded_lease(lease,event_lease,datetime.now(timezone.utc),r['timeout_seconds'])
        record['lease_deadline'] = lease
        budget(datetime.now(timezone.utc),shutil.disk_usage(root).free,memory_available(),admission=True,
               memory_gib=r['memory_gib'])
        image_log = result/'acquisition.log'
        run_command(['docker','pull','--platform','linux/amd64',IMAGE],image_log,ctx)
        value = json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True,timeout=15))[0]
        check(value['Architecture']=='amd64' and value['Os']=='linux' and value['Id']==IMAGE_CONFIG and
              IMAGE in value.get('RepoDigests', []), 'installed image identity differs')
        record['image_id'] = value['Id']
        pdk = root/'pdk'
        pdk.mkdir(exist_ok=False)
        check(pdk.resolve() == pdk, 'symlinked PDK root refused')
        common = ['docker','run','--rm','--pull','never','--platform','linux/amd64',
                  '--label',OWNER+'='+owner,'--cpus',str(r['threads']),
                  '--memory',str(int(r['memory_gib']*1024))+'m',
                  '--user',f'{os.getuid()}:{os.getgid()}','-e','HOME=/tmp','-e','PDK_ROOT=/pdk']
        record['units_evidence'] = image_contract(common,result,ctx)
        record['pdk_release_assets']=obtain_pdk(root,pdk,common,ctx,result,jid)
        # Input staging makes the only writable project bind an empty run directory.
        isolated = result/'design'
        isolated.mkdir()
        for source in sorted(files):
            target = isolated/source.relative_to(design)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
            check(checksum(target)==preimages[str(source.relative_to(root))], 'staged source differs')
        runs = isolated/'runs'
        runs.mkdir()
        budget(datetime.now(timezone.utc),shutil.disk_usage(root).free,memory_available(),admission=True,
               memory_gib=r['memory_gib'])
        record['pre_flow_disk_bytes'] = shutil.disk_usage(root).free
        name = 'trueclock-physical-'+jid
        flow = [*common,'--name',name,'--network','none','--cap-drop','ALL',
                '-v',f'{pdk}:/pdk:ro','-v',f'{isolated}:/tc/design:ro','-v',f'{runs}:/tc/design/runs',
                '-w','/tc/design',IMAGE,'python3','-m','librelane','--pdk-root','/pdk','--manual-pdk',
                '--pdk','sky130A','--scl','sky130_fd_sc_hd','--run-tag',jid,
                '--design-dir','/tc/design','-j',str(r['threads']),'/tc/design/'+Path(req['runner']['config']['path']).name]
        run_command(flow,result/'flow.log',ctx,container=name)
        pdk_ready(pdk)
        for path, old in preimages.items():
            check(checksum(root/path)==old, 'input changed during build: '+path)
        directory = runs/jid
        resolved = read(directory/'resolved.json')
        metrics = directory/'final/metrics.json'
        check(metrics.is_file() and resolved['DESIGN_NAME']==req['runner']['design'] and
              resolved['PDK']=='sky130A' and resolved['STD_CELL_LIBRARY']=='sky130_fd_sc_hd' and
              resolved['CLOCK_PERIOD']==req['target_period_ns'] and set(resolved['STA_CORNERS'])==CORNERS,
              'incomplete output or resolved contract differs')
        states = sorted(directory.glob('[0-9]*-*/state_out.json'),
                        key=lambda p:int(p.parent.name.split('-')[0]))
        check(states, 'no step state recorded')
        record.update(execution_status='completed',final_state_relative=str(states[-1].relative_to(result)),
                      final_state_sha256=checksum(states[-1]),metrics_sha256=checksum(metrics),
                      resolved_sha256=checksum(directory/'resolved.json'))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        record['reason'] = f'{type(exc).__name__}: {exc}'
        return 2
    finally:
        record.update(elapsed_s=time.monotonic()-started, ended_utc=datetime.now(timezone.utc).isoformat(),
                      end_disk_bytes=shutil.disk_usage(root).free)
        exclusive_json(result/'runner.json',record)
        print(json.dumps({key:record.get(key) for key in ('run_id','execution_status','reason','elapsed_s')}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',required=True)
    parser.add_argument('--request-sha256',required=True)
    parser.add_argument('--approval-sha256',required=True)
    args=parser.parse_args()
    root=Path.cwd().resolve()
    try:
        head=check_host(root,os.environ)
        path=checked_path(root,args.request)
        req,design,files,refs=freeze(root,path,args.request_sha256,args.approval_sha256)
        # All evidence must be committed to the one dispatched revision.
        rels=[str(p.relative_to(root)) for p in sorted(refs)]
        subprocess.run(['git','ls-files','--error-unmatch','--',*rels],cwd=root,check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['git','diff','--quiet','HEAD','--',*rels],cwd=root,check=True)
        result=root/'results'/req['job_id']
        result.parent.mkdir(exist_ok=True)
        with (result.parent/'.worker.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            result.mkdir()
            def stop(signum, frame):
                raise KeyboardInterrupt('received termination signal')
            signal.signal(signal.SIGTERM,stop)
            return run(root,req,design,files,refs,result,head)
    except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError) as exc:
        print('refused: '+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
