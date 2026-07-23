using System.Numerics;

namespace AutoRouteModule.Core
{
    public struct OBB
    {
        public Vector3 Center;
        public Vector3 Extents;
        public Vector3[] Axes;

    }

    public struct OBB_RAW
    {
        public float OBB_LEFT_BOTTOM_BACK_X { get; set; }
        public float OBB_LEFT_BOTTOM_BACK_Y { get; set; }
        public float OBB_LEFT_BOTTOM_BACK_Z { get; set; }
        public float OBB_RIGHT_BOTTOM_BACK_X { get; set; }
        public float OBB_RIGHT_BOTTOM_BACK_Y { get; set; }
        public float OBB_RIGHT_BOTTOM_BACK_Z { get; set; }
        public float OBB_RIGHT_TOP_BACK_X { get; set; }
        public float OBB_RIGHT_TOP_BACK_Y { get; set; }
        public float OBB_RIGHT_TOP_BACK_Z { get; set; }
        public float OBB_LEFT_TOP_BACK_X { get; set; }
        public float OBB_LEFT_TOP_BACK_Y { get; set; }
        public float OBB_LEFT_TOP_BACK_Z { get; set; }
        public float OBB_LEFT_BOTTOM_FRONT_X { get; set; }
        public float OBB_LEFT_BOTTOM_FRONT_Y { get; set; }
        public float OBB_LEFT_BOTTOM_FRONT_Z { get; set; }
        public float OBB_RIGHT_BOTTOM_FRONT_X { get; set; }
        public float OBB_RIGHT_BOTTOM_FRONT_Y { get; set; }
        public float OBB_RIGHT_BOTTOM_FRONT_Z { get; set; }
        public float OBB_RIGHT_TOP_FRONT_X { get; set; }
        public float OBB_RIGHT_TOP_FRONT_Y { get; set; }
        public float OBB_RIGHT_TOP_FRONT_Z { get; set; }
        public float OBB_LEFT_TOP_FRONT_X { get; set; }
        public float OBB_LEFT_TOP_FRONT_Y { get; set; }
        public float OBB_LEFT_TOP_FRONT_Z { get; set; }
    }
}