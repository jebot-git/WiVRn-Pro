// SPDX-License-Identifier: GPL-3.0-or-later
#import <Cocoa/Cocoa.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <CoreImage/CoreImage.h>
#import <ApplicationServices/ApplicationServices.h>
#import <Security/Security.h>
#import <ImageIO/ImageIO.h>
#import "VirtualDisplay.h"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#include <math.h>
#include <signal.h>

static const int Width=1920, Height=1080, Port=9758;
static const int64_t EventTag=0x57565244;
static BOOL WriteAll(int fd, const void *data, size_t size) {
    const char *p=data;
    while(size) { ssize_t n=send(fd,p,size,0); if(n<0 && errno==EINTR)continue; if(n<=0)return NO; p+=n;size-=n; }
    return YES;
}
static NSString *ReadLine(int fd) {
    NSMutableData *data=[NSMutableData data];
    for(int i=0;i<4096;i++) { char c;ssize_t n=recv(fd,&c,1,0);if(n<0&&errno==EINTR){i--;continue;}if(n!=1)return nil;if(c=='\n')return [[NSString alloc]initWithData:data encoding:NSUTF8StringEncoding];[data appendBytes:&c length:1]; }
    return nil;
}

@interface Companion : NSObject <NSApplicationDelegate, SCStreamOutput, SCStreamDelegate> {
    double _lastClickAt[3];
    CGPoint _lastClickPosition[3];
    int _clickCount[3];
}
@property(strong) NSWindow *window;
@property(strong) NSStatusItem *statusItem;
@property(strong) dispatch_source_t termination;
@property BOOL background;
@property double lastRetry;
@property(strong) NSTextField *status;
@property(strong) CGVirtualDisplay *display;
@property(strong) SCStream *stream;
@property(strong) CIContext *context;
@property(strong) NSLock *lock;
@property(strong) NSData *lastJPEG;
@property(strong) NSString *token;
@property(strong) NSMutableSet<NSNumber *> *keys;
@property(strong) NSMutableSet<NSNumber *> *buttons;
@property(strong) id mouseMonitor;
@property int listener, client;
@property BOOL focused, starting;
@property uint64_t sequence, connectionGeneration, physicalButtons;
@property double heartbeat, physicalUntil, lastSent;
@property CGPoint cursor;
@end
@implementation Companion
- (void)showStatus:(NSString *)text {
    dispatch_async(dispatch_get_main_queue(), ^{ self.status.stringValue=text; if(self.background)NSLog(@"%@",text); });
}
- (void)message:(NSString *)text {
    [self showStatus:text];
    [self sendType:3 data:[NSJSONSerialization dataWithJSONObject:@{@"message":text} options:0 error:nil]];
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.listener=-1; self.client=-1; self.lock=[NSLock new];self.keys=[NSMutableSet new];self.buttons=[NSMutableSet new];self.context=[CIContext contextWithOptions:nil];
    self.window=[[NSWindow alloc]initWithContentRect:NSMakeRect(0,0,580,230) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable backing:NSBackingStoreBuffered defer:NO];
    self.window.title=@"Mac Desktop Relay";self.window.releasedWhenClosed=NO;
    self.status=[NSTextField wrappingLabelWithString:@"One virtual 1920×1080 desktop. Connect with Mac Desktop Relay on Linux."];
    self.status.frame=NSMakeRect(20,95,540,115);[self.window.contentView addSubview:self.status];
    NSArray *labels=@[@"Screen capture permission",@"Input permission",@"Start / retry"];
    SEL actions[]={@selector(capturePermission:),@selector(inputPermission:),@selector(startButton:)};
    for(int i=0;i<3;i++){NSButton *b=[NSButton buttonWithTitle:labels[i] target:self action:actions[i]];b.frame=NSMakeRect(15+i*185,35,180,36);[self.window.contentView addSubview:b];}
    [self.window center];
    if(!self.background)[self showSettings:nil];
    self.statusItem=[NSStatusBar.systemStatusBar statusItemWithLength:NSVariableStatusItemLength];
    self.statusItem.button.title=@"MDR";
    NSMenu *menu=[NSMenu new];
    NSMenuItem *settings=[[NSMenuItem alloc]initWithTitle:@"Mac Desktop Relay settings…" action:@selector(showSettings:) keyEquivalent:@""];
    settings.target=self;[menu addItem:settings];
    NSMenuItem *retry=[[NSMenuItem alloc]initWithTitle:@"Retry capture" action:@selector(startButton:) keyEquivalent:@""];
    retry.target=self;[menu addItem:retry];
    [menu addItem:NSMenuItem.separatorItem];
    NSMenuItem *quit=[[NSMenuItem alloc]initWithTitle:@"Quit Mac Desktop Relay" action:@selector(terminate:) keyEquivalent:@""];
    quit.target=NSApp;[menu addItem:quit];self.statusItem.menu=menu;
    signal(SIGTERM,SIG_IGN);
    self.termination=dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL,SIGTERM,0,dispatch_get_main_queue());
    dispatch_source_set_event_handler(self.termination, ^{[NSApp terminate:nil];});
    dispatch_resume(self.termination);
    if(![self startListener])return;
    __weak Companion *weak=self;
    self.mouseMonitor=[NSEvent addGlobalMonitorForEventsMatchingMask:(NSEventMaskMouseMoved|NSEventMaskLeftMouseDown|NSEventMaskLeftMouseUp|NSEventMaskRightMouseDown|NSEventMaskRightMouseUp|NSEventMaskOtherMouseDown|NSEventMaskOtherMouseUp|NSEventMaskScrollWheel|NSEventMaskLeftMouseDragged|NSEventMaskRightMouseDragged|NSEventMaskOtherMouseDragged) handler:^(NSEvent *event){
        Companion *s=weak;if(!s)return;
        if(event.CGEvent && CGEventGetIntegerValueField(event.CGEvent,kCGEventSourceUserData)==EventTag)return;
        if(event.buttonNumber>=0 && event.buttonNumber<64){
            uint64_t bit=1ULL<<event.buttonNumber;
            if(event.type==NSEventTypeLeftMouseDown || event.type==NSEventTypeRightMouseDown || event.type==NSEventTypeOtherMouseDown)s.physicalButtons|=bit;
            if(event.type==NSEventTypeLeftMouseUp || event.type==NSEventTypeRightMouseUp || event.type==NSEventTypeOtherMouseUp)s.physicalButtons&=~bit;
        }
        s.physicalUntil=NSProcessInfo.processInfo.systemUptime+1.5;
        [s releaseInputs];
    }];
    [NSTimer scheduledTimerWithTimeInterval:0.25 repeats:YES block:^(NSTimer *timer){
        Companion *s=weak;if(!s)return;double now=NSProcessInfo.processInfo.systemUptime;
        if(s.focused && now-s.heartbeat>1.5){s.focused=NO;[s releaseInputs];}
        if(s.background && !s.stream && !s.starting && now-s.lastRetry>5 && CGPreflightScreenCaptureAccess()){
            s.lastRetry=now;[s startCapture];
        }
        [s.lock lock];NSData *last=s.lastJPEG;BOOL repeat=now-s.lastSent>0.75;[s.lock unlock];
        if(last && repeat)dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0), ^{[s sendType:2 data:last];});
    }];
    [self startCapture];
}
- (void)showSettings:(id)sender { [self.window makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES]; }
- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)visible { [self showSettings:nil];return YES; }
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return !self.background; }
- (void)applicationWillTerminate:(NSNotification *)notification {
    [self releaseInputs];if(self.mouseMonitor)[NSEvent removeMonitor:self.mouseMonitor];
    [self.lock lock];if(self.client>=0)shutdown(self.client,SHUT_RDWR);[self.lock unlock];
    if(self.listener>=0)close(self.listener);
}
- (void)capturePermission:(id)sender { CGRequestScreenCaptureAccess();[self message:@"After granting Screen & System Audio Recording, quit and reopen this app, then Start / retry."]; }
- (void)inputPermission:(id)sender { AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)@{(__bridge NSString *)kAXTrustedCheckOptionPrompt:@YES}); }
- (void)startButton:(id)sender { [self startCapture]; }
- (BOOL)startListener {
    NSString *directory=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Mac Desktop Relay"];
    NSError *error=nil;
    if(![[NSFileManager defaultManager]createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:&error]){[self showStatus:error.localizedDescription];return NO;}
    NSString *path=[directory stringByAppendingPathComponent:@"relay.token"];
    if(![[NSFileManager defaultManager]fileExistsAtPath:path]){
        NSString *legacy=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/WiVRn-Pro/relay.token"];
        if([[NSFileManager defaultManager]fileExistsAtPath:legacy]){
            if(![[NSFileManager defaultManager]copyItemAtPath:legacy toPath:path error:&error]){[self showStatus:error.localizedDescription];return NO;}
        }
    }
    self.token=[[NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    NSCharacterSet *hex=[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"];
    if(self.token.length!=64 || [self.token rangeOfCharacterFromSet:hex.invertedSet].location!=NSNotFound){
        uint8_t bytes[32];if(SecRandomCopyBytes(kSecRandomDefault,sizeof(bytes),bytes)!=errSecSuccess){[self showStatus:@"Cannot generate pairing token"];return NO;}
        NSMutableString *token=[NSMutableString string];for(int i=0;i<32;i++)[token appendFormat:@"%02x",bytes[i]];self.token=token;
        if(![token writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:&error]){[self showStatus:error.localizedDescription];return NO;}
    }
    chmod(path.fileSystemRepresentation,0600);
    int fd=socket(AF_INET,SOCK_STREAM,0), one=1;
    if(fd<0){[self showStatus:@"Cannot create relay socket"];return NO;}
    setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
    struct sockaddr_in address={0};address.sin_len=sizeof(address);address.sin_family=AF_INET;address.sin_port=htons(Port);address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    if(bind(fd,(struct sockaddr *)&address,sizeof(address)) || listen(fd,1)){close(fd);[self showStatus:@"Relay port is already in use. Close the other companion first."];return NO;}
    self.listener=fd;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0), ^{[self acceptClients];});
    return YES;
}
- (void)acceptClients {
    while(YES){@autoreleasepool{
        int fd=accept(self.listener,NULL,NULL);if(fd<0)return;int one=1;setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof(one));
        struct timeval timeout={2,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));timeout.tv_sec=0;timeout.tv_usec=200000;setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
        NSString *line=ReadLine(fd);NSDictionary *auth=line?[NSJSONSerialization JSONObjectWithData:[line dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil]:nil;
        if(![auth isKindOfClass:NSDictionary.class] || ![auth[@"op"]isEqual:@"auth"] || ![auth[@"token"]isEqual:self.token]){close(fd);continue;}
        [self.lock lock];self.client=fd;self.connectionGeneration++;uint64_t generation=self.connectionGeneration;[self.lock unlock];
        dispatch_sync(dispatch_get_main_queue(), ^{self.sequence=0;self.focused=NO;self.heartbeat=NSProcessInfo.processInfo.systemUptime;[self releaseInputs];});
        NSData *info=[NSJSONSerialization dataWithJSONObject:@{@"width":@(Width),@"height":@(Height),@"codec":@"jpeg",@"fps":@15} options:0 error:nil];[self sendType:1 data:info];
        while((line=ReadLine(fd))){
            NSDictionary *message=[NSJSONSerialization JSONObjectWithData:[line dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil];
            if(![message isKindOfClass:NSDictionary.class])break;
            dispatch_sync(dispatch_get_main_queue(), ^{[self handleInput:message];});
        }
        [self.lock lock];self.client=-1;[self.lock unlock];close(fd);
        dispatch_sync(dispatch_get_main_queue(), ^{if(self.connectionGeneration==generation){self.focused=NO;[self releaseInputs];}});
    }}
}
- (void)sendType:(uint8_t)type data:(NSData *)data {
    if(!data.length || data.length>4*1024*1024)return;
    uint8_t header[10]={'W','V','R','D',1,type,0,0,0,0};uint32_t length=htonl((uint32_t)data.length);memcpy(header+6,&length,4);
    [self.lock lock];
    if(self.client>=0){
        if(!WriteAll(self.client,header,sizeof(header)) || !WriteAll(self.client,data.bytes,data.length))shutdown(self.client,SHUT_RDWR);
        else if(type==2)self.lastSent=NSProcessInfo.processInfo.systemUptime;
    }
    [self.lock unlock];
}
- (void)startCapture {
    if(self.stream || self.starting)return;
    if(!CGPreflightScreenCaptureAccess()){[self message:@"Grant Screen capture permission, then quit and reopen the companion."];return;}
    @try {
        if(!self.display){
            if(!NSClassFromString(@"CGVirtualDisplay") || !NSClassFromString(@"CGVirtualDisplayDescriptor")){[self message:@"Virtual display API unavailable on this macOS release."];return;}
            CGVirtualDisplayDescriptor *d=[NSClassFromString(@"CGVirtualDisplayDescriptor") new];
            d.queue=dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0);d.name=@"Mac Desktop Relay";d.maxPixelsWide=Width;d.maxPixelsHigh=Height;d.vendorID=0x5756;d.productID=0x5052;d.serialNum=1;if([d respondsToSelector:NSSelectorFromString(@"setSerialNumber:")])[d setValue:@1 forKey:@"serialNumber"];d.sizeInMillimeters=CGSizeMake(508,285.75);
            d.whitePoint=CGPointMake(0.3127,0.3290);d.redPrimary=CGPointMake(0.64,0.33);d.greenPrimary=CGPointMake(0.30,0.60);d.bluePrimary=CGPointMake(0.15,0.06);
            self.display=[[NSClassFromString(@"CGVirtualDisplay") alloc]initWithDescriptor:d];
            CGVirtualDisplaySettings *settings=[NSClassFromString(@"CGVirtualDisplaySettings") new];settings.hiDPI=0;
            settings.modes=@[[[NSClassFromString(@"CGVirtualDisplayMode") alloc]initWithWidth:Width height:Height refreshRate:60]];
            if(!self.display || ![self.display applySettings:settings]){self.display=nil;[self message:@"Creating the dedicated 1080p virtual display failed."];return;}
        }
    } @catch(NSException *e){self.display=nil;[self message:[@"Virtual display failed: " stringByAppendingString:e.reason?:@"unknown"]];return;}
    self.starting=YES;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),dispatch_get_main_queue(), ^{
        [SCShareableContent getShareableContentWithCompletionHandler:^(SCShareableContent *content,NSError *error){
            dispatch_async(dispatch_get_main_queue(), ^{
                if(error){self.starting=NO;[self message:error.localizedDescription];return;}
                SCDisplay *selected=nil;for(SCDisplay *candidate in content.displays)if(candidate.displayID==self.display.displayID){selected=candidate;break;}
                if(!selected){self.starting=NO;[self message:@"Virtual display is not yet visible to capture. Press Start / retry."];return;}
                SCContentFilter *filter=[[SCContentFilter alloc]initWithDisplay:selected excludingWindows:@[]];
                SCStreamConfiguration *config=[SCStreamConfiguration new];config.width=Width;config.height=Height;config.minimumFrameInterval=CMTimeMake(1,15);config.queueDepth=3;config.pixelFormat=kCVPixelFormatType_32BGRA;config.showsCursor=YES;config.capturesAudio=NO;
                self.stream=[[SCStream alloc]initWithFilter:filter configuration:config delegate:self];
                NSError *outputError=nil;
                if(![self.stream addStreamOutput:self type:SCStreamOutputTypeScreen sampleHandlerQueue:dispatch_queue_create("wivrn.capture",DISPATCH_QUEUE_SERIAL) error:&outputError]){self.stream=nil;self.starting=NO;[self message:outputError.localizedDescription];return;}
                [self.stream startCaptureWithCompletionHandler:^(NSError *startError){dispatch_async(dispatch_get_main_queue(), ^{self.starting=NO;if(startError){self.stream=nil;[self message:startError.localizedDescription];}else{[self message:@"Virtual desktop ready: 1920×1080, prototype JPEG/15 fps. Linux connects over SSH."];}});}];
            });
        }];
    });
}
- (void)stream:(SCStream *)stream didOutputSampleBuffer:(CMSampleBufferRef)sample ofType:(SCStreamOutputType)type {
    if(type!=SCStreamOutputTypeScreen || !CMSampleBufferIsValid(sample))return;
    CVPixelBufferRef buffer=CMSampleBufferGetImageBuffer(sample);if(!buffer || CVPixelBufferGetWidth(buffer)!=Width || CVPixelBufferGetHeight(buffer)!=Height)return;
    CFArrayRef attachments=CMSampleBufferGetSampleAttachmentsArray(sample,false);
    if(!attachments || !CFArrayGetCount(attachments))return;
    NSDictionary *info=(__bridge NSDictionary *)CFArrayGetValueAtIndex(attachments,0);
    if(!info[SCStreamFrameInfoStatus] || [info[SCStreamFrameInfoStatus]integerValue]!=SCFrameStatusComplete)return;
    @autoreleasepool{
        CIImage *image=[CIImage imageWithCVPixelBuffer:buffer];CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        NSData *jpeg=[self.context JPEGRepresentationOfImage:image colorSpace:space options:@{(__bridge NSString *)kCGImageDestinationLossyCompressionQuality:@0.75}];CGColorSpaceRelease(space);
        [self.lock lock];self.lastJPEG=jpeg;[self.lock unlock];[self sendType:2 data:jpeg];
    }
}
- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error { [self.lock lock];self.lastJPEG=nil;[self.lock unlock];dispatch_async(dispatch_get_main_queue(), ^{self.stream=nil;self.focused=NO;[self releaseInputs];[self message:error.localizedDescription];}); }
- (CGEventFlags)flags {
    CGEventFlags flags=0;if([self.keys containsObject:@56])flags|=kCGEventFlagMaskShift;if([self.keys containsObject:@59])flags|=kCGEventFlagMaskControl;if([self.keys containsObject:@58])flags|=kCGEventFlagMaskAlternate;if([self.keys containsObject:@55])flags|=kCGEventFlagMaskCommand;return flags;
}
- (void)post:(CGEventRef)event {
    if(!event)return;CGEventSetIntegerValueField(event,kCGEventSourceUserData,EventTag);CGEventSetFlags(event,[self flags]);CGEventPost(kCGHIDEventTap,event);CFRelease(event);
}
- (void)key:(int)code down:(BOOL)down {
    if(down)[self.keys addObject:@(code)];else[self.keys removeObject:@(code)];[self post:CGEventCreateKeyboardEvent(NULL,(CGKeyCode)code,down)];
}
- (void)button:(int)button down:(BOOL)down {
    if(down)[self.buttons addObject:@(button)];else[self.buttons removeObject:@(button)];
    CGEventType type=button==0?(down?kCGEventLeftMouseDown:kCGEventLeftMouseUp):button==1?(down?kCGEventRightMouseDown:kCGEventRightMouseUp):(down?kCGEventOtherMouseDown:kCGEventOtherMouseUp);
    if(down){
        double now=NSProcessInfo.processInfo.systemUptime;
        double dx=self.cursor.x-_lastClickPosition[button].x,dy=self.cursor.y-_lastClickPosition[button].y;
        _clickCount[button]=(now-_lastClickAt[button]<NSEvent.doubleClickInterval && dx*dx+dy*dy<25)?MIN(_clickCount[button]+1,3):1;
        _lastClickAt[button]=now;_lastClickPosition[button]=self.cursor;
    }
    CGEventRef event=CGEventCreateMouseEvent(NULL,type,self.cursor,(CGMouseButton)button);
    if(event)CGEventSetIntegerValueField(event,kCGMouseEventClickState,_clickCount[button]);
    [self post:event];
}
- (void)releaseInputs { for(NSNumber *key in [self.keys copy])[self key:key.intValue down:NO];for(NSNumber *button in [self.buttons copy]){
    if(self.physicalButtons & (1ULL<<button.intValue))[self.buttons removeObject:button];
    else [self button:button.intValue down:NO];
} }
- (void)handleInput:(NSDictionary *)m {
    if(![m[@"seq"]isKindOfClass:NSNumber.class] || ![m[@"op"]isKindOfClass:NSString.class])return;
    uint64_t seq=[m[@"seq"]unsignedLongLongValue];if(!seq || seq<=self.sequence)return;self.sequence=seq;
    double now=NSProcessInfo.processInfo.systemUptime;self.heartbeat=now;NSString *op=m[@"op"];
    if([op isEqual:@"ping"])return;
    if([op isEqual:@"focus"]){
        BOOL requested=[m[@"active"]isKindOfClass:NSNumber.class] && [m[@"active"]boolValue];
        if(!requested){self.focused=NO;[self releaseInputs];return;}
        if(!AXIsProcessTrusted()){[self message:@"Grant Input permission on the Mac before controlling it."];return;}
        if(!self.display || !self.stream)return;
        if(!self.focused){CGRect bounds=CGDisplayBounds(self.display.displayID);self.cursor=CGPointMake(CGRectGetMidX(bounds),CGRectGetMidY(bounds));}
        self.focused=YES;return;
    }
    if(!self.focused || now<self.physicalUntil || self.physicalButtons || !self.display || !AXIsProcessTrusted())return;
    if([op isEqual:@"move"] || [op isEqual:@"scroll"]){
        if(![m[@"x"]isKindOfClass:NSNumber.class] || ![m[@"y"]isKindOfClass:NSNumber.class])return;double x=[m[@"x"]doubleValue], y=[m[@"y"]doubleValue];if(!isfinite(x)||!isfinite(y))return;
        if([op isEqual:@"move"]){
            if(x<0||x>1||y<0||y>1)return;CGRect b=CGDisplayBounds(self.display.displayID);self.cursor=CGPointMake(b.origin.x+x*(b.size.width-1),b.origin.y+y*(b.size.height-1));
            CGEventType type=kCGEventMouseMoved;CGMouseButton button=kCGMouseButtonLeft;
            if([self.buttons containsObject:@0])type=kCGEventLeftMouseDragged;else if([self.buttons containsObject:@1]){type=kCGEventRightMouseDragged;button=kCGMouseButtonRight;}else if([self.buttons containsObject:@2]){type=kCGEventOtherMouseDragged;button=kCGMouseButtonCenter;}
            [self post:CGEventCreateMouseEvent(NULL,type,self.cursor,button)];
        }else if(fabs(x)<=10 && fabs(y)<=10){[self post:CGEventCreateScrollWheelEvent(NULL,kCGScrollEventUnitLine,2,(int32_t)lround(y),(int32_t)lround(x))];}
    }else if([op isEqual:@"key"] || [op isEqual:@"button"]){
        NSString *field=[op isEqual:@"key"]?@"code":@"button";
        if(![m[field]isKindOfClass:NSNumber.class] || ![m[@"down"]isKindOfClass:NSNumber.class])return;int code=[m[field]intValue];BOOL down=[m[@"down"]boolValue];
        if([op isEqual:@"key"] && code>=0 && code<128)[self key:code down:down];else if([op isEqual:@"button"] && code>=0 && code<=2)[self button:code down:down];
    }
}
@end
int main(int argc,const char **argv){@autoreleasepool{NSApplication *app=NSApplication.sharedApplication;BOOL background=[NSProcessInfo.processInfo.arguments containsObject:@"--background"];[app setActivationPolicy:background?NSApplicationActivationPolicyAccessory:NSApplicationActivationPolicyRegular];__attribute__((objc_precise_lifetime)) Companion *delegate=[Companion new];delegate.background=background;app.delegate=delegate;[app run];}return 0;}
