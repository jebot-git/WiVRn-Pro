// Source reference-space admission only; no XR/Vulkan calls or filesystem writes.
#define main quality_application_main
#include "app.cpp"
#undef main
int main() {
    try {
        auto check=[](std::vector<std::string> strings) {
            std::vector<char*> args;for(auto& s:strings)args.push_back(s.data());
            return parse(int(args.size()),args.data());
        };
        std::vector<std::string> base={"test-space","--pattern","chroma-grid","--width","17","--height","33","--output","/tmp/sub15-quality-space-test-never-created"};
        unsigned passed=0,rejected=0;
        const auto legacy=check(base);require(reference_space_type(legacy)==XR_REFERENCE_SPACE_TYPE_VIEW,"legacy VIEW changed");++passed;
        for(const auto& [name,type]:std::array<std::pair<const char*,XrReferenceSpaceType>,2>{{{"LOCAL",XR_REFERENCE_SPACE_TYPE_LOCAL},{"VIEW",XR_REFERENCE_SPACE_TYPE_VIEW}}}) {
            auto args=base;args.insert(args.end(),{"--source-reference-space",name});const auto opt=check(args);require(reference_space_type(opt)==type,"wrong actual reference space enum");++passed;
        }
        for(const char* bad:{"STAGE","local","VIEW_SPACE","","LOCAL;touch x"}) {
            auto args=base;args.insert(args.end(),{"--source-reference-space",bad});bool fail=false;try{check(args);}catch(const std::exception&){fail=true;}require(fail,"invalid space admitted");++rejected;
        }
        auto duplicate=base;duplicate.insert(duplicate.end(),{"--source-reference-space","LOCAL","--source-reference-space","VIEW"});bool fail=false;try{check(duplicate);}catch(const std::exception&){fail=true;}require(fail,"duplicate space admitted");++rejected;
        require(monotonic_ns(timespec{1,2})==1000000002LL,"timespec conversion");++passed;
        require(monotonic_ns(timespec{9223372036LL,854775807})==INT64_MAX,"timespec exact upper bound");++passed;
        for(const auto ts:std::array<timespec,6>{{{-1,0},{0,-1},{0,1000000000L},{0,0},{9223372036LL,854775808},{9223372037LL,0}}}) {
            bool bad=false;try{monotonic_ns(ts);}catch(const std::exception&){bad=true;}require(bad,"invalid timespec admitted");++rejected;
        }
        std::cout<<json({{"passed",passed},{"rejected",rejected},{"openxr_exercised",false},{"gpu_exercised",false}}).dump()<<'\n';return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
