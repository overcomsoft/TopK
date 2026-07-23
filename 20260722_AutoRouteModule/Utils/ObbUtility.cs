using AutoRouteModule.Core;
using System;
using System.Collections.Generic;
using System.Numerics;

namespace AutoRouteModule.Utils
{
    public static class ObbUtility
    {
        public static OBB CreateOBB(Vector3 center, Vector3 halfExtents, Quaternion rotation)
        {
            Matrix4x4 rotMatrix = Matrix4x4.CreateFromQuaternion(rotation);
            return new OBB
            {
                Center = center,
                Extents = halfExtents,
                Axes = new Vector3[]
                {
                    new Vector3(rotMatrix.M11, rotMatrix.M21, rotMatrix.M31),
                    new Vector3(rotMatrix.M12, rotMatrix.M22, rotMatrix.M32),
                    new Vector3(rotMatrix.M13, rotMatrix.M23, rotMatrix.M33)
                }
            };
        }

        public static OBB CreateOBBFromAABB(AABB aabb, Quaternion rotation)
        {
            Vector3 center = (aabb.Min + aabb.Max) * 0.5f;
            Vector3 halfExtents = (aabb.Max - aabb.Min) * 0.5f;
            return CreateOBB(center, halfExtents, rotation);
        }

        public static OBB CreateOBBFromAABB(AABB aabb)
        {
            Vector3 center = (aabb.Min + aabb.Max) * 0.5f;
            Vector3 halfExtents = (aabb.Max - aabb.Min) * 0.5f;
            return new OBB
            {
                Center = center,
                Extents = halfExtents,
                Axes = new Vector3[]
                {
                    Vector3.UnitX,
                    Vector3.UnitY,
                    Vector3.UnitZ
                }
            };
        }

        public static OBB CreateOBBFromRaw(OBB_RAW rawData)
        {
            Vector3 leftBottomBack = ToUnityPos(rawData.OBB_LEFT_BOTTOM_BACK_X, rawData.OBB_LEFT_BOTTOM_BACK_Y, rawData.OBB_LEFT_BOTTOM_BACK_Z);
            Vector3 rightBottomBack = ToUnityPos(rawData.OBB_RIGHT_BOTTOM_BACK_X, rawData.OBB_RIGHT_BOTTOM_BACK_Y, rawData.OBB_RIGHT_BOTTOM_BACK_Z);
            Vector3 leftTopBack = ToUnityPos(rawData.OBB_LEFT_TOP_BACK_X, rawData.OBB_LEFT_TOP_BACK_Y, rawData.OBB_LEFT_TOP_BACK_Z);
            Vector3 leftBottomFront = ToUnityPos(rawData.OBB_LEFT_BOTTOM_FRONT_X, rawData.OBB_LEFT_BOTTOM_FRONT_Y, rawData.OBB_LEFT_BOTTOM_FRONT_Z);

            Vector3 axisX = Vector3.Normalize(rightBottomBack - leftBottomBack);
            Vector3 axisY = Vector3.Normalize(leftTopBack - leftBottomBack);
            Vector3 axisZ = Vector3.Normalize(leftBottomFront - leftBottomBack);

            float extentX = Vector3.Distance(rightBottomBack, leftBottomBack) * 0.5f;
            float extentY = Vector3.Distance(leftTopBack, leftBottomBack) * 0.5f;
            float extentZ = Vector3.Distance(leftBottomFront, leftBottomBack) * 0.5f;

            Vector3 pos = leftBottomBack + (axisX * extentX + axisY * extentY + axisZ * extentZ);


            return new OBB
            {
                Center = pos,
                Extents = new Vector3(extentX, extentY, extentZ),
                Axes = new Vector3[] {
                    axisX,
                    axisY,
                    axisZ
                }
            };
        }

        public static AABB CalculateWorldAabb(Vector3 center, Vector3 halfExtents, Quaternion rotation)
        {
            Matrix4x4 rotMatrix = Matrix4x4.CreateFromQuaternion(rotation);

            Vector3 absX = MathHelper.Abs(new Vector3(rotMatrix.M11, rotMatrix.M21, rotMatrix.M31)) * halfExtents.X;
            Vector3 absY = MathHelper.Abs(new Vector3(rotMatrix.M12, rotMatrix.M22, rotMatrix.M32)) * halfExtents.Y;
            Vector3 absZ = MathHelper.Abs(new Vector3(rotMatrix.M13, rotMatrix.M23, rotMatrix.M33)) * halfExtents.Z;

            Vector3 worldHalf = absX + absY + absZ;

            return new AABB
            {
                Min = center - worldHalf,
                Max = center + worldHalf
            };
        }

        public static AABB CalculateAABBFromOBB(OBB obb)
        {
            Vector3 absX = MathHelper.Abs(obb.Axes[0]) * obb.Extents.X;
            Vector3 absY = MathHelper.Abs(obb.Axes[1]) * obb.Extents.Y;
            Vector3 absZ = MathHelper.Abs(obb.Axes[2]) * obb.Extents.Z;

            Vector3 worldHalf = absX + absY + absZ;

            return new AABB
            {
                Min = obb.Center - worldHalf,
                Max = obb.Center + worldHalf
            };
        }

        public static List<OBB> CreateOBBsFromPath(List<Vector3> path, float diameter)
        {
            List<OBB> obbs = new List<OBB>();
            for (int i = 0; i < path.Count - 1; i++)
            {
                AABB startAABB = AABB.FromCenterSize(path[i], new Vector3(diameter, diameter, diameter));
                AABB endAABB = AABB.FromCenterSize(path[i + 1], new Vector3(diameter, diameter, diameter));

                OBB obb = CreateOBBFromAABB(AABB.Union(startAABB, endAABB));
                obbs.Add(obb);
            }
            return obbs;
        }

        private static Vector3 ToUnityPos(float x, float y, float z) =>
        new Vector3(x, z, y) * 0.001f;
    }
}