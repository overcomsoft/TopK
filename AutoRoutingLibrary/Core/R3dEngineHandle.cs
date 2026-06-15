using System;
using Microsoft.Win32.SafeHandles;

namespace AutoRoutingLibrary.Core
{
    internal sealed class R3dEngineHandle : SafeHandleZeroOrMinusOneIsInvalid
    {
        private R3dEngineHandle() : base(ownsHandle: true) { }

        public static R3dEngineHandle Create()
        {
            var handle = new R3dEngineHandle();
            handle.SetHandle(Native.r3d_create());
            if (handle.IsInvalid)
            {
                handle.Dispose();
                throw new InvalidOperationException("Failed to create native Routing3D engine handle.");
            }
            return handle;
        }

        protected override bool ReleaseHandle()
        {
            Native.r3d_destroy(handle);
            return true;
        }
    }
}
