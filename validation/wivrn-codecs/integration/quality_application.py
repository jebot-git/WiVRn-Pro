"""Pure pre-ADB admission and owned snapshot of an explicit quality fixture app."""
import hashlib
import json
from pathlib import Path
import re

MAX_BINARY = 64*1024*1024
MAX_PLAN = 65536
PATTERNS = ('chroma-grid', 'rgb-ramp', 'fine-detail')
REQUIRED = dict(mode='custom-rrb32', rrb_source='q1', q=1, compression='none',
                pairing='paired', source_trace=True, quality_capture=True,
                stereo_envelope_cap=262144, protected_radius_degrees=10.0,
                gaze_mode='fixed-center')
FIELDS = {'schema','profile','latency_comparable','source_generation','pattern','source_stage',
          'width','height','frame_limit','seconds_limit','capture_directory','application',
          'executable','executable_sha256','original_executable','gpu_or_runtime_started',
          'required_native_contract','source_fov_order','quality_mapping_fov_order','limitations'}

def require(ok, message):
    if not ok: raise ValueError('quality application: '+message)

def digest(data): return hashlib.sha256(data).hexdigest()
def valid_sha(value): return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None

def parse(data):
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'duplicate JSON key');result[key]=value
        return result
    def constant(value): raise ValueError('nonfinite JSON constant '+value)
    return json.loads(data,object_pairs_hook=pairs,parse_constant=constant)

def read(path, limit):
    require(path.is_absolute() and path.is_file() and not path.is_symlink(),'absolute regular input required')
    require(path.stat().st_size <= limit,'input exceeds bound')
    with path.open('rb') as source: data=source.read(limit+1)
    require(len(data)<=limit,'input grew beyond bound')
    return data

def bounded(value, low, high):
    require(type(value) is int and low<=value<=high,'bounded exact integer required')
    return value

def argv(plan, executable):
    return ['/usr/bin/timeout','-k','2',str(plan['seconds_limit']+10),'/usr/bin/stdbuf','-oL','-eL',str(executable),
            '--pattern',plan['pattern'],'--width',str(plan['width']),'--height',str(plan['height']),
            '--frames',str(plan['frame_limit']),'--seconds',str(plan['seconds_limit']),
            '--output',plan['capture_directory']]

def contract(plan):
    require(type(plan) is dict and set(plan)==FIELDS,'unknown or missing plan fields')
    require(type(plan['schema']) is int and plan['schema']==1,'plan schema')
    require(plan['profile']=='application-quality-stimuli' and plan['latency_comparable'] is False
            and plan['source_generation']=='direct_encoded_srgb_byte_upload'
            and plan['source_stage']=='application_swapchain_before_compositor'
            and plan['gpu_or_runtime_started'] is False,'quality/source scope')
    require(plan['pattern'] in PATTERNS,'unknown fixture pattern')
    width=bounded(plan['width'],1,4096);height=bounded(plan['height'],1,4096)
    require(width*height<=4194304,'4 MP per-eye cap')
    bounded(plan['frame_limit'],1,10800);bounded(plan['seconds_limit'],1,120)
    for key in ('capture_directory','executable','original_executable'):
        require(type(plan[key]) is str and Path(plan[key]).is_absolute()
                and str(Path(plan[key]))==plan[key],'canonical absolute path required')
    require(valid_sha(plan['executable_sha256']),'invalid executable SHA')
    required=plan['required_native_contract']
    require(type(required) is dict and set(required)==set(REQUIRED)
            and all(type(required[k]) is type(v) and required[k]==v for k,v in REQUIRED.items()),'native contract changed')
    require(plan['source_fov_order']==['left','right','up','down']
            and plan['quality_mapping_fov_order']==['left','right','down','up'],'FOV order changed')
    require(type(plan['limitations']) is list and all(type(v) is str for v in plan['limitations']),'scope notes')
    require(plan['application']==argv(plan,plan['executable']),'noncanonical executable/argv template')

def preflight(args):
    """Read-only, no subprocess/ADB. Returns immutable bytes and parsed metadata."""
    path=getattr(args,'quality_application_plan',None)
    expected=getattr(args,'quality_application_plan_sha256',None)
    if path is None:
        require(expected is None,'plan digest without plan');return None
    require(valid_sha(expected),'explicit plan SHA256 required')
    path=Path(path);raw=read(path,MAX_PLAN);require(digest(raw)==expected,'plan digest mismatch')
    plan=parse(raw);contract(plan)
    for key in ('mode','rrb_source','q','compression','source_trace','quality_capture'):
        value=REQUIRED[key]
        require(type(getattr(args,key,None)) is type(value) and getattr(args,key)==value,'required native setting '+key)
    require(getattr(args,'decoder_backend',None)=='cpu-neon' and getattr(args,'cpu_output',None)=='cached-copy','RRB native coordinator/output contract')
    bounded(getattr(args,'rrb_workers',None),2,4);require(args.rrb_workers in (2,4),'two/four workers required')
    require(getattr(args,'rrb_output',None) in ('cpu','staged-gpu'),'unknown RRB output')
    require(not hasattr(args,'pairing') or args.pairing=='paired','paired publication required')
    output=Path(args.out);require(output.is_absolute(),'absolute native output required')
    require(plan['capture_directory']==str(output/'app-source') and not (output/'app-source').exists(),'capture must be new exact output/app-source')
    native_seconds=bounded(getattr(args,'seconds',None),6,3600)
    require(plan['seconds_limit']<=native_seconds-5,'app must finish at least five seconds before native cleanup')
    executable=Path(plan['executable']);data=read(executable,MAX_BINARY)
    require(data.startswith(b'\x7fELF') and digest(data)==plan['executable_sha256'],'planned ELF bytes differ')
    # The exact bytes are cached before any live operation. Later pathname
    # replacement never changes what configure() copies or executes.
    return dict(plan=plan,plan_bytes=raw,binary_bytes=data,plan_path=str(path),
                plan_sha256=expected,helper_sha256=digest(Path(__file__).read_bytes()))

def configure(cached, runtime, output):
    """Copy cached bytes into an owned runtime directory; never launch an app."""
    if cached is None:return None,None
    plan=parse(cached['plan_bytes']);contract(plan)
    require(digest(cached['plan_bytes'])==cached['plan_sha256'],'cached plan bytes changed')
    require(digest(cached['binary_bytes'])==plan['executable_sha256'],'cached executable bytes changed')
    require(digest(Path(__file__).read_bytes())==cached['helper_sha256'],'helper changed after preflight')
    output,runtime=Path(output),Path(runtime)
    require(output.is_absolute() and runtime.is_absolute() and runtime.is_dir() and not runtime.is_symlink(),'absolute owned runtime/output required')
    require(plan['capture_directory']==str(output/'app-source') and not (output/'app-source').exists(),'capture output changed after preflight')
    directory=runtime/'quality-application';directory.mkdir(mode=0o700)
    executable=directory/'quality-stimuli'
    with executable.open('xb') as target:target.write(cached['binary_bytes'])
    executable.chmod(0o500)
    require(digest(read(executable,MAX_BINARY))==plan['executable_sha256'],'snapshot write mismatch')
    output.mkdir(parents=True,exist_ok=True)
    with (output/'quality-application-plan.json').open('xb') as target:target.write(cached['plan_bytes'])
    command=argv(plan,executable)
    record=dict(schema=1,profile=plan['profile'],latency_eligible=False,latency_comparable=False,
        source_application_space='VIEW',source_generation=plan['source_generation'],source_stage=plan['source_stage'],
        pattern=plan['pattern'],width=plan['width'],height=plan['height'],frame_limit=plan['frame_limit'],seconds_limit=plan['seconds_limit'],
        capture_directory=plan['capture_directory'],plan_path=cached['plan_path'],plan_sha256=cached['plan_sha256'],original_plan_sha256=cached['plan_sha256'],
        archived_plan='quality-application-plan.json',original_executable=plan['original_executable'],
        planned_executable=plan['executable'],executed_snapshot=str(executable),snapshot_path=str(executable),executable_sha256=plan['executable_sha256'],
        helper_sha256=cached['helper_sha256'],application=command,actual_application=command,
        required_native_contract=REQUIRED,source_fov_order=plan['source_fov_order'],quality_mapping_fov_order=plan['quality_mapping_fov_order'])
    return record,command
