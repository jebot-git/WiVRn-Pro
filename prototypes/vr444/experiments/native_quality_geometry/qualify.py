#!/usr/bin/env python3
"""Isolated geometry admission tests and actual native ON/OFF syntax checks."""
import argparse,hashlib,json,shlex,subprocess,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
OVERLAY=ROOT/'validation/wivrn-codecs/integration/source-overlay'
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--native',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    assert a.native.is_absolute()and a.out.is_absolute()and not a.out.exists();a.out.mkdir()
    sources=[HERE/'test.cpp',Path(__file__),OVERLAY/'common/codec_test/quality_geometry.h',OVERLAY/'client/scenes/stream.cpp',OVERLAY/'server/encoder/encoder_settings.cpp']
    before={str(p):digest(p)for p in sources};commands=[]
    def run(label,argv,cwd=None):
        start=time.monotonic();r=subprocess.run(argv,cwd=cwd,capture_output=True,timeout=90)
        commands.append(dict(label=label,argv=list(map(str,argv)),cwd=str(cwd or Path.cwd()),returncode=r.returncode,elapsed_s=time.monotonic()-start,stdout=r.stdout.decode(),stderr=r.stderr.decode()))
        (a.out/'partial.json').write_text(json.dumps(commands,indent=2)+'\n')
        if r.returncode:raise RuntimeError(label+' failed: '+r.stderr.decode()[-2000:])
    compiler='/usr/bin/clang++';binary=a.out/'admission-asan'
    run('admission-build',[compiler,'-std=c++23','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(OVERLAY/'common'),str(HERE/'test.cpp'),'-o',str(binary)])
    run('admission-run',[str(binary)])
    stream=(OVERLAY/'client/scenes/stream.cpp').read_text();start=stream.index('            auto read_choice = ');end=stream.index('\n            const auto token=',start);body=stream[start:end]
    source='''#include "codec_test/quality_geometry.h"
#include <filesystem>
#include <fstream>
#include <string>
#include <iostream>
int main(int argc,char**argv){if(argc!=2)return 2;std::filesystem::path path=argv[1];
'''+body+'''
auto check=[](bool v){if(!v)throw std::runtime_error("IO check");};
auto write=[&](std::string s){std::ofstream(path,std::ios::binary).write(s.data(),s.size());};
check(!read_choice(path));
for(auto text:{"off","q1-960x1056","q1-960x1056\\n","q1-960x1056\\r\\n"}){write(text);auto value=read_choice(path);check(value.has_value());check(wivrn::codec_test::quality_geometry::client(*value,true,"rrb32").width== (std::string_view(text)=="off"?576:960));}
for(auto text:{std::string(""),std::string("q1-960x1056 "),std::string("q1-960x1056\\n\\n"),std::string(64,'x'),std::string(65,'x'),std::string(4096,'x')}){write(text);bool rejected=false;try{auto value=read_choice(path);(void)wivrn::codec_test::quality_geometry::client(*value,true,"rrb32");}catch(const std::runtime_error&){rejected=true;}check(rejected);}
std::filesystem::remove(path);std::filesystem::create_directory(path);bool directory_rejected=false;try{read_choice(path);}catch(const std::runtime_error&){directory_rejected=true;}check(directory_rejected);
std::filesystem::remove(path);std::filesystem::create_symlink(path.string()+"-absent",path);bool link_rejected=false;try{read_choice(path);}catch(const std::runtime_error&){link_rejected=true;}check(link_rejected);
std::cout<<"13 actual client file-reader/default/bounded-token checks passed\\n";}
'''
    cpp=a.out/'client-choice.cpp';cpp.write_text(source);io=a.out/'client-choice-asan'
    run('client-reader-build',[compiler,'-std=c++23','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(OVERLAY/'common'),str(cpp),'-o',str(io)])
    run('client-reader-run',[str(io),str(a.out/'choice.txt')])
    databases=[(a.native/'.cxx/Debug/2hd2e6j2/arm64-v8a/compile_commands.json','client/scenes/stream.cpp','android-stream'),(a.native/'build/codec-server/compile_commands.json','server/encoder/encoder_settings.cpp','host-encoder-settings')]
    db_hashes={};toolchains={compiler:digest(compiler)}
    for database,name,label in databases:
        db_hashes[str(database)]=digest(database);rows=json.loads(database.read_bytes());row=next(r for r in rows if r['file']==str(a.native/name));args=row.get('arguments')or shlex.split(row['command']);toolchains[args[0]]=digest(args[0]);base=[];i=0
        # Copy only the edited TU. Quoted includes must resolve to the single
        # native header tree; mixing identical pragma-once headers from both
        # worktrees can create duplicate declarations unrelated to the change.
        private_source=a.out/(label+'.cpp');private_source.write_bytes((OVERLAY/name).read_bytes())
        while i<len(args):
            if args[i]in('-o','-MF','-MT','-MQ'):i+=2;continue
            if args[i]in('-c','-MD','-MMD','-MP'):i+=1;continue
            if args[i]==row['file']:base.append(str(private_source))
            else:base.append(args[i])
            i+=1
        for enabled in(True,False):
            argv=[x for x in base if enabled or not x.startswith('-DWIVRN_CODEC_TESTS')]
            argv += ['-fsyntax-only','-iquote',str((a.native/name).parent),'-I'+str(OVERLAY/'common')]
            if not enabled:argv+=['-UWIVRN_CODEC_TESTS']
            run(label+('-on'if enabled else'-off'),argv,row['directory'])
    assert before=={str(p):digest(p)for p in sources}
    assert db_hashes=={p:digest(p)for p in db_hashes}
    report=dict(schema=1,status='pass',source_sha256=before,sources_unchanged=True,native_compile_database_sha256=db_hashes,toolchain_sha256=toolchains,binary_sha256={str(p):digest(p)for p in(binary,io)},actual_client_reader_extracted_sha256=digest(cpp),commands=commands,native_link=False,android_application_build=False,headset_used=False,gpu_execution=False,scope='Pure admission and actual client-reader ASan/UBSan, exact existing native compile flags with owned source/header replacements, Android stream and host encoder ON/OFF syntax only')
    (a.out/'qualification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':'pass','commands':len(commands),'output':str(a.out)}))
if __name__=='__main__':main()
