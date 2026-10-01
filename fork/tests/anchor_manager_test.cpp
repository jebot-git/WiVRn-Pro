// SPDX-License-Identifier: GPL-3.0-or-later
#include "anchor_manager.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <fstream>
#include <source_location>
using namespace wivrn::anchor;
static auto session = reinterpret_cast<XrSession>(1);
static auto stage = reinterpret_cast<XrSpace>(2);
static auto hand = reinterpret_cast<XrSpace>(3);
static auto anchor_space = reinterpret_cast<XrSpace>(4);
static XrAsyncRequestIdFB serial = 0;
static XrUuidEXT id{{1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16}};
static XrResult create_result=XR_SUCCESS, component_result=XR_SUCCESS;
static bool locatable=true, storable=true, tracked=true, query_found=true;
static int creates=0, saves=0, erases=0, destroys=0, checks=0;
static XrPosef captured{};
static void check(bool good, std::source_location at=std::source_location::current()) { if(!good)std::cerr<<at.line()<<" check failed\n"; assert(good); ++checks; }
static XrResult XRAPI_CALL create(XrSession, const XrSpatialAnchorCreateInfoFB *i, XrAsyncRequestIdFB *r) { ++creates; captured=i->poseInSpace; *r=++serial; return create_result; }
static XrResult XRAPI_CALL set_component(XrSpace, const XrSpaceComponentStatusSetInfoFB *i, XrAsyncRequestIdFB *r) { *r=++serial; if(i->componentType==XR_SPACE_COMPONENT_TYPE_LOCATABLE_FB)locatable=true;else storable=true;return component_result; }
static XrResult XRAPI_CALL get_component(XrSpace, XrSpaceComponentTypeFB kind, XrSpaceComponentStatusFB *s) { s->enabled=kind==XR_SPACE_COMPONENT_TYPE_LOCATABLE_FB?locatable:storable;return component_result; }
static XrResult XRAPI_CALL save(XrSession, const XrSpaceSaveInfoFB *i, XrAsyncRequestIdFB *r) { check(i->location==XR_SPACE_STORAGE_LOCATION_LOCAL_FB);*r=++serial;++saves;return XR_SUCCESS; }
static XrResult XRAPI_CALL erase(XrSession, const XrSpaceEraseInfoFB *, XrAsyncRequestIdFB *r) { check(storable); ++erases; *r=++serial;return XR_SUCCESS; }
static XrResult XRAPI_CALL query(XrSession, const XrSpaceQueryInfoBaseHeaderFB *, XrAsyncRequestIdFB *r) { *r=++serial;return XR_SUCCESS; }
static XrResult XRAPI_CALL retrieve(XrSession, XrAsyncRequestIdFB, XrSpaceQueryResultsFB *r) { r->resultCountOutput=query_found?1:0;if(r->resultCapacityInput&&query_found)r->results[0]={anchor_space,id};return XR_SUCCESS; }
static XrResult XRAPI_CALL locate(XrSpace space, XrSpace base, XrTime, XrSpaceLocation *l) { check(base==stage);l->pose={{0,0,0,1},{1,0.5f,-2}};l->locationFlags=tracked?15:0;if(space==hand)l->pose.position.x=2;return XR_SUCCESS; }
static XrResult XRAPI_CALL destroy(XrSpace) { ++destroys; return XR_SUCCESS; }
static manager::dispatch api{create,set_component,get_component,save,erase,query,retrieve,locate,destroy};
template<class T> void event(manager &m,T e) { XrEventDataBuffer b{};static_assert(sizeof(e)<=sizeof(b));std::memcpy(&b,&e,sizeof(e));m.event(b); }
static void created(manager &m) { XrEventDataSpatialAnchorCreateCompleteFB e{XR_TYPE_EVENT_DATA_SPATIAL_ANCHOR_CREATE_COMPLETE_FB};e.requestId=serial;e.result=XR_SUCCESS;e.space=anchor_space;e.uuid=id;event(m,e); }
static void saved(manager &m,XrResult r=XR_SUCCESS) { XrEventDataSpaceSaveCompleteFB e{XR_TYPE_EVENT_DATA_SPACE_SAVE_COMPLETE_FB};e.requestId=serial;e.result=r;e.space=anchor_space;e.uuid=id;event(m,e); }
static void queried(manager &m) { XrEventDataSpaceQueryResultsAvailableFB e{XR_TYPE_EVENT_DATA_SPACE_QUERY_RESULTS_AVAILABLE_FB};e.requestId=serial;event(m,e);XrEventDataSpaceQueryCompleteFB done{XR_TYPE_EVENT_DATA_SPACE_QUERY_COMPLETE_FB};done.requestId=serial;done.result=XR_SUCCESS;event(m,done); }
int main() {
 auto path=std::filesystem::path("/tmp/wivrn-anchor-unit.uuid"); std::filesystem::remove(path);
 { manager m(session,stage,hand,path,{});check(m.tick(1).status==state::unsupported);m.execute({1,operation::create},1);check(creates==0); }
 {
  manager m(session,stage,hand,path,api);check(m.tick(1).status==state::empty);
  m.execute({10,operation::create},1);check(m.tick(2).status==state::placing);check(creates==0);
  check(m.tick(3'000'000'002).status==state::creating);check(creates==1&&captured.position.x==2);
  m.execute({11,operation::create},3'000'000'003);check(creates==1);
  created(m); check(m.tick(3'000'000'004).status==state::saving);check(saves==1);
  saved(m);auto sample=m.tick(3'000'000'005);check(sample.status==state::localized&&sample.persisted&&sample.flags==15);check(std::filesystem::exists(path));
  m.execute({10,operation::erase},3'000'000'006);check(m.tick(3'000'000'007).status==state::localized);
  tracked=false;check(m.tick(3'000'000'008).flags==0);check(m.tick(3'000'000'009).status==state::lost);tracked=true;
  XrEventDataReferenceSpaceChangePending change{XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING};change.referenceSpaceType=XR_REFERENCE_SPACE_TYPE_STAGE;change.changeTime=4'000'000'000;event(m,change);
  sample=m.tick(3'500'000'000);check(sample.flags==0&&sample.generation==1);check(m.tick(4'000'000'001).flags==15);
 }
 check(destroys==1);
 {
  manager m(session,stage,hand,path,api);check(m.tick(1).status==state::loading);queried(m);check(m.tick(2).status==state::localized);
  m.execute({20,operation::create},3);check(m.tick(4).status==state::error&&creates==1&&std::filesystem::exists(path));
  storable=false;
  m.execute({21,operation::erase},5);check(m.tick(6).status==state::erasing&&erases==0);
  check(m.tick(7).status==state::erasing&&erases==1);
  XrEventDataSpaceEraseCompleteFB e{XR_TYPE_EVENT_DATA_SPACE_ERASE_COMPLETE_FB};e.requestId=serial;e.result=XR_ERROR_RUNTIME_FAILURE;event(m,e);check(std::filesystem::exists(path));
  m.execute({22,operation::erase},8);m.tick(9);e.requestId=serial;e.result=XR_SUCCESS;event(m,e);check(m.tick(10).status==state::empty&&!std::filesystem::exists(path));
 }
 {
  manager m(session,stage,hand,path,api);create_result=XR_ERROR_PERMISSION_INSUFFICIENT;m.execute({1,operation::create},1);check(m.tick(4'000'000'000).result==XR_ERROR_PERMISSION_INSUFFICIENT);create_result=XR_SUCCESS;
 }
 {
  manager m(session,stage,hand,path,api);m.execute({1,operation::create},1);m.tick(4'000'000'000);check(m.tick(20'000'000'000).status==state::error);
 }
 {
  std::ofstream(path)<<"not-a-uuid";manager m(session,stage,hand,path,api);check(m.tick(1).result==XR_ERROR_FILE_CONTENTS_INVALID);std::filesystem::remove(path);
 }
 {
  manager m(session,stage,hand,path,api);m.execute({1,operation::create},1);m.tick(4'000'000'000);created(m);locatable=storable=false;check(m.tick(4'000'000'001).status==state::saving);m.tick(4'000'000'002);saved(m,XR_ERROR_RUNTIME_FAILURE);check(m.tick(4'000'000'003).status==state::error&&!std::filesystem::exists(path));
 }
 {
  const int before=creates;locatable=storable=tracked=true;
  manager m(session,stage,hand,path,api,false);
  XrPosef frozen{{0,0,0,1},{3,1,-4}};
  check(m.create_at(1,frozen,1));check(creates==before+1&&captured.position.x==3&&captured.position.z==-4);
  check(m.busy()&&!m.retry(2,2));created(m);check(m.tick(3).status==state::saving);
  saved(m,XR_ERROR_RUNTIME_FAILURE);check(m.tick(4).status==state::error);
  check(m.retry(3,5));check(m.tick(6).status==state::saving);
  saved(m);check(m.tick(7).persisted&&std::filesystem::exists(path));
  check(creates==before+1); // Retrying the failed save did not create another anchor.
 }
 {
  manager m(session,stage,hand,path,api,false);
  check(m.tick(1).status==state::empty); // Guided flow controls when saved slots load.
  check(!m.create_at(1,{{0,0,0,1},{0,0,0}},2));check(std::filesystem::exists(path));
  check(m.retry(2,3));queried(m);check(m.tick(4).status==state::localized);
 }
 std::filesystem::remove(path);
 {
  manager m(session,stage,hand,path,api,false);
  check(m.create_at(1,{{0,0,0,1},{0,0,-1}},1));
  check(m.tick(20'000'000'000).status==state::error&&m.busy());
  check(!m.retry(2,20'000'000'001)); // A timeout doesn't abandon the async handle.
  created(m);check(m.tick(20'000'000'002).status==state::saving);
  saved(m);check(m.tick(20'000'000'003).persisted);
 }
 std::filesystem::remove(path);
 std::cout<<"ANCHOR_MANAGER "<<checks<<" checks passed\n";
}
