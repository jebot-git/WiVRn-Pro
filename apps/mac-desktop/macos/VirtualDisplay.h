// SPDX-License-Identifier: GPL-3.0-or-later
// Private CoreGraphics ABI, dynamically looked up. See PROVENANCE.txt.
#import <Cocoa/Cocoa.h>
@interface CGVirtualDisplayDescriptor : NSObject
@property(strong) dispatch_queue_t queue;
@property(copy) NSString *name;
@property unsigned int maxPixelsWide, maxPixelsHigh, vendorID, productID, serialNum;
@property CGSize sizeInMillimeters;
@property CGPoint redPrimary, greenPrimary, bluePrimary, whitePoint;
@end
@interface CGVirtualDisplaySettings : NSObject
@property(strong) NSArray *modes;
@property unsigned int hiDPI;
@end
@interface CGVirtualDisplayMode : NSObject
- (instancetype)initWithWidth:(unsigned int)width height:(unsigned int)height refreshRate:(double)rate;
@end
@interface CGVirtualDisplay : NSObject
@property(readonly) CGDirectDisplayID displayID;
- (instancetype)initWithDescriptor:(CGVirtualDisplayDescriptor *)descriptor;
- (BOOL)applySettings:(CGVirtualDisplaySettings *)settings;
@end
