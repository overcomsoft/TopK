using AutoRouteModule.Core;
using System;
using System.Numerics;

namespace AutoRouteModule.Utils
{
    public static class CollisionUtility
    {
        public static bool AabbVsObb(
       Vector3 aCenter,
       Vector3 aHalf,
       in OBB obb)
        {
            Vector3 B0 = obb.Axes[0];
            Vector3 B1 = obb.Axes[1];
            Vector3 B2 = obb.Axes[2];

            Vector3 t = obb.Center - aCenter;

            const float epsilon = 1e-6f;

            float r00 = B0.X;
            float r01 = B1.X;
            float r02 = B2.X;

            float r10 = B0.Y;
            float r11 = B1.Y;
            float r12 = B2.Y;

            float r20 = B0.Z;
            float r21 = B1.Z;
            float r22 = B2.Z;

            float ar00 = Math.Abs(r00) + epsilon;
            float ar01 = Math.Abs(r01) + epsilon;
            float ar02 = Math.Abs(r02) + epsilon;

            float ar10 = Math.Abs(r10) + epsilon;
            float ar11 = Math.Abs(r11) + epsilon;
            float ar12 = Math.Abs(r12) + epsilon;

            float ar20 = Math.Abs(r20) + epsilon;
            float ar21 = Math.Abs(r21) + epsilon;
            float ar22 = Math.Abs(r22) + epsilon;

            float ax = aHalf.X;
            float ay = aHalf.Y;
            float az = aHalf.Z;

            float bx = obb.Extents.X;
            float by = obb.Extents.Y;
            float bz = obb.Extents.Z;

            float tx = t.X;
            float ty = t.Y;
            float tz = t.Z;

            float ra;
            float rb;

            // AABB axes
            ra = ax;
            rb = bx * ar00 + by * ar01 + bz * ar02;
            if (Math.Abs(tx) > ra + rb) return false;

            ra = ay;
            rb = bx * ar10 + by * ar11 + bz * ar12;
            if (Math.Abs(ty) > ra + rb) return false;

            ra = az;
            rb = bx * ar20 + by * ar21 + bz * ar22;
            if (Math.Abs(tz) > ra + rb) return false;

            // OBB axes
            ra = ax * ar00 + ay * ar10 + az * ar20;
            rb = bx;
            if (Math.Abs(tx * r00 + ty * r10 + tz * r20) > ra + rb) return false;

            ra = ax * ar01 + ay * ar11 + az * ar21;
            rb = by;
            if (Math.Abs(tx * r01 + ty * r11 + tz * r21) > ra + rb) return false;

            ra = ax * ar02 + ay * ar12 + az * ar22;
            rb = bz;
            if (Math.Abs(tx * r02 + ty * r12 + tz * r22) > ra + rb) return false;

            // Cross axes
            ra = ay * ar20 + az * ar10;
            rb = by * ar02 + bz * ar01;
            if (Math.Abs(tz * r10 - ty * r20) > ra + rb) return false;

            ra = ay * ar21 + az * ar11;
            rb = bx * ar02 + bz * ar00;
            if (Math.Abs(tz * r11 - ty * r21) > ra + rb) return false;

            ra = ay * ar22 + az * ar12;
            rb = bx * ar01 + by * ar00;
            if (Math.Abs(tz * r12 - ty * r22) > ra + rb) return false;

            ra = ax * ar20 + az * ar00;
            rb = by * ar12 + bz * ar11;
            if (Math.Abs(tx * r20 - tz * r00) > ra + rb) return false;

            ra = ax * ar21 + az * ar01;
            rb = bx * ar12 + bz * ar10;
            if (Math.Abs(tx * r21 - tz * r01) > ra + rb) return false;

            ra = ax * ar22 + az * ar02;
            rb = bx * ar11 + by * ar10;
            if (Math.Abs(tx * r22 - tz * r02) > ra + rb) return false;

            ra = ax * ar10 + ay * ar00;
            rb = by * ar22 + bz * ar21;
            if (Math.Abs(ty * r00 - tx * r10) > ra + rb) return false;

            ra = ax * ar11 + ay * ar01;
            rb = bx * ar22 + bz * ar20;
            if (Math.Abs(ty * r01 - tx * r11) > ra + rb) return false;

            ra = ax * ar12 + ay * ar02;
            rb = bx * ar21 + by * ar20;
            if (Math.Abs(ty * r02 - tx * r12) > ra + rb) return false;

            return true;
        }

        public static bool AabbVsObb(AABB aabb, OBB obb)
        {
            return AabbVsObb(
                aabb.Center,
                aabb.HalfSize,
                in obb
            );
        }
    }
}
