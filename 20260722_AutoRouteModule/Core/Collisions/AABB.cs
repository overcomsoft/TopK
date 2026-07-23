using System.Numerics;

namespace AutoRouteModule.Core
{
    public struct AABB
    {
        public Vector3 Min;
        public Vector3 Max;

        public Vector3 Center => (Min + Max) * 0.5f;
        public Vector3 Size => Max - Min;
        public Vector3 HalfSize => (Max - Min) * 0.5f;

        public bool Contains(Vector3 point)
        {
            return point.X >= Min.X && point.X <= Max.X
                && point.Y >= Min.Y && point.Y <= Max.Y
                && point.Z >= Min.Z && point.Z <= Max.Z;
        }

        public bool Intersects(AABB other)
        {
            return Min.X <= other.Max.X && Max.X >= other.Min.X
                && Min.Y <= other.Max.Y && Max.Y >= other.Min.Y
                && Min.Z <= other.Max.Z && Max.Z >= other.Min.Z;
        }

        public bool IntersectsStrict(AABB other)
        {
            return Min.X < other.Max.X && Max.X > other.Min.X
                && Min.Y < other.Max.Y && Max.Y > other.Min.Y
                && Min.Z < other.Max.Z && Max.Z > other.Min.Z;
        }

        public bool Contains(AABB other)
        {
            return Min.X <= other.Min.X && Max.X >= other.Max.X
                && Min.Y <= other.Min.Y && Max.Y >= other.Max.Y
                && Min.Z <= other.Min.Z && Max.Z >= other.Max.Z;
        }

        public static AABB FromCenterSize(Vector3 center, Vector3 size)
        {
            Vector3 half = size * 0.5f;
            return new AABB
            {
                Min = center - half,
                Max = center + half
            };
        }

        public static AABB Union(AABB a, AABB b)
        {
            return new AABB
            {
                Min = MathHelper.Min(a.Min, b.Min),
                Max = MathHelper.Max(a.Max, b.Max)
            };
        }
    }

}
